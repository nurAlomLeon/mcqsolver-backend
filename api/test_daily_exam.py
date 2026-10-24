from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .daily_exam_services import (
    DHAKA_TZ,
    exam_window_for_date,
    ensure_daily_exam,
    leaderboard_payload,
    select_daily_exam_category,
)
from .models import (
    Answer,
    Category,
    DailyExam,
    DailyExamAttempt,
    Question,
)


def at_dhaka(hour, minute=0, day=19):
    return datetime(2026, 10, day, hour, minute, tzinfo=DHAKA_TZ)


class DailyExamTestMixin:
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='learner',
            password='password',
            first_name='Daily',
            last_name='Learner',
        )
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        self.root = Category.objects.create(name='Mathematics', level=0)
        self.part = Category.objects.create(
            name='Algebra',
            parent=self.root,
        )
        self.leaf = Category.objects.create(
            name='Linear Equations',
            parent=self.part,
        )
        self.questions = [
            self._make_question(index)
            for index in range(25)
        ]

        self.now = at_dhaka(21, 0)
        with patch('django.utils.timezone.now', return_value=self.now):
            self.exam, _created = ensure_daily_exam(
                exam_date=date(2026, 10, 19),
                category=self.leaf,
                now=self.now,
                force=True,
            )

    def _make_question(self, index):
        question = Question.objects.create(
            category=self.leaf,
            question_text=f'Question {index}',
            explanation=f'Explanation {index}',
        )
        Answer.objects.create(
            question=question,
            text=f'Correct {index}',
            is_correct=True,
        )
        Answer.objects.create(
            question=question,
            text=f'Wrong {index}',
            is_correct=False,
        )
        return question

    def _submissions(self, all_correct=True):
        submissions = []
        questions = list(
            self.exam.quiz.questions.prefetch_related('answers').all()
        )
        for index, question in enumerate(questions):
            answers = list(question.answers.all())
            correct = next(answer for answer in answers if answer.is_correct)
            wrong = next(answer for answer in answers if not answer.is_correct)
            use_correct = all_correct or index % 2 == 0
            submissions.append({
                'question_id': question.id,
                'selected_answer_id': (
                    correct.id if use_correct else wrong.id
                ),
            })
        return submissions

    def _start(self, now=None):
        now = now or self.now
        with patch('django.utils.timezone.now', return_value=now):
            return self.client.post(
                f'/api/daily-exams/{self.exam.pk}/start/',
                {},
                format='json',
            )

    def _submit(self, attempt_id, submissions=None, now=None):
        now = now or self.now + timedelta(minutes=5)
        with patch('django.utils.timezone.now', return_value=now):
            return self.client.post(
                f'/api/daily-exams/attempts/{attempt_id}/submit/',
                {'submissions': submissions or self._submissions()},
                format='json',
            )


class DailyExamGenerationTests(DailyExamTestMixin, TestCase):
    def test_exam_window_uses_the_dhaka_schedule(self):
        opens_at, closes_at = exam_window_for_date(date(2026, 10, 19))

        self.assertEqual(opens_at.astimezone(DHAKA_TZ).hour, 21)
        self.assertEqual(closes_at.astimezone(DHAKA_TZ).hour, 23)

    def test_selects_a_leaf_subcategory(self):
        with patch('api.daily_exam_services.random.choice', side_effect=lambda rows: rows[0]):
            category = select_daily_exam_category()
        self.assertEqual(category.id, self.leaf.id)

    def test_exam_has_twenty_questions_and_expected_marks(self):
        self.assertEqual(self.exam.quiz.questions.count(), 20)
        self.assertEqual(self.exam.total_marks, 20)
        self.assertEqual(str(self.exam.quiz.correct_mark), '1.00')
        self.assertEqual(self.exam.quiz.duration_minutes, 20)

    def test_second_generation_returns_the_same_exam(self):
        exam, created = ensure_daily_exam(
            exam_date=date(2026, 10, 19),
            now=self.now,
        )
        self.assertFalse(created)
        self.assertEqual(exam.pk, self.exam.pk)
        self.assertEqual(DailyExam.objects.count(), 1)


