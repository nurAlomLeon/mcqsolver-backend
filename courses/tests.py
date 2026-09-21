from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .models import (
    Course,
    CourseEnrollment,
    CourseQuestion,
    CourseQuiz,
    CourseQuizAttempt,
    CourseQuizSubmission,
)
from .services import auto_reorganize_course_quizzes, reorder_course_quizzes


class CoursesApiTestCase(TestCase):
    """Shared users, auth helpers and fixtures for the courses API tests."""

    def setUp(self):
        self.client = APIClient()
        self.staff_user = User.objects.create_user(
            username='staff',
            password='password',
            is_staff=True,
        )
        self.learner = User.objects.create_user(
            username='learner',
            password='password',
        )
        self.staff_token = Token.objects.create(user=self.staff_user)
        self.learner_token = Token.objects.create(user=self.learner)

    def authenticate_staff(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.staff_token.key}')

    def authenticate_learner(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.learner_token.key}')

    def create_quiz(self, course, position=1, is_active=True):
        quiz = CourseQuiz.objects.create(
            course=course,
            name=f'Quiz {position}',
            position=position,
            duration_minutes=20,
            correct_mark='1.00',
            is_active=is_active,
        )
        for question_position in range(1, 3):
            question = CourseQuestion.objects.create(
                quiz=quiz,
                position=question_position,
                question_text=f'Question {position}.{question_position}',
                fingerprint=f'{course.id}-{position}-{question_position}',
            )
            question.answers.create(position=1, text='Correct', is_correct=True)
            question.answers.create(position=2, text='Wrong', is_correct=False)
        return quiz

    def create_completed_attempt(self, user, quiz, score, duration_seconds=60, outcomes=()):
        now = timezone.now()
        attempt = CourseQuizAttempt.objects.create(
            user=user,
            quiz=quiz,
            expires_at=now + timedelta(minutes=20),
            end_time=now,
            score=score,
            is_completed=True,
            total_questions=2,
            correct_mark='1.00',
            wrong_mark='0.00',
            unanswered_mark='0.00',
        )
        CourseQuizAttempt.objects.filter(pk=attempt.pk).update(
            start_time=now - timedelta(seconds=duration_seconds),
            end_time=now,
        )
        attempt.refresh_from_db()
        for question, is_correct in zip(quiz.questions.all(), outcomes):
            answer = question.answers.get(is_correct=is_correct)
            CourseQuizSubmission.objects.create(
                attempt=attempt,
                question=question,
                selected_answer=answer,
                is_correct=is_correct,
            )
        return attempt


