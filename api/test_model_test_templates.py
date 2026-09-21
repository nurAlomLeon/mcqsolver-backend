from decimal import Decimal
import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import (
    Answer,
    Category,
    DailyProgress,
    DailyTarget,
    ModelTestTemplate,
    ModelTestTemplateCategory,
    Question,
    Quiz,
    QuizAttempt,
    UserSubmission,
    UserActivity,
    GuestUser,
)


class ModelTestTemplateApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='candidate', password='password')
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        self.root = Category.objects.create(name='General')
        self.child = Category.objects.create(name='Mathematics', parent=self.root)
        self.root_question = self._create_question(self.root, 'Root question')
        self.child_questions = [
            self._create_question(self.child, f'Child question {index}')
            for index in range(3)
        ]
        self.template = self._create_template(
            name='Standard Model Test',
            category=self.child,
            count=3,
        )

    def _create_question(self, category, text):
        question = Question.objects.create(
            category=category,
            question_text=text,
            explanation=f'Explanation for {text}',
        )
        question.correct_answer = Answer.objects.create(
            question=question,
            text='Correct',
            is_correct=True,
        )
        question.wrong_answer = Answer.objects.create(
            question=question,
            text='Wrong',
            is_correct=False,
        )
        return question

    def _create_template(self, name, category, count, **kwargs):
        duration_minutes = kwargs.pop('duration_minutes', 30)
        include_subcategories = kwargs.pop('include_subcategories', True)
        template = ModelTestTemplate.objects.create(
            name=name,
            duration_minutes=duration_minutes,
            **kwargs,
        )
        ModelTestTemplateCategory.objects.create(
            template=template,
            category=category,
            question_count=count,
            include_subcategories=include_subcategories,
        )
        return template

    def _start_template(self, template=None):
        template = template or self.template
        return self.client.post(
            f'/api/model-test-templates/{template.id}/start/',
            {},
            format='json',
        )

    def test_active_list_is_ordered_and_excludes_inactive_templates(self):
        self.template.display_order = 2
        self.template.save(update_fields=['display_order'])
        first = self._create_template(
            name='First',
            category=self.root,
            count=1,
            display_order=1,
            include_subcategories=False,
        )
        self._create_template(
            name='Inactive',
            category=self.root,
            count=1,
            is_active=False,
            include_subcategories=False,
        )

        response = self.client.get('/api/model-test-templates/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], [first.id, self.template.id])
        self.assertNotIn('Inactive', [item['name'] for item in response.data])
        self.assertEqual(response.data[0]['total_questions'], 1)
        configuration = response.data[0]['category_configurations'][0]
        self.assertEqual(configuration['category']['id'], self.root.id)
        self.assertEqual(
            configuration['category']['full_path'],
            self.root.get_full_path(),
        )
        self.assertEqual(configuration['question_count'], 1)

    def test_template_generation_uses_exact_quota_and_all_descendants(self):
        descendant_template = self._create_template(
            name='All General Questions',
            category=self.root,
            count=4,
            include_subcategories=True,
        )

        response = self._start_template(descendant_template)

        self.assertEqual(response.status_code, 201)
        attempt = QuizAttempt.objects.get(pk=response.data['attempt_id'])
        self.assertEqual(attempt.quiz.questions.count(), 4)
        self.assertSetEqual(
            set(attempt.quiz.questions.values_list('id', flat=True)),
            {self.root_question.id, *(question.id for question in self.child_questions)},
        )
        self.assertEqual(attempt.quiz.source_template, descendant_template)
        self.assertEqual(attempt.quiz.generated_for_user, self.user)
        self.assertIn('started_at', response.data)
        self.assertIn('expires_at', response.data)

    def test_shortage_rolls_back_quiz_and_attempt(self):
        shortage = self._create_template(
            name='Too Many Questions',
            category=self.child,
            count=4,
            include_subcategories=False,
        )
        quiz_count = Quiz.objects.count()
        attempt_count = QuizAttempt.objects.count()

        response = self._start_template(shortage)

        self.assertEqual(response.status_code, 400)
        self.assertIn('only 3 unique questions are available', str(response.data))
        self.assertEqual(Quiz.objects.count(), quiz_count)
        self.assertEqual(QuizAttempt.objects.count(), attempt_count)

    def test_candidate_start_hides_correctness_and_explanations(self):
        response = self._start_template()

        self.assertEqual(response.status_code, 201)
        question = response.data['quiz_details']['questions'][0]
        self.assertIsNone(question['explanation'])
        self.assertIsNone(question['explanation_image'])
        self.assertIs(question['answers'][0]['is_correct'], False)

        result = self.client.get(
            f"/api/results/{response.data['attempt_id']}/",
        )
        self.assertEqual(result.status_code, 404)

    def test_decimal_scoring_counts_unanswered_separately(self):
        start_response = self._start_template()
        attempt = QuizAttempt.objects.select_related('quiz').get(
            pk=start_response.data['attempt_id'],
        )
        questions = list(attempt.quiz.questions.order_by('id'))
        payload = {
            'submissions': [
                {
                    'question_id': questions[0].id,
                    'selected_answer_id': questions[0].answers.get(is_correct=True).id,
                },
                {
                    'question_id': questions[1].id,
                    'selected_answer_id': questions[1].answers.get(is_correct=False).id,
                },
                {
                    'question_id': questions[2].id,
                    'selected_answer_id': None,
                },
            ],
        }

        response = self.client.post(
            f'/api/exam-attempts/{attempt.id}/submit-bulk/',
            payload,
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['score'], 0.75)
        self.assertEqual(response.data['correct_answers'], 1)
        self.assertEqual(response.data['wrong_answers'], 1)
        self.assertEqual(response.data['unanswered'], 1)
        self.assertEqual(response.data['attempted_string'], '2/3')
        self.assertEqual(response.data['maximum_score'], 3)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, Decimal('0.75'))
        self.assertEqual(UserSubmission.objects.filter(attempt=attempt).count(), 3)
        self.assertEqual(
            UserSubmission.objects.filter(
                attempt=attempt,
                selected_answer__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(
            UserActivity.objects.filter(
                user=self.user,
                activity_type=UserActivity.ActivityType.QUESTION_ANSWERED,
            ).count(),
            2,
        )
        self.assertEqual(
            UserActivity.objects.filter(
                user=self.user,
                activity_type=UserActivity.ActivityType.MODEL_TEST_COMPLETED,
            ).count(),
            1,
        )
        question_progress = DailyProgress.objects.get(
            user=self.user,
            target_type=DailyTarget.TargetType.QUESTIONS_SOLVED,
        )
        self.assertEqual(question_progress.current_value, 2)

        result = self.client.get(f'/api/results/{attempt.id}/')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['correct_answers'], 1)
        result_question = result.data['submissions'][0]['question']
        self.assertIn('explanation', result_question)
        self.assertIn('is_correct', result_question['answers'][0])

    def test_generated_quiz_cannot_be_started_by_another_user(self):
        response = self._start_template()
        quiz_id = response.data['quiz_details']['id']
        other_user = User.objects.create_user(username='other', password='password')
        other_token = Token.objects.create(user=other_user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {other_token.key}')

        forbidden = self.client.post(
            f'/api/exam-attempts/{quiz_id}/start/',
            {},
            format='json',
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(QuizAttempt.objects.filter(user=other_user).count(), 0)

    def test_custom_test_keeps_default_scoring_and_exact_count(self):
        response = self.client.post(
            '/api/create-model-test/',
            {
                'name': 'Custom Test',
                'duration_minutes': 10,
                'config': [{
                    'category_id': self.root.id,
                    'count': 1,
                    'include_subcategories': False,
                }],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        quiz = Quiz.objects.get(pk=response.data['id'])
        self.assertEqual(quiz.questions.count(), 1)
        self.assertEqual(quiz.correct_mark, Decimal('1.00'))
        self.assertEqual(quiz.wrong_mark, Decimal('0.00'))
        self.assertEqual(quiz.unanswered_mark, Decimal('0.00'))
        self.assertEqual(quiz.generated_for_user, self.user)
        self.assertIsNone(response.data['questions'][0]['explanation'])
        self.assertIs(response.data['questions'][0]['answers'][0]['is_correct'], False)

        start = self.client.post(
            f'/api/exam-attempts/{quiz.id}/start/',
            {},
            format='json',
        )
        self.assertEqual(start.status_code, 200)
        self.assertIn('started_at', start.data)
        self.assertIn('expires_at', start.data)

    def test_custom_test_keeps_legacy_shortage_behavior(self):
        response = self.client.post(
            '/api/create-model-test/',
            {
                'name': 'Short Custom Test',
                'duration_minutes': 10,
                'config': [{
                    'category_id': self.child.id,
                    'count': 99,
                    'include_subcategories': False,
                }],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        quiz = Quiz.objects.get(pk=response.data['id'])
        self.assertEqual(quiz.questions.count(), 3)

    def test_custom_test_keeps_legacy_overlap_behavior(self):
        response = self.client.post(
            '/api/create-model-test/',
            {
                'name': 'Overlap Custom Test',
                'duration_minutes': 10,
                'config': [
                    {
                        'category_id': self.root.id,
                        'count': 99,
                        'include_subcategories': True,
                    },
                    {
                        'category_id': self.child.id,
                        'count': 99,
                        'include_subcategories': False,
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        quiz = Quiz.objects.get(pk=response.data['id'])
        self.assertEqual(quiz.questions.count(), 4)

    def test_bulk_submission_rejects_answers_from_other_questions(self):
        start_response = self._start_template()
        attempt = QuizAttempt.objects.get(pk=start_response.data['attempt_id'])
        questions = list(attempt.quiz.questions.order_by('id'))
        submissions = [
            {'question_id': question.id, 'selected_answer_id': None}
            for question in questions
        ]
        submissions[0]['selected_answer_id'] = questions[1].answers.first().id

        response = self.client.post(
            f'/api/exam-attempts/{attempt.id}/submit-bulk/',
            {'submissions': submissions},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(QuizAttempt.objects.get(pk=attempt.id).is_completed)
        self.assertFalse(UserSubmission.objects.filter(attempt=attempt).exists())

    def test_v2_bulk_submission_returns_compact_result_and_is_idempotent(self):
        start_response = self._start_template()
        attempt = QuizAttempt.objects.select_related('quiz').get(
            pk=start_response.data['attempt_id'],
        )
        questions = list(attempt.quiz.questions.order_by('id'))
        payload = {
            'submissions': [
                {
                    'question_id': questions[0].id,
                    'selected_answer_id': questions[0].answers.get(is_correct=True).id,
                },
                {
                    'question_id': questions[1].id,
                    'selected_answer_id': questions[1].answers.get(is_correct=False).id,
                },
                {
                    'question_id': questions[2].id,
                    'selected_answer_id': None,
                },
            ],
        }

        response = self.client.post(
            f'/api/v2/exam-attempts/{attempt.id}/submit/',
            payload,
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['score'], 0.75)
        self.assertNotIn('questions', response.data['quiz'])
        self.assertEqual(len(response.data['submissions']), 3)
        self.assertEqual(
            UserActivity.objects.filter(
                user=self.user,
                activity_type=UserActivity.ActivityType.QUESTION_ANSWERED,
            ).count(),
            2,
        )
        self.assertEqual(
            UserActivity.objects.filter(
                user=self.user,
                activity_type=UserActivity.ActivityType.MODEL_TEST_COMPLETED,
            ).count(),
            1,
        )

        retry = self.client.post(
            f'/api/v2/exam-attempts/{attempt.id}/submit/',
            payload,
            format='json',
        )

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data['score'], 0.75)
        self.assertEqual(
            UserActivity.objects.filter(
                user=self.user,
                activity_type=UserActivity.ActivityType.QUESTION_ANSWERED,
            ).count(),
            2,
        )
        self.assertEqual(
            UserActivity.objects.filter(
                user=self.user,
                activity_type=UserActivity.ActivityType.MODEL_TEST_COMPLETED,
            ).count(),
            1,
        )

    def test_v2_bulk_submission_rejects_conflicting_retry(self):
        start_response = self._start_template()
        attempt = QuizAttempt.objects.select_related('quiz').get(
            pk=start_response.data['attempt_id'],
        )
        questions = list(attempt.quiz.questions.order_by('id'))
        payload = {
            'submissions': [
                {
                    'question_id': question.id,
                    'selected_answer_id': question.answers.get(is_correct=True).id,
                }
                for question in questions
            ],
        }

        response = self.client.post(
            f'/api/v2/exam-attempts/{attempt.id}/submit/',
            payload,
            format='json',
        )
        self.assertEqual(response.status_code, 200)

        conflicting_payload = {
            'submissions': [
                {
                    'question_id': questions[0].id,
                    'selected_answer_id': questions[0].answers.get(is_correct=False).id,
                },
                *payload['submissions'][1:],
            ],
        }

        conflicting = self.client.post(
            f'/api/v2/exam-attempts/{attempt.id}/submit/',
            conflicting_payload,
            format='json',
        )

        self.assertEqual(conflicting.status_code, 409)
        self.assertIn('already been submitted', conflicting.data['error'])

    def test_v3_submit_summary_returns_fast_summary_without_submissions(self):
        start_response = self._start_template()
        attempt = QuizAttempt.objects.select_related('quiz').get(
            pk=start_response.data['attempt_id'],
        )
        questions = list(attempt.quiz.questions.order_by('id'))
        payload = {
            'submissions': [
                {
                    'question_id': questions[0].id,
                    'selected_answer_id': questions[0].answers.get(is_correct=True).id,
                },
                {
                    'question_id': questions[1].id,
                    'selected_answer_id': questions[1].answers.get(is_correct=False).id,
                },
                {
                    'question_id': questions[2].id,
                    'selected_answer_id': None,
                },
            ],
        }

        response = self.client.post(
            f'/api/v3/exam-attempts/{attempt.id}/submit-summary/',
            payload,
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['score'], 0.75)
        self.assertNotIn('questions', response.data['quiz'])
        self.assertNotIn('submissions', response.data)
        self.assertEqual(response.data['attempted_string'], '2/3')

        retry = self.client.post(
            f'/api/v3/exam-attempts/{attempt.id}/submit-summary/',
            payload,
            format='json',
        )

        self.assertEqual(retry.status_code, 200)
        self.assertNotIn('submissions', retry.data)

    def test_guest_template_start_requires_matching_guest_credentials(self):
        guest_id = uuid.uuid4()
        GuestUser.objects.create(guest_id=guest_id, device_id='guest-device')
        self.client.credentials(
            HTTP_X_GUEST_TOKEN=str(guest_id),
            HTTP_X_DEVICE_ID='guest-device',
        )

        response = self._start_template()

        self.assertEqual(response.status_code, 201)
        attempt = QuizAttempt.objects.get(pk=response.data['attempt_id'])
        self.assertEqual(attempt.guest_user.device_id, 'guest-device')
        self.assertEqual(attempt.quiz.generated_for_guest, attempt.guest_user)

        self.client.credentials(
            HTTP_X_GUEST_TOKEN=str(uuid.uuid4()),
            HTTP_X_DEVICE_ID='guest-device',
        )
        rejected = self.client.get('/api/model-test-templates/')
        self.assertIn(rejected.status_code, {401, 403})