class DailyExamAttemptTests(DailyExamTestMixin, TestCase):
    def test_today_endpoint_reports_upcoming_and_live_states(self):
        with patch('django.utils.timezone.now', return_value=at_dhaka(20, 30)):
            response = self.client.get('/api/daily-exams/today/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['exam']['status'], 'UPCOMING')
        self.assertFalse(response.data['can_start'])

        with patch('django.utils.timezone.now', return_value=at_dhaka(21, 5)):
            response = self.client.get('/api/daily-exams/today/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['exam']['status'], 'LIVE')
        self.assertTrue(response.data['can_start'])

    def test_start_returns_candidate_safe_questions(self):
        response = self._start()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['attempt_id'], DailyExamAttempt.objects.get().pk)
        self.assertEqual(response.data['quiz_details']['quiz_type'], 'DAILY_EXAM')
        questions = response.data['quiz_details']['questions']
        self.assertEqual(len(questions), 20)
        for question in questions:
            for answer in question['answers']:
                self.assertFalse(answer['is_correct'])

    def test_starting_again_resumes_the_same_attempt(self):
        first = self._start()
        second = self._start(at_dhaka(21, 5))
        self.assertEqual(second.status_code, 201)
        self.assertTrue(second.data['resumed'])
        self.assertEqual(first.data['attempt_id'], second.data['attempt_id'])
        self.assertEqual(DailyExamAttempt.objects.count(), 1)

    def test_one_attempt_after_submission(self):
        start = self._start()
        submit = self._submit(start.data['attempt_id'])
        self.assertEqual(submit.status_code, 200)

        second = self._start(at_dhaka(21, 10))
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data['code'], 'already_attempted')

    def test_can_start_at_2259_but_attempt_is_unranked(self):
        start = self._start(at_dhaka(22, 59))
        self.assertEqual(start.status_code, 201)
        self.assertFalse(start.data['is_ranked'])
        self.assertEqual(start.data['expires_at'], self.exam.closes_at)

    def test_late_start_is_allowed_but_unranked(self):
        start = self._start(at_dhaka(22, 45))
        self.assertEqual(start.status_code, 201)
        self.assertFalse(start.data['is_ranked'])
        self.assertEqual(start.data['ranking_reason'], 'late_start')
        self.assertLessEqual(start.data['expires_at'], self.exam.closes_at)

        submit = self._submit(
            start.data['attempt_id'],
            now=at_dhaka(22, 50),
        )
        self.assertEqual(submit.status_code, 200)
        self.assertFalse(submit.data['is_ranked'])
        self.assertIsNone(submit.data['rank'])

        board = leaderboard_payload(self.exam, current_user_id=self.user.pk)
        self.assertEqual(board['participant_count'], 0)
        self.assertEqual(board['leaderboard'], [])
        self.assertIsNone(board['current_user_entry'])

    def test_guest_can_attempt_but_is_not_ranked(self):
        guest_client = APIClient()
        guest_device = 'daily-exam-guest-device'
        from .models import GuestUser
        guest = GuestUser.objects.create(device_id=guest_device)
        guest_client.credentials(
            HTTP_X_GUEST_TOKEN=str(guest.guest_id),
            HTTP_X_DEVICE_ID=guest.device_id,
        )

        with patch('django.utils.timezone.now', return_value=at_dhaka(21, 5)):
            start = guest_client.post(
                f'/api/daily-exams/{self.exam.pk}/start/',
                {},
                format='json',
            )
        self.assertEqual(start.status_code, 201)
        self.assertFalse(start.data['is_ranked'])
        self.assertEqual(start.data['ranking_reason'], 'guest')

    def test_cannot_start_before_opening(self):
        with patch('django.utils.timezone.now', return_value=at_dhaka(20, 30)):
            response = self.client.post(
                f'/api/daily-exams/{self.exam.pk}/start/',
                {},
                format='json',
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'exam_not_open')

    def test_cannot_start_after_closing(self):
        with patch('django.utils.timezone.now', return_value=at_dhaka(23, 1)):
            response = self.client.post(
                f'/api/daily-exams/{self.exam.pk}/start/',
                {},
                format='json',
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'exam_closed')