class CoursesApiTests(CoursesApiTestCase):
    def test_staff_can_create_archive_course_and_nested_quiz(self):
        self.authenticate_staff()

        course_response = self.client.post(
            '/api/courses/',
            {
                'name': 'Archive Course',
                'description': 'Archived content',
                'course_type': Course.CourseType.ARCHIVE,
                'is_published': True,
                'allow_self_enrollment': True,
            },
            format='json',
        )

        self.assertEqual(course_response.status_code, 201)
        course_id = course_response.data['id']

        course_list_response = self.client.get('/api/courses/')
        self.assertEqual(course_list_response.status_code, 200)
        self.assertFalse(course_list_response.data['results'][0]['is_enrolled'])

        quiz_response = self.client.post(
            f'/api/courses/{course_id}/quizzes/',
            {
                'name': 'Starter Quiz',
                'topic': 'Main topic section',
                'quiz_type': CourseQuiz.QuizType.PRACTICE,
                'position': 1,
                'duration_minutes': 15,
                'correct_mark': '1.00',
                'wrong_mark': '-0.25',
                'unanswered_mark': '0.00',
                'is_active': True,
                'questions': [
                    {
                        'question_text': '2 + 2 = ?',
                        'answers': [
                            {'text': '3', 'is_correct': False},
                            {'text': '4', 'is_correct': True},
                        ],
                    },
                ],
            },
            format='json',
        )

        self.assertEqual(quiz_response.status_code, 201)
        self.assertEqual(quiz_response.data['question_count'], 1)
        self.assertEqual(quiz_response.data['correct_mark'], 1)
        self.assertEqual(quiz_response.data['wrong_mark'], -0.25)
        self.assertEqual(quiz_response.data['unanswered_mark'], 0)
        self.assertEqual(len(quiz_response.data['questions']), 1)
        self.assertEqual(quiz_response.data['topic'], 'Main topic section')
        self.assertTrue(quiz_response.data['is_unlocked'])
        self.assertEqual(quiz_response.data['attempt_status'], 'NOT_STARTED')
        self.assertEqual(quiz_response.data['completed_attempt_count'], 0)

        update_response = self.client.patch(
            f'/api/courses/{course_id}/quizzes/{quiz_response.data["id"]}/',
            {'topic': 'Updated topic'},
            format='json',
        )

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data['topic'], 'Updated topic')

    def test_live_course_requires_enrollment_and_unlock_before_attempt(self):
        course = Course.objects.create(
            name='Live Course',
            description='Live',
            course_type=Course.CourseType.LIVE,
            is_published=True,
            allow_self_enrollment=True,
            created_by=self.staff_user,
        )
        quiz = CourseQuiz.objects.create(
            course=course,
            name='Locked Quiz',
            position=1,
            duration_minutes=20,
            unlock_at=timezone.now() + timedelta(hours=1),
        )
        question = CourseQuestion.objects.create(
            quiz=quiz,
            position=1,
            question_text='Capital of France?',
            explanation='Paris is the capital of France.',
            fingerprint='fp1',
        )
        question.answers.create(position=1, text='Paris', is_correct=True)
        question.answers.create(position=2, text='Rome', is_correct=False)

        self.authenticate_learner()

        not_enrolled_response = self.client.post(
            f'/api/courses/{course.id}/quizzes/{quiz.id}/attempts/start/',
            {},
            format='json',
        )
        self.assertEqual(not_enrolled_response.status_code, 403)

        enroll_response = self.client.post(f'/api/courses/{course.id}/enroll/', {}, format='json')
        self.assertEqual(enroll_response.status_code, 201)

        course_detail_response = self.client.get(f'/api/courses/{course.id}/')
        self.assertEqual(course_detail_response.status_code, 200)
        self.assertEqual(course_detail_response.data['quizzes'][0]['is_unlocked'], False)

        locked_response = self.client.post(
            f'/api/courses/{course.id}/quizzes/{quiz.id}/attempts/start/',
            {},
            format='json',
        )
        self.assertEqual(locked_response.status_code, 403)

        quiz.unlock_at = timezone.now() - timedelta(minutes=1)
        quiz.save(update_fields=['unlock_at'])

        unlocked_response = self.client.post(
            f'/api/courses/{course.id}/quizzes/{quiz.id}/attempts/start/',
            {},
            format='json',
        )
        self.assertEqual(unlocked_response.status_code, 200)
        self.assertEqual(unlocked_response.data['quiz']['questions'][0]['question_text'], 'Capital of France?')
        self.assertNotIn('is_correct', unlocked_response.data['quiz']['questions'][0]['answers'][0])

    def test_staff_answersheet_import_creates_quiz_and_skips_duplicates(self):
        course = Course.objects.create(
            name='Import Course',
            description='Importable',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            allow_self_enrollment=False,
            created_by=self.staff_user,
        )
        self.authenticate_staff()

        payload = {
            'quiz_name': 'Imported Quiz',
            'topic': 'পার্ট-১) Topic text before the source lines',
            'quiz_type': CourseQuiz.QuizType.QUESTION_BANK,
            'duration_minutes': 2,
            'source_name': 'bcstarget',
            'external_quiz_id': 'quiz-1',
            'questions': [
                {
                    'source_question_id': 'q1',
                    'question_text': 'First imported question?',
                    'option_a': 'A1',
                    'option_b': 'B1',
                    'option_c': 'C1',
                    'option_d': 'D1',
                    'correct_option': 'A',
                    'explanation': 'Because A1 is correct.',
                },
                {
                    'source_question_id': 'q2',
                    'question_text': 'Second imported question?',
                    'option_a': 'A2',
                    'option_b': 'B2',
                    'option_c': 'C2',
                    'option_d': 'D2',
                    'correct_option': 'D',
                    'explanation': 'Because D2 is correct.',
                },
            ],
        }

        first_response = self.client.post(
            f'/api/courses/{course.id}/quizzes/import/',
            payload,
            format='json',
        )
        second_response = self.client.post(
            f'/api/courses/{course.id}/quizzes/import/',
            payload,
            format='json',
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.data['created'], 2)
        self.assertTrue(first_response.data['quiz_created'])
        self.assertEqual(first_response.data['quiz']['topic'], payload['topic'])
        self.assertEqual(first_response.data['quiz']['attempt_status'], 'NOT_STARTED')
        self.assertEqual(first_response.data['quiz']['completed_attempt_count'], 0)

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.data['created'], 0)
        self.assertEqual(second_response.data['skipped'], 2)
        self.assertFalse(second_response.data['quiz_created'])
        imported_quiz = CourseQuiz.objects.get(course=course, name='Imported Quiz')
        self.assertEqual(imported_quiz.questions.count(), 2)
        self.assertEqual(imported_quiz.topic, payload['topic'])

    def test_staff_csv_import_adds_questions_to_existing_quiz(self):
        course = Course.objects.create(
            name='CSV Course',
            description='CSV',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        quiz = CourseQuiz.objects.create(
            course=course,
            name='CSV Quiz',
            position=1,
            duration_minutes=10,
        )
        self.authenticate_staff()

        csv_content = (
            'Item Type,Question Title,Answer Text,Answer Correct/InCorrect,Question Answer Info\n'
            'question,CSV Question 1,,,Explanation 1\n'
            'answer,,Wrong,0,\n'
            'answer,,Right,1,\n'
        )
        upload = SimpleUploadedFile('questions.csv', csv_content.encode('utf-8'), content_type='text/csv')

        response = self.client.post(
            f'/api/courses/{course.id}/quizzes/{quiz.id}/questions/import/',
            {'file': upload},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['created'], 1)
        self.assertEqual(quiz.questions.count(), 1)
        self.assertEqual(quiz.questions.get().answers.count(), 2)

    def test_staff_can_enroll_user_by_email(self):
        course = Course.objects.create(
            name='Email Enrollment Course',
            description='Email enrollment',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        self.learner.email = 'learner@example.com'
        self.learner.save(update_fields=['email'])
        self.authenticate_staff()

        response = self.client.post(
            f'/api/courses/{course.id}/enrollments/',
            {'email': 'Learner@Example.com', 'is_active': True},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        enrollment = CourseEnrollment.objects.get(course=course, user=self.learner)
        self.assertTrue(enrollment.is_active)
        self.assertEqual(enrollment.enrolled_by, self.staff_user)

    def test_staff_email_enrollment_reactivates_existing_enrollment(self):
        course = Course.objects.create(
            name='Reactivation Course',
            description='Reactivate enrollment',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        self.learner.email = 'learner@example.com'
        self.learner.save(update_fields=['email'])
        CourseEnrollment.objects.create(
            course=course,
            user=self.learner,
            is_active=False,
            source=CourseEnrollment.EnrollmentSource.STAFF,
            enrolled_by=self.staff_user,
        )
        self.authenticate_staff()

        response = self.client.post(
            f'/api/courses/{course.id}/enrollments/',
            {'email': 'learner@example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        enrollment = CourseEnrollment.objects.get(course=course, user=self.learner)
        self.assertTrue(enrollment.is_active)

    def test_staff_email_enrollment_rejects_unknown_email(self):
        course = Course.objects.create(
            name='Unknown Email Course',
            description='Unknown email',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        self.authenticate_staff()

        response = self.client.post(
            f'/api/courses/{course.id}/enrollments/',
            {'email': 'missing@example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['email'][0], 'No user found with this email.')

    def test_staff_email_enrollment_rejects_duplicate_email(self):
        course = Course.objects.create(
            name='Duplicate Email Course',
            description='Duplicate email',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        self.learner.email = 'shared@example.com'
        self.learner.save(update_fields=['email'])
        User.objects.create_user(
            username='duplicate-email-user',
            password='password',
            email='shared@example.com',
        )
        self.authenticate_staff()

        response = self.client.post(
            f'/api/courses/{course.id}/enrollments/',
            {'email': 'shared@example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['email'][0],
            'Multiple users found with this email. Use user_id instead.',
        )

    def test_staff_email_enrollment_requires_exactly_one_identifier(self):
        course = Course.objects.create(
            name='Identifier Validation Course',
            description='Identifier validation',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        self.learner.email = 'learner@example.com'
        self.learner.save(update_fields=['email'])
        self.authenticate_staff()

        response = self.client.post(
            f'/api/courses/{course.id}/enrollments/',
            {'user_id': self.learner.id, 'email': 'learner@example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['non_field_errors'][0], 'Provide exactly one of user_id or email.')

    def test_enrolled_user_cannot_view_result_until_after_submission(self):
        course = Course.objects.create(
            name='Attempt Course',
            description='Attempts',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            allow_self_enrollment=True,
            created_by=self.staff_user,
        )
        quiz = CourseQuiz.objects.create(
            course=course,
            name='Attempt Quiz',
            position=1,
            duration_minutes=20,
            correct_mark='1.00',
            wrong_mark='-0.25',
            unanswered_mark='0.00',
        )
        q1 = CourseQuestion.objects.create(
            quiz=quiz,
            position=1,
            question_text='Sky color?',
            explanation='Usually blue.',
            fingerprint='attempt-q1',
        )
        a11 = q1.answers.create(position=1, text='Blue', is_correct=True)
        q1.answers.create(position=2, text='Green', is_correct=False)
        q2 = CourseQuestion.objects.create(
            quiz=quiz,
            position=2,
            question_text='Grass color?',
            explanation='Usually green.',
            fingerprint='attempt-q2',
        )
        q2.answers.create(position=1, text='Blue', is_correct=False)
        a22 = q2.answers.create(position=2, text='Green', is_correct=True)

        CourseEnrollment.objects.create(course=course, user=self.learner, is_active=True)
        self.authenticate_learner()

        start_response = self.client.post(
            f'/api/courses/{course.id}/quizzes/{quiz.id}/attempts/start/',
            {},
            format='json',
        )
        self.assertEqual(start_response.status_code, 200)
        attempt_id = start_response.data['id']
        self.assertEqual(start_response.data['quiz']['question_count'], 2)
        self.assertEqual(start_response.data['quiz']['correct_mark'], 1)
        self.assertEqual(start_response.data['quiz']['wrong_mark'], -0.25)
        self.assertEqual(start_response.data['quiz']['unanswered_mark'], 0)
        self.assertEqual(len(start_response.data['quiz']['questions']), 2)
        self.assertNotIn('is_correct', start_response.data['quiz']['questions'][0]['answers'][0])

        incomplete_result_response = self.client.get(
            f'/api/courses/attempts/{attempt_id}/result/'
        )
        self.assertEqual(incomplete_result_response.status_code, 404)

        submit_response = self.client.post(
            f'/api/courses/attempts/{attempt_id}/submit/',
            {
                'submissions': [
                    {'question_id': q1.id, 'selected_answer_id': a11.id},
                    {'question_id': q2.id, 'selected_answer_id': a22.id},
                ],
            },
            format='json',
        )

        self.assertEqual(submit_response.status_code, 200)
        self.assertEqual(submit_response.data['score'], 2)
        self.assertEqual(submit_response.data['correct_mark'], 1)
        self.assertEqual(submit_response.data['wrong_mark'], -0.25)
        self.assertEqual(submit_response.data['unanswered_mark'], 0)
        self.assertEqual(submit_response.data['correct_answers'], 2)
        self.assertEqual(submit_response.data['unanswered'], 0)
        self.assertIn('is_correct', submit_response.data['submissions'][0]['question']['answers'][0])

        result_response = self.client.get(f'/api/courses/attempts/{attempt_id}/result/')
        self.assertEqual(result_response.status_code, 200)
        self.assertEqual(result_response.data['correct_answers'], 2)
        self.assertEqual(result_response.data['attempted_string'], '2/2')
        self.assertIn('is_correct', result_response.data['submissions'][0]['question']['answers'][0])

    def test_quiz_attempt_status_progresses_and_in_progress_retake_has_priority(self):
        course = Course.objects.create(
            name='Status Course',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        quiz = self.create_quiz(course)
        CourseEnrollment.objects.create(course=course, user=self.learner, is_active=True)
        self.authenticate_learner()

        detail = self.client.get(f'/api/courses/{course.id}/')
        summary = detail.data['quizzes'][0]
        self.assertEqual(summary['attempt_status'], 'NOT_STARTED')
        self.assertEqual(summary['completed_attempt_count'], 0)

        first_start = self.client.post(
            f'/api/courses/{course.id}/quizzes/{quiz.id}/attempts/start/',
            {},
            format='json',
        )
        self.assertEqual(first_start.data['quiz']['attempt_status'], 'IN_PROGRESS')
        self.assertEqual(first_start.data['quiz']['completed_attempt_count'], 0)

        submissions = [
            {
                'question_id': question.id,
                'selected_answer_id': question.answers.get(is_correct=True).id,
            }
            for question in quiz.questions.all()
        ]
        submitted = self.client.post(
            f'/api/courses/attempts/{first_start.data["id"]}/submit/',
            {'submissions': submissions},
            format='json',
        )
        self.assertEqual(submitted.data['quiz']['attempt_status'], 'COMPLETED')
        self.assertEqual(submitted.data['quiz']['completed_attempt_count'], 1)

        second_start = self.client.post(
            f'/api/courses/{course.id}/quizzes/{quiz.id}/attempts/start/',
            {},
            format='json',
        )
        self.assertNotEqual(second_start.data['id'], first_start.data['id'])
        self.assertEqual(second_start.data['quiz']['attempt_status'], 'IN_PROGRESS')
        self.assertEqual(second_start.data['quiz']['completed_attempt_count'], 1)
        self.assertEqual(CourseQuizAttempt.objects.filter(user=self.learner, quiz=quiz).count(), 2)

        quiz_list = self.client.get(f'/api/courses/{course.id}/quizzes/')
        self.assertEqual(quiz_list.data['results'][0]['attempt_status'], 'IN_PROGRESS')
        self.assertEqual(quiz_list.data['results'][0]['completed_attempt_count'], 1)

        course_list = self.client.get('/api/courses/')
        nested_summary = course_list.data['results'][0]['quizzes'][0]
        self.assertEqual(nested_summary['attempt_status'], 'IN_PROGRESS')
        self.assertEqual(nested_summary['completed_attempt_count'], 1)

    def test_course_performance_uses_best_attempt_and_unattempted_exam_zero(self):
        course = Course.objects.create(
            name='Performance Course',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        quiz_one = self.create_quiz(course, position=1)
        quiz_two = self.create_quiz(course, position=2)
        inactive_quiz = self.create_quiz(course, position=3, is_active=False)
        competitor = User.objects.create(username='competitor', first_name='Higher', last_name='Rank')
        timed_out_user = User.objects.create(username='timed-out')
        inactive_user = User.objects.create(username='inactive-enrollment')

        self.authenticate_learner()
        denied = self.client.get(f'/api/courses/{course.id}/performance/')
        self.assertEqual(denied.status_code, 403)

        for user in [self.learner, competitor, timed_out_user]:
            CourseEnrollment.objects.create(course=course, user=user, is_active=True)
        CourseEnrollment.objects.create(course=course, user=inactive_user, is_active=False)

        self.create_completed_attempt(self.learner, quiz_one, '1.00', 5, [True, False])
        self.create_completed_attempt(self.learner, quiz_one, '2.00', 20, [True, True])
        CourseQuizAttempt.objects.create(
            user=self.learner,
            quiz=quiz_two,
            expires_at=timezone.now() + timedelta(minutes=20),
            total_questions=2,
            correct_mark='1.00',
            wrong_mark='0.00',
            unanswered_mark='0.00',
        )
        self.create_completed_attempt(self.learner, inactive_quiz, '2.00', outcomes=[True, True])
        self.create_completed_attempt(competitor, quiz_one, '1.00', outcomes=[True, False])
        self.create_completed_attempt(competitor, quiz_two, '1.00', outcomes=[True, False])
        self.create_completed_attempt(timed_out_user, quiz_one, '0.00')
        self.create_completed_attempt(inactive_user, quiz_one, '2.00', outcomes=[True, True])

        other_course = Course.objects.create(
            name='Other Course',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        other_quiz = self.create_quiz(other_course)
        self.create_completed_attempt(self.learner, other_quiz, '2.00', outcomes=[True, True])

        response = self.client.get(f'/api/courses/{course.id}/performance/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data),
            {'summary', 'leaderboard', 'current_user_entry'},
        )
        self.assertEqual(response.data['summary'], {
            'rank': 2,
            'participant_count': 3,
            'course_score': 50.0,
            'exams_completed': 1,
            'total_exams': 2,
            'attempt_count': 3,
        })
        self.assertEqual(response.data['leaderboard'][0]['user_id'], competitor.id)
        self.assertEqual(response.data['leaderboard'][0]['rank'], 1)
        self.assertEqual(response.data['leaderboard'][0]['user_display_name'], 'Higher Rank')
        self.assertEqual(response.data['leaderboard'][0]['exams_completed'], 2)
        self.assertEqual(set(response.data['leaderboard'][0]), {
            'rank',
            'user_id',
            'user_display_name',
            'course_score',
            'exams_completed',
            'total_exams',
            'accuracy',
            'is_current_user',
        })
        self.assertEqual(response.data['leaderboard'][1]['user_id'], self.learner.id)
        self.assertEqual(response.data['leaderboard'][1]['accuracy'], 100.0)
        self.assertEqual(response.data['current_user_entry'], response.data['leaderboard'][1])
        self.assertEqual(response.data['leaderboard'][2]['user_id'], timed_out_user.id)

        self.authenticate_staff()
        staff_response = self.client.get(f'/api/courses/{course.id}/performance/')
        self.assertEqual(staff_response.status_code, 200)
        self.assertIsNone(staff_response.data['summary']['rank'])
        self.assertEqual(staff_response.data['summary']['participant_count'], 3)
        self.assertIsNone(staff_response.data['current_user_entry'])

    def test_course_performance_limits_leaderboard_and_returns_ranked_current_user(self):
        course = Course.objects.create(
            name='Top Ten Course',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        quiz = self.create_quiz(course)
        CourseEnrollment.objects.create(course=course, user=self.learner, is_active=True)
        self.create_completed_attempt(self.learner, quiz, '0.00')

        higher_users = []
        for index in range(11):
            participant = User.objects.create(username=f'participant-{index:02d}')
            higher_users.append(participant)
            CourseEnrollment.objects.create(course=course, user=participant, is_active=True)
            self.create_completed_attempt(participant, quiz, '2.00', outcomes=[True, True])

        self.authenticate_learner()
        response = self.client.get(f'/api/courses/{course.id}/performance/')

        self.assertEqual(len(response.data['leaderboard']), 10)
        self.assertEqual(
            [entry['rank'] for entry in response.data['leaderboard']],
            list(range(1, 11)),
        )
        self.assertNotIn(
            self.learner.id,
            [entry['user_id'] for entry in response.data['leaderboard']],
        )
        self.assertEqual(response.data['summary']['rank'], 12)
        self.assertEqual(response.data['current_user_entry']['rank'], 12)
        self.assertTrue(response.data['current_user_entry']['is_current_user'])


class CourseHomeApiTests(CoursesApiTestCase):
    """Covers /api/courses/home/, the aggregated home screen payload."""

    def create_course(self, name, is_published=True, course_type=Course.CourseType.ARCHIVE):
        return Course.objects.create(
            name=name,
            course_type=course_type,
            is_published=is_published,
            allow_self_enrollment=True,
            created_by=self.staff_user,
        )

    def test_home_returns_enrolled_exams_and_popular_courses(self):
        enrolled_course = self.create_course('Enrolled course')
        first_quiz = self.create_quiz(enrolled_course, position=1)
        self.create_quiz(enrolled_course, position=2)
        self.create_quiz(enrolled_course, position=3, is_active=False)
        CourseEnrollment.objects.create(course=enrolled_course, user=self.learner)
        self.create_completed_attempt(
            self.learner,
            first_quiz,
            '2.00',
            outcomes=[True, True],
        )

        popular_course = self.create_course('Popular course')
        self.create_quiz(popular_course, position=1)
        for index in range(3):
            crowd_member = User.objects.create_user(
                username=f'crowd{index}',
                password='password',
            )
            CourseEnrollment.objects.create(course=popular_course, user=crowd_member)

        self.create_course('Unpublished course', is_published=False)

        self.authenticate_learner()
        response = self.client.get('/api/courses/home/')

        self.assertEqual(response.status_code, 200)

        enrolled = response.data['enrolled_courses']
        self.assertEqual([course['name'] for course in enrolled], ['Enrolled course'])
        # Inactive quizzes are excluded from both the total and the progress.
        self.assertEqual(enrolled[0]['quiz_count'], 2)
        self.assertEqual(enrolled[0]['completed_quiz_count'], 1)
        self.assertEqual(enrolled[0]['enrollment_count'], 1)
        self.assertTrue(enrolled[0]['is_enrolled'])

        exam_names = [exam['name'] for exam in response.data['course_exams']]
        self.assertEqual(exam_names, ['Quiz 2', 'Quiz 1'])
        first_exam = response.data['course_exams'][0]
        self.assertEqual(first_exam['course_id'], enrolled_course.id)
        self.assertEqual(first_exam['course_name'], 'Enrolled course')
        self.assertEqual(first_exam['course_type'], Course.CourseType.ARCHIVE)
        self.assertEqual(first_exam['attempt_status'], 'NOT_STARTED')
        self.assertEqual(first_exam['question_count'], 2)

        popular_names = [course['name'] for course in response.data['popular_courses']]
        self.assertEqual(popular_names, ['Popular course'])
        self.assertEqual(response.data['popular_courses'][0]['enrollment_count'], 3)

    def test_home_prioritises_in_progress_exams(self):
        course = self.create_course('Live course', course_type=Course.CourseType.LIVE)
        now = timezone.now()
        unlocked = self.create_quiz(course, position=1)
        CourseQuiz.objects.filter(pk=unlocked.pk).update(unlock_at=now - timedelta(hours=1))
        resumable = self.create_quiz(course, position=2)
        CourseQuiz.objects.filter(pk=resumable.pk).update(unlock_at=now - timedelta(hours=2))
        locked = self.create_quiz(course, position=3)
        CourseQuiz.objects.filter(pk=locked.pk).update(unlock_at=now + timedelta(days=1))
        CourseEnrollment.objects.create(course=course, user=self.learner)
        CourseQuizAttempt.objects.create(
            user=self.learner,
            quiz=resumable,
            expires_at=now + timedelta(minutes=20),
            total_questions=2,
            correct_mark='1.00',
            wrong_mark='0.00',
            unanswered_mark='0.00',
        )

        self.authenticate_learner()
        response = self.client.get('/api/courses/home/')

        self.assertEqual(response.status_code, 200)
        exams = response.data['course_exams']
        self.assertEqual([exam['name'] for exam in exams], ['Quiz 2', 'Quiz 1', 'Quiz 3'])
        self.assertEqual(exams[0]['attempt_status'], 'IN_PROGRESS')
        self.assertTrue(exams[1]['is_unlocked'])
        self.assertFalse(exams[2]['is_unlocked'])

    def test_home_without_enrollments_returns_empty_sections(self):
        self.create_course('Only course')

        self.authenticate_learner()
        response = self.client.get('/api/courses/home/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['enrolled_courses'], [])
        self.assertEqual(response.data['course_exams'], [])
        self.assertEqual(
            [course['name'] for course in response.data['popular_courses']],
            ['Only course'],
        )

    def test_home_limit_is_clamped_and_requires_authentication(self):
        for index in range(3):
            self.create_course(f'Course {index}')

        self.assertEqual(self.client.get('/api/courses/home/').status_code, 401)

        self.authenticate_learner()
        limited = self.client.get('/api/courses/home/', {'limit': 1})
        self.assertEqual(limited.status_code, 200)
        self.assertEqual(len(limited.data['popular_courses']), 1)

        invalid = self.client.get('/api/courses/home/', {'limit': 'abc'})
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(len(invalid.data['popular_courses']), 3)


class CourseQuizReorderServiceTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='reorder-staff',
            password='password',
            is_staff=True,
        )
        self.course = Course.objects.create(
            name='Reorder Course',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        self.quiz_one = CourseQuiz.objects.create(
            course=self.course,
            name='Quiz One',
            position=1,
            duration_minutes=10,
        )
        self.quiz_two = CourseQuiz.objects.create(
            course=self.course,
            name='Quiz Two',
            position=2,
            duration_minutes=10,
        )
        self.quiz_three = CourseQuiz.objects.create(
            course=self.course,
            name='Quiz Three',
            position=3,
            duration_minutes=10,
        )

    def test_reorder_course_quizzes_assigns_positions_in_supplied_order(self):
        reorder_course_quizzes(
            self.course,
            [self.quiz_three.id, self.quiz_one.id, self.quiz_two.id],
        )

        self.assertEqual(
            list(self.course.quizzes.values_list('id', 'position')),
            [
                (self.quiz_three.id, 1),
                (self.quiz_one.id, 2),
                (self.quiz_two.id, 3),
            ],
        )

    def test_reorder_course_quizzes_requires_exactly_every_quiz(self):
        with self.assertRaises(DjangoValidationError):
            reorder_course_quizzes(self.course, [self.quiz_one.id, self.quiz_two.id])

        with self.assertRaises(DjangoValidationError):
            reorder_course_quizzes(
                self.course,
                [self.quiz_one.id, self.quiz_two.id, self.quiz_two.id],
            )

    def test_auto_reorganize_course_quizzes_renumbers_and_renames(self):
        auto_reorganize_course_quizzes(self.course)

        self.assertEqual(
            list(self.course.quizzes.values_list('id', 'position', 'name')),
            [
                (self.quiz_one.id, 1, 'পরীক্ষা - ১'),
                (self.quiz_two.id, 2, 'পরীক্ষা - ২'),
                (self.quiz_three.id, 3, 'পরীক্ষা - ৩'),
            ],
        )

    def test_auto_reorganize_course_quizzes_with_no_quizzes_returns_empty(self):
        empty_course = Course.objects.create(
            name='Empty Course',
            course_type=Course.CourseType.ARCHIVE,
            created_by=self.staff_user,
        )
        self.assertEqual(auto_reorganize_course_quizzes(empty_course), [])


class CourseQuizReorderAdminTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            username='admin-reorder-staff',
            password='password',
            is_staff=True,
            is_superuser=True,
        )
        self.course = Course.objects.create(
            name='Admin Reorder Course',
            course_type=Course.CourseType.ARCHIVE,
            is_published=True,
            created_by=self.staff_user,
        )
        self.quiz_one = CourseQuiz.objects.create(
            course=self.course,
            name='Quiz One',
            position=1,
            duration_minutes=10,
        )
        self.quiz_two = CourseQuiz.objects.create(
            course=self.course,
            name='Quiz Two',
            position=2,
            duration_minutes=10,
        )
        self.client = Client()
        self.client.force_login(self.staff_user)

    def test_manual_reorder_posts_reorders_exams(self):
        response = self.client.post(
            reverse(
                'admin:courses_course_reorganize_exams',
                args=[self.course.pk],
            ),
            {
                f'position_{self.quiz_one.id}': '2',
                f'position_{self.quiz_two.id}': '1',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(self.course.quizzes.order_by('position').values_list('id', 'position')),
            [(self.quiz_two.id, 1), (self.quiz_one.id, 2)],
        )

    def test_auto_reorder_posts_renames_exams(self):
        response = self.client.post(
            reverse(
                'admin:courses_course_reorganize_exams',
                args=[self.course.pk],
            ),
            {'auto': '1'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(self.course.quizzes.order_by('position').values_list('id', 'position', 'name')),
            [(self.quiz_one.id, 1, 'পরীক্ষা - ১'), (self.quiz_two.id, 2, 'পরীক্ষা - ২')],
        )

