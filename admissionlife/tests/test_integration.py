"""
Integration tests for the AdmissionLife module.

Tests cover the main end-to-end flows:
1. Full enrollment flow: payment submit → admin approve → enrollment → exam access
2. Pre-recorded batch progression: sequential exam unlocking
3. Live batch scheduling: time-based exam access
4. Scoring and leaderboard: multiple users, correct ordering
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from admissionlife.models import (
    Batch,
    Enrollment,
    Exam,
    ExamAttempt,
    ExamQuestion,
    ExamSubmission,
    Payment,
)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class FullEnrollmentFlowTest(TestCase):
    """Test the complete enrollment flow: payment → approval → enrollment → exam access."""

    def setUp(self):
        self.client = APIClient()

        # Create users
        self.user = User.objects.create_user(
            username='student1', password='testpass123', first_name='Test', last_name='Student'
        )
        self.admin = User.objects.create_superuser(
            username='admin1', password='adminpass123'
        )

        # Create tokens
        self.user_token = Token.objects.create(user=self.user)
        self.admin_token = Token.objects.create(user=self.admin)

        # Create a batch with exams
        self.batch = Batch.objects.create(
            name='Test Batch',
            description='A test batch for integration testing',
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal('500.00'),
            is_active=True,
        )
        self.exam = Exam.objects.create(
            batch=self.batch,
            title='Exam 1',
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )
        # Add questions to the exam
        self.q1 = ExamQuestion.objects.create(
            exam=self.exam,
            question_text='What is 2+2?',
            answer_1='3', answer_2='4', answer_3='5', answer_4='6',
            correct_answer=2,
        )
        self.q2 = ExamQuestion.objects.create(
            exam=self.exam,
            question_text='What is 3+3?',
            answer_1='5', answer_2='6', answer_3='7', answer_4='8',
            correct_answer=2,
        )

    def test_full_enrollment_flow(self):
        """User submits payment → Admin approves → Enrollment created → User can access exams."""
        # Step 1: User submits payment
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)
        response = self.client.post('/api/admissionlife/payments/', {
            'batch': self.batch.id,
            'payment_method': 'bKash',
            'transaction_id': 'TXN123456',
            'sender_number': '01712345678',
            'amount': '500.00',
        })
        self.assertEqual(response.status_code, 201)
        payment_id = response.data['id']

        # Verify payment is PENDING
        payment = Payment.objects.get(id=payment_id)
        self.assertEqual(payment.status, Payment.PaymentStatus.PENDING)

        # Step 2: Verify user is NOT enrolled yet
        response = self.client.get(f'/api/admissionlife/enrollments/check/{self.batch.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_enrolled'])

        # Step 3: User cannot start exam (not enrolled)
        response = self.client.post(f'/api/admissionlife/exam-attempts/{self.exam.id}/start/')
        self.assertEqual(response.status_code, 403)

        # Step 4: Admin approves payment
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.admin_token.key)
        response = self.client.post(f'/api/admissionlife/payments/{payment_id}/approve/')
        self.assertEqual(response.status_code, 200)

        # Step 5: Verify enrollment was created
        self.assertTrue(Enrollment.objects.filter(user=self.user, batch=self.batch).exists())

        # Step 6: User checks enrollment
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)
        response = self.client.get(f'/api/admissionlife/enrollments/check/{self.batch.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_enrolled'])

        # Step 7: User can now start the exam
        response = self.client.post(f'/api/admissionlife/exam-attempts/{self.exam.id}/start/')
        self.assertEqual(response.status_code, 201)
        self.assertIn('attempt_id', response.data)
        self.assertIn('questions', response.data)
        self.assertEqual(len(response.data['questions']), 2)

        # Verify questions don't include correct_answer
        for q in response.data['questions']:
            self.assertNotIn('correct_answer', q)

    def test_payment_rejection_does_not_create_enrollment(self):
        """Admin rejects payment → No enrollment created."""
        # User submits payment
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)
        response = self.client.post('/api/admissionlife/payments/', {
            'batch': self.batch.id,
            'payment_method': 'Nagad',
            'transaction_id': 'TXN_REJECT_01',
            'sender_number': '01812345678',
            'amount': '500.00',
        })
        self.assertEqual(response.status_code, 201)
        payment_id = response.data['id']

        # Admin rejects payment
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.admin_token.key)
        response = self.client.post(f'/api/admissionlife/payments/{payment_id}/reject/', {
            'admin_notes': 'Invalid transaction ID',
        })
        self.assertEqual(response.status_code, 200)

        # Verify no enrollment
        self.assertFalse(Enrollment.objects.filter(user=self.user, batch=self.batch).exists())

        # User still cannot access exams
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)
        response = self.client.post(f'/api/admissionlife/exam-attempts/{self.exam.id}/start/')
        self.assertEqual(response.status_code, 403)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PreRecordedBatchProgressionTest(TestCase):
    """Test pre-recorded batch sequential exam unlocking."""

    def setUp(self):
        self.client = APIClient()

        self.user = User.objects.create_user(
            username='student2', password='testpass123'
        )
        self.user_token = Token.objects.create(user=self.user)

        # Create pre-recorded batch with 3 sequential exams
        self.batch = Batch.objects.create(
            name='Pre-Recorded Batch',
            description='Sequential exam batch',
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal('1000.00'),
            is_active=True,
        )

        self.exam1 = Exam.objects.create(
            batch=self.batch, title='Exam 1', duration_minutes=30, order=1, is_active=True
        )
        self.exam2 = Exam.objects.create(
            batch=self.batch, title='Exam 2', duration_minutes=30, order=2, is_active=True
        )
        self.exam3 = Exam.objects.create(
            batch=self.batch, title='Exam 3', duration_minutes=30, order=3, is_active=True
        )

        # Add questions to each exam
        for exam in [self.exam1, self.exam2, self.exam3]:
            ExamQuestion.objects.create(
                exam=exam,
                question_text=f'Question for {exam.title}',
                answer_1='A', answer_2='B', answer_3='C', answer_4='D',
                correct_answer=1,
            )

        # Enroll user directly
        Enrollment.objects.create(user=self.user, batch=self.batch)

    def _start_and_submit_exam(self, exam):
        """Helper: start an exam and submit correct answers."""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)

        # Start exam
        response = self.client.post(f'/api/admissionlife/exam-attempts/{exam.id}/start/')
        self.assertEqual(response.status_code, 201, f"Failed to start {exam.title}: {response.data}")
        attempt_id = response.data['attempt_id']
        questions = response.data['questions']

        # Submit all correct answers
        submissions = [
            {'question_id': q['id'], 'selected_answer': 1}
            for q in questions
        ]
        response = self.client.post(
            f'/api/admissionlife/exam-attempts/{attempt_id}/submit/',
            {'submissions': submissions},
            format='json',
        )
        self.assertEqual(response.status_code, 200, f"Failed to submit {exam.title}: {response.data}")
        return response

    def test_sequential_exam_progression(self):
        """Complete exam 1 → Exam 2 unlocks → Complete exam 2 → Exam 3 unlocks."""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)

        # Initially only exam 1 is accessible
        response = self.client.get(
            f'/api/admissionlife/exams/?batch_id={self.batch.id}'
        )
        self.assertEqual(response.status_code, 200)
        exams_data = response.data['results']
        # Exam 1 should be unlocked, exams 2 and 3 should be locked
        exam1_data = next(e for e in exams_data if e['id'] == self.exam1.id)
        exam2_data = next(e for e in exams_data if e['id'] == self.exam2.id)
        exam3_data = next(e for e in exams_data if e['id'] == self.exam3.id)
        self.assertTrue(exam1_data['is_unlocked'])
        self.assertFalse(exam2_data['is_unlocked'])
        self.assertFalse(exam3_data['is_unlocked'])

        # Cannot start exam 2 yet
        response = self.client.post(f'/api/admissionlife/exam-attempts/{self.exam2.id}/start/')
        self.assertEqual(response.status_code, 403)

        # Complete exam 1
        self._start_and_submit_exam(self.exam1)

        # Now exam 2 should be unlocked
        response = self.client.get(
            f'/api/admissionlife/exams/?batch_id={self.batch.id}'
        )
        exams_data = response.data['results']
        exam2_data = next(e for e in exams_data if e['id'] == self.exam2.id)
        exam3_data = next(e for e in exams_data if e['id'] == self.exam3.id)
        self.assertTrue(exam2_data['is_unlocked'])
        self.assertFalse(exam3_data['is_unlocked'])

        # Complete exam 2
        self._start_and_submit_exam(self.exam2)

        # Now exam 3 should be unlocked
        response = self.client.get(
            f'/api/admissionlife/exams/?batch_id={self.batch.id}'
        )
        exams_data = response.data['results']
        exam3_data = next(e for e in exams_data if e['id'] == self.exam3.id)
        self.assertTrue(exam3_data['is_unlocked'])

        # Can start and complete exam 3
        self._start_and_submit_exam(self.exam3)

    def test_cannot_retake_completed_exam(self):
        """Once an exam is completed, user cannot start it again."""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)

        # Complete exam 1
        self._start_and_submit_exam(self.exam1)

        # Try to start exam 1 again
        response = self.client.post(f'/api/admissionlife/exam-attempts/{self.exam1.id}/start/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('already completed', response.data['detail'])


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class LiveBatchSchedulingTest(TestCase):
    """Test live batch time-based exam access."""

    def setUp(self):
        self.client = APIClient()

        self.user = User.objects.create_user(
            username='student3', password='testpass123'
        )
        self.admin = User.objects.create_superuser(
            username='admin2', password='adminpass123'
        )
        self.user_token = Token.objects.create(user=self.user)
        self.admin_token = Token.objects.create(user=self.admin)

        # Create live batch
        self.batch = Batch.objects.create(
            name='Live Batch',
            description='Time-based exam batch',
            batch_type=Batch.BatchType.LIVE,
            price=Decimal('800.00'),
            is_active=True,
        )

        self.exam = Exam.objects.create(
            batch=self.batch,
            title='Live Exam 1',
            duration_minutes=45,
            order=1,
            is_active=True,
            unlock_datetime=None,  # Not scheduled yet
        )

        ExamQuestion.objects.create(
            exam=self.exam,
            question_text='Live question 1',
            answer_1='A', answer_2='B', answer_3='C', answer_4='D',
            correct_answer=3,
        )

        # Enroll user
        Enrollment.objects.create(user=self.user, batch=self.batch)

    def test_live_batch_scheduling_flow(self):
        """Admin sets unlock_datetime → Time passes → User gains access."""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)

        # Step 1: Exam has no unlock_datetime - user cannot access
        response = self.client.post(f'/api/admissionlife/exam-attempts/{self.exam.id}/start/')
        self.assertEqual(response.status_code, 403)
        self.assertIn('not been scheduled', response.data['detail'])

        # Step 2: Admin sets unlock_datetime to the future
        future_time = timezone.now() + timedelta(hours=1)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.admin_token.key)
        response = self.client.post(
            f'/api/admissionlife/exams/{self.exam.id}/set-unlock-datetime/',
            {'unlock_datetime': future_time.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        # Step 3: User still cannot access (time hasn't passed)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)
        response = self.client.post(f'/api/admissionlife/exam-attempts/{self.exam.id}/start/')
        self.assertEqual(response.status_code, 403)
        self.assertIn('unlocks at', response.data['detail'])

        # Step 4: Admin sets unlock_datetime to the past (simulating time passing)
        past_time = timezone.now() - timedelta(hours=1)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.admin_token.key)
        response = self.client.post(
            f'/api/admissionlife/exams/{self.exam.id}/set-unlock-datetime/',
            {'unlock_datetime': past_time.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        # Step 5: User can now access the exam
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)
        response = self.client.post(f'/api/admissionlife/exam-attempts/{self.exam.id}/start/')
        self.assertEqual(response.status_code, 201)
        self.assertIn('attempt_id', response.data)

    def test_exam_list_shows_unlock_status(self):
        """Exam list for live batch shows is_unlocked based on unlock_datetime."""
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user_token.key)

        # No unlock_datetime set - should be locked
        response = self.client.get(f'/api/admissionlife/exams/?batch_id={self.batch.id}')
        self.assertEqual(response.status_code, 200)
        exam_data = response.data['results'][0]
        self.assertFalse(exam_data['is_unlocked'])

        # Set unlock_datetime to the past
        self.exam.unlock_datetime = timezone.now() - timedelta(minutes=30)
        self.exam.save()

        response = self.client.get(f'/api/admissionlife/exams/?batch_id={self.batch.id}')
        self.assertEqual(response.status_code, 200)
        exam_data = response.data['results'][0]
        self.assertTrue(exam_data['is_unlocked'])


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ScoringAndLeaderboardTest(TestCase):
    """Test scoring with negative marking and leaderboard ordering."""

    def setUp(self):
        self.client = APIClient()

        # Create 3 users
        self.user1 = User.objects.create_user(
            username='alice', password='testpass123', first_name='Alice', last_name='Smith'
        )
        self.user2 = User.objects.create_user(
            username='bob', password='testpass123', first_name='Bob', last_name='Jones'
        )
        self.user3 = User.objects.create_user(
            username='charlie', password='testpass123', first_name='Charlie', last_name='Brown'
        )

        self.token1 = Token.objects.create(user=self.user1)
        self.token2 = Token.objects.create(user=self.user2)
        self.token3 = Token.objects.create(user=self.user3)

        # Create batch and exam
        self.batch = Batch.objects.create(
            name='Leaderboard Batch',
            description='Batch for leaderboard testing',
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal('300.00'),
            is_active=True,
        )

        self.exam = Exam.objects.create(
            batch=self.batch,
            title='Leaderboard Exam',
            duration_minutes=60,
            order=1,
            is_active=True,
        )

        # Create 4 questions
        self.questions = []
        for i in range(4):
            q = ExamQuestion.objects.create(
                exam=self.exam,
                question_text=f'Question {i+1}',
                answer_1='A', answer_2='B', answer_3='C', answer_4='D',
                correct_answer=1,  # Correct answer is always 1
            )
            self.questions.append(q)

        # Enroll all users
        for user in [self.user1, self.user2, self.user3]:
            Enrollment.objects.create(user=user, batch=self.batch)

    def _take_exam(self, user, token, answers):
        """
        Helper: user takes exam with specified answers.
        answers is a list of selected_answer values (1-4 or None for unanswered).
        """
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.key)

        # Start exam
        response = self.client.post(f'/api/admissionlife/exam-attempts/{self.exam.id}/start/')
        self.assertEqual(response.status_code, 201, f"Failed to start exam for {user.username}: {response.data}")
        attempt_id = response.data['attempt_id']

        # Submit answers
        submissions = []
        for i, answer in enumerate(answers):
            submissions.append({
                'question_id': self.questions[i].id,
                'selected_answer': answer,
            })

        response = self.client.post(
            f'/api/admissionlife/exam-attempts/{attempt_id}/submit/',
            {'submissions': submissions},
            format='json',
        )
        self.assertEqual(response.status_code, 200, f"Failed to submit for {user.username}: {response.data}")
        return response

    def test_scoring_with_negative_marking(self):
        """Verify scoring: +1 correct, -0.25 incorrect, 0 unanswered."""
        # Alice: 3 correct, 1 incorrect → 3 - 0.25 = 2.75
        result = self._take_exam(self.user1, self.token1, [1, 1, 1, 2])
        self.assertEqual(float(result.data['score']), 2.75)
        self.assertEqual(result.data['correct_count'], 3)
        self.assertEqual(result.data['incorrect_count'], 1)
        self.assertEqual(result.data['unanswered_count'], 0)

    def test_scoring_all_correct(self):
        """All correct answers → score equals total questions."""
        result = self._take_exam(self.user1, self.token1, [1, 1, 1, 1])
        self.assertEqual(float(result.data['score']), 4.0)
        self.assertEqual(result.data['correct_count'], 4)
        self.assertEqual(result.data['incorrect_count'], 0)

    def test_scoring_with_unanswered(self):
        """Unanswered questions get 0 marks."""
        result = self._take_exam(self.user1, self.token1, [1, None, None, 2])
        # 1 correct (+1), 2 unanswered (0), 1 incorrect (-0.25) = 0.75
        self.assertEqual(float(result.data['score']), 0.75)
        self.assertEqual(result.data['correct_count'], 1)
        self.assertEqual(result.data['incorrect_count'], 1)
        self.assertEqual(result.data['unanswered_count'], 2)

    def test_leaderboard_reflects_correct_ordering(self):
        """Multiple users take exam → Leaderboard reflects correct ordering by score."""
        # Alice: 4 correct → score 4.0
        self._take_exam(self.user1, self.token1, [1, 1, 1, 1])

        # Bob: 2 correct, 2 incorrect → 2 - 0.5 = 1.5
        self._take_exam(self.user2, self.token2, [1, 1, 2, 2])

        # Charlie: 3 correct, 1 unanswered → 3.0
        self._take_exam(self.user3, self.token3, [1, 1, 1, None])

        # Check exam leaderboard
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token1.key)
        response = self.client.get(f'/api/admissionlife/exams/{self.exam.id}/leaderboard/')
        self.assertEqual(response.status_code, 200)

        entries = response.data['entries']
        self.assertEqual(len(entries), 3)

        # Verify ordering: Alice (4.0) > Charlie (3.0) > Bob (1.5)
        self.assertEqual(entries[0]['user_display_name'], 'Alice Smith')
        self.assertEqual(entries[0]['score'], 4.0)
        self.assertEqual(entries[0]['rank'], 1)

        self.assertEqual(entries[1]['user_display_name'], 'Charlie Brown')
        self.assertEqual(entries[1]['score'], 3.0)
        self.assertEqual(entries[1]['rank'], 2)

        self.assertEqual(entries[2]['user_display_name'], 'Bob Jones')
        self.assertEqual(entries[2]['score'], 1.5)
        self.assertEqual(entries[2]['rank'], 3)

    def test_batch_leaderboard_includes_user_rank(self):
        """Batch leaderboard includes requesting user's own rank."""
        # All users take the exam
        self._take_exam(self.user1, self.token1, [1, 1, 1, 1])  # 4.0
        self._take_exam(self.user2, self.token2, [1, 1, 2, 2])  # 1.5
        self._take_exam(self.user3, self.token3, [1, 1, 1, None])  # 3.0

        # Bob checks leaderboard - should see his own rank
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.token2.key)
        response = self.client.get(f'/api/admissionlife/batches/{self.batch.id}/leaderboard/')
        self.assertEqual(response.status_code, 200)

        # Bob should be rank 3 (lowest score)
        current_user_entry = response.data['current_user_entry']
        self.assertIsNotNone(current_user_entry)
        self.assertEqual(current_user_entry['rank'], 3)
        self.assertEqual(current_user_entry['total_score'], 1.5)

    def test_non_enrolled_user_cannot_view_leaderboard(self):
        """Non-enrolled user gets 403 on leaderboard."""
        non_enrolled = User.objects.create_user(username='outsider', password='testpass123')
        outsider_token = Token.objects.create(user=non_enrolled)

        self.client.credentials(HTTP_AUTHORIZATION='Token ' + outsider_token.key)
        response = self.client.get(f'/api/admissionlife/batches/{self.batch.id}/leaderboard/')
        self.assertEqual(response.status_code, 403)