class DailyExamSubmissionTests(DailyExamTestMixin, TestCase):
    def test_submission_scores_and_ranks_the_user(self):
        start = self._start()
        response = self._submit(start.data['attempt_id'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['score'], 20)
        self.assertEqual(response.data['correct_answers'], 20)
        self.assertEqual(response.data['wrong_answers'], 0)
        self.assertEqual(response.data['unanswered'], 0)
        self.assertEqual(response.data['rank'], 1)
        self.assertTrue(response.data['is_ranked'])

        attempt = DailyExamAttempt.objects.get(pk=start.data['attempt_id'])
        self.assertEqual(attempt.time_taken_seconds, 300)
        self.assertIsNotNone(attempt.submitted_at)

    def test_answer_key_is_locked_until_the_exam_closes(self):
        start = self._start()
        self._submit(start.data['attempt_id'])

        with patch('django.utils.timezone.now', return_value=at_dhaka(21, 15)):
            locked = self.client.get(
                f'/api/daily-exams/attempts/{start.data["attempt_id"]}/result/'
            )
        self.assertEqual(locked.status_code, 200)
        self.assertFalse(locked.data['answer_key_available'])
        self.assertNotIn('submissions', locked.data)
        self.assertNotIn('questions', locked.data['quiz'])

        with patch('django.utils.timezone.now', return_value=at_dhaka(23, 5)):
            unlocked = self.client.get(
                f'/api/daily-exams/attempts/{start.data["attempt_id"]}/result/'
            )
        self.assertEqual(unlocked.status_code, 200)
        self.assertTrue(unlocked.data['answer_key_available'])
        self.assertEqual(len(unlocked.data['submissions']), 20)
        self.assertTrue(any(
            answer['is_correct']
            for submission in unlocked.data['submissions']
            for answer in submission['question']['answers']
        ))

    def test_history_returns_the_attempt_and_rank(self):
        start = self._start()
        self._submit(start.data['attempt_id'])

        with patch('django.utils.timezone.now', return_value=at_dhaka(23, 5)):
            response = self.client.get('/api/daily-exams/history/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        item = response.data['results'][0]
        self.assertEqual(item['exam']['id'], self.exam.pk)
        self.assertEqual(item['attempt']['score'], 20)
        self.assertEqual(item['attempt']['rank'], 1)
        self.assertEqual(item['participant_count'], 1)
        self.assertTrue(item['answer_key_available'])

    def test_leaderboard_orders_by_score_then_time(self):
        start = self._start()
        self._submit(start.data['attempt_id'])

        other = User.objects.create_user(
            username='second',
            password='password',
            first_name='Second',
        )
        other_token = Token.objects.create(user=other)
        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION=f'Token {other_token.key}')

        with patch('django.utils.timezone.now', return_value=at_dhaka(21, 2)):
            other_start = other_client.post(
                f'/api/daily-exams/{self.exam.pk}/start/',
                {},
                format='json',
            )
            other_submit = other_client.post(
                f'/api/daily-exams/attempts/{other_start.data["attempt_id"]}/submit/',
                {'submissions': self._submissions(all_correct=True)},
                format='json',
            )
        self.assertEqual(other_submit.status_code, 200)

        board = leaderboard_payload(self.exam, current_user_id=self.user.pk)
        self.assertEqual(board['participant_count'], 2)
        # The first learner took 5 minutes, the second only 2, so the second
        # wins the tie on time.
        self.assertEqual(board['leaderboard'][0]['user_id'], other.pk)
        self.assertEqual(board['leaderboard'][1]['user_id'], self.user.pk)
        self.assertEqual(board['current_user_entry']['rank'], 2)

        top_only = leaderboard_payload(
            self.exam,
            current_user_id=self.user.pk,
            limit=1,
        )
        self.assertEqual(len(top_only['leaderboard']), 1)
        self.assertEqual(top_only['current_user_entry']['rank'], 2)

    def test_generic_model_test_endpoints_reject_daily_exam(self):
        start = self._start()
        generic_start = self.client.post(
            f'/api/exam-attempts/{self.exam.quiz_id}/start/',
            {},
            format='json',
        )
        self.assertEqual(generic_start.status_code, 400)
        self.assertEqual(
            generic_start.data['code'],
            'daily_exam_endpoint_required',
        )

        submit = self._submit(start.data['attempt_id'])
        generic_result = self.client.get(
            f'/api/results/{submit.data["id"]}/',
        )
        self.assertEqual(generic_result.status_code, 404)

    def test_daily_exam_quiz_is_hidden_from_the_quiz_catalog(self):
        response = self.client.get('/api/quizzes/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
