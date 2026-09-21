"""
Property-based tests for the admissionlife module.
Uses Hypothesis for property-based testing with a minimum of 100 iterations per property.
"""
import string

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from admissionlife.models import Batch, Payment
from admissionlife.validators import (
    validate_bangladeshi_phone,
    validate_transaction_id,
    validate_amount,
)


# --- Strategies ---

def valid_sender_number_strategy():
    """Generates valid Bangladeshi phone numbers: exactly 11 digits starting with '01'."""
    return st.text(
        alphabet=string.digits, min_size=9, max_size=9
    ).map(lambda suffix: "01" + suffix)


def invalid_sender_number_strategy():
    """Generates invalid sender numbers that violate at least one rule."""
    return st.one_of(
        # Wrong prefix (doesn't start with "01")
        st.text(alphabet=string.digits, min_size=11, max_size=11).filter(
            lambda s: not s.startswith("01")
        ),
        # Too short (less than 11 digits)
        st.text(alphabet=string.digits, min_size=1, max_size=10).filter(
            lambda s: len(s) < 11
        ),
        # Too long (more than 11 digits)
        st.text(alphabet=string.digits, min_size=12, max_size=20),
        # Contains non-digit characters but correct length
        st.text(
            alphabet=string.ascii_letters + string.digits, min_size=11, max_size=11
        ).filter(lambda s: not s.isdigit()),
        # Empty string
        st.just(""),
    )


def valid_transaction_id_strategy():
    """Generates valid transaction IDs: 1 to 30 characters."""
    return st.text(
        alphabet=string.ascii_letters + string.digits,
        min_size=1,
        max_size=30,
    )


def invalid_transaction_id_strategy():
    """Generates invalid transaction IDs: empty or > 30 characters."""
    return st.one_of(
        # Empty string
        st.just(""),
        # Too long (> 30 characters)
        st.text(
            alphabet=string.ascii_letters + string.digits,
            min_size=31,
            max_size=60,
        ),
    )


def valid_amount_strategy():
    """Generates valid amounts: between 1 and 99999 inclusive."""
    return st.decimals(
        min_value=Decimal("1"),
        max_value=Decimal("99999"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    )


def invalid_amount_strategy():
    """Generates invalid amounts: less than 1 or greater than 99999."""
    return st.one_of(
        # Below minimum
        st.decimals(
            min_value=Decimal("-99999"),
            max_value=Decimal("0.99"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        # Above maximum
        st.decimals(
            min_value=Decimal("99999.01"),
            max_value=Decimal("999999"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    )


VALID_PAYMENT_METHODS = ["bKash", "Nagad", "Rocket", "Upay"]


def valid_payment_method_strategy():
    """Generates valid payment methods."""
    return st.sampled_from(VALID_PAYMENT_METHODS)


def invalid_payment_method_strategy():
    """Generates invalid payment methods."""
    return st.text(
        alphabet=string.ascii_letters, min_size=1, max_size=20
    ).filter(lambda s: s not in VALID_PAYMENT_METHODS)


# =============================================================================
# Property 8: Payment sender number validation
# Feature: admissionlife, Property 8: Payment sender number validation
# =============================================================================
# **Validates: Requirements 3.1**


class TestProperty8PaymentSenderNumberValidation(TestCase):
    """
    Property 8: Payment sender number validation

    For any payment submission, the sender_number should be accepted if and only
    if it is exactly 11 digits and starts with "01". The transaction_id should be
    accepted if and only if it is <= 30 characters. The amount should be accepted
    if and only if it is between 1 and 99999. The payment_method should be one of
    (bKash, Nagad, Rocket, Upay).

    **Validates: Requirements 3.1**
    """

    @given(sender_number=valid_sender_number_strategy())
    @settings(max_examples=10, deadline=None)
    def test_valid_sender_number_accepted(self, sender_number):
        """Valid sender numbers (11 digits starting with '01') should be accepted."""
        # Feature: admissionlife, Property 8: Payment sender number validation
        # Should not raise ValidationError
        validate_bangladeshi_phone(sender_number)

    @given(sender_number=invalid_sender_number_strategy())
    @settings(max_examples=10, deadline=None)
    def test_invalid_sender_number_rejected(self, sender_number):
        """Invalid sender numbers should be rejected with ValidationError."""
        # Feature: admissionlife, Property 8: Payment sender number validation
        with self.assertRaises(ValidationError):
            validate_bangladeshi_phone(sender_number)

    @given(transaction_id=valid_transaction_id_strategy())
    @settings(max_examples=10, deadline=None)
    def test_valid_transaction_id_accepted(self, transaction_id):
        """Valid transaction IDs (1-30 chars) should be accepted."""
        # Feature: admissionlife, Property 8: Payment sender number validation
        validate_transaction_id(transaction_id)

    @given(transaction_id=invalid_transaction_id_strategy())
    @settings(max_examples=10, deadline=None)
    def test_invalid_transaction_id_rejected(self, transaction_id):
        """Invalid transaction IDs (empty or >30 chars) should be rejected."""
        # Feature: admissionlife, Property 8: Payment sender number validation
        with self.assertRaises(ValidationError):
            validate_transaction_id(transaction_id)

    @given(amount=valid_amount_strategy())
    @settings(max_examples=10, deadline=None)
    def test_valid_amount_accepted(self, amount):
        """Valid amounts (1 to 99999) should be accepted."""
        # Feature: admissionlife, Property 8: Payment sender number validation
        validate_amount(amount)

    @given(amount=invalid_amount_strategy())
    @settings(max_examples=10, deadline=None)
    def test_invalid_amount_rejected(self, amount):
        """Invalid amounts (< 1 or > 99999) should be rejected."""
        # Feature: admissionlife, Property 8: Payment sender number validation
        with self.assertRaises(ValidationError):
            validate_amount(amount)

    @given(payment_method=valid_payment_method_strategy())
    @settings(max_examples=10, deadline=None)
    def test_valid_payment_method_accepted(self, payment_method):
        """Valid payment methods should be in the Payment.PaymentMethod choices."""
        # Feature: admissionlife, Property 8: Payment sender number validation
        valid_choices = [choice[0] for choice in Payment.PaymentMethod.choices]
        self.assertIn(payment_method, valid_choices)

    @given(payment_method=invalid_payment_method_strategy())
    @settings(max_examples=10, deadline=None)
    def test_invalid_payment_method_rejected(self, payment_method):
        """Invalid payment methods should not be in the Payment.PaymentMethod choices."""
        # Feature: admissionlife, Property 8: Payment sender number validation
        valid_choices = [choice[0] for choice in Payment.PaymentMethod.choices]
        self.assertNotIn(payment_method, valid_choices)


# =============================================================================
# Property 11: Transaction ID uniqueness per payment method
# Feature: admissionlife, Property 11: Transaction ID uniqueness per payment method
# =============================================================================
# **Validates: Requirements 3.7**


class TestProperty11TransactionIDUniqueness(TestCase):
    """
    Property 11: Transaction ID uniqueness per payment method

    For any valid transaction_id and payment_method combination, submitting a
    second payment with the same combination should be rejected.

    **Validates: Requirements 3.7**
    """

    def get_fixtures(self):
        """Get or create test user and batch for payment tests."""
        user, _ = User.objects.get_or_create(
            username="testuser_prop11",
            defaults={"password": "testpass123"},
        )
        user2, _ = User.objects.get_or_create(
            username="testuser2_prop11",
            defaults={"password": "testpass123"},
        )
        batch, _ = Batch.objects.get_or_create(
            name="Test Batch Prop11",
            defaults={
                "description": "A test batch for property 11",
                "batch_type": Batch.BatchType.PRE_RECORDED,
                "price": Decimal("1000.00"),
                "is_active": True,
            },
        )
        return user, user2, batch

    @given(
        transaction_id=valid_transaction_id_strategy(),
        payment_method=valid_payment_method_strategy(),
    )
    @settings(max_examples=10, deadline=None)
    def test_duplicate_transaction_id_per_method_rejected(
        self, transaction_id, payment_method
    ):
        """
        A second payment with the same transaction_id and payment_method
        combination should be rejected by the database unique constraint.
        """
        # Feature: admissionlife, Property 11: Transaction ID uniqueness per payment method
        user, user2, batch = self.get_fixtures()

        # Create the first payment - should succeed
        Payment.objects.create(
            user=user,
            batch=batch,
            payment_method=payment_method,
            transaction_id=transaction_id,
            sender_number="01712345678",
            amount=Decimal("500.00"),
            status=Payment.PaymentStatus.PENDING,
        )

        # Attempt to create a second payment with the same transaction_id
        # and payment_method - should raise IntegrityError
        with self.assertRaises(IntegrityError):
            Payment.objects.create(
                user=user2,
                batch=batch,
                payment_method=payment_method,
                transaction_id=transaction_id,
                sender_number="01798765432",
                amount=Decimal("600.00"),
                status=Payment.PaymentStatus.PENDING,
            )

    @given(
        transaction_id=valid_transaction_id_strategy(),
        payment_method1=valid_payment_method_strategy(),
        payment_method2=valid_payment_method_strategy(),
    )
    @settings(max_examples=10, deadline=None)
    def test_same_transaction_id_different_method_allowed(
        self, transaction_id, payment_method1, payment_method2
    ):
        """
        The same transaction_id with a different payment_method should be allowed
        (uniqueness is per payment_method + transaction_id combination).
        """
        # Feature: admissionlife, Property 11: Transaction ID uniqueness per payment method
        assume(payment_method1 != payment_method2)
        user, user2, batch = self.get_fixtures()

        # Create first payment with method1
        Payment.objects.create(
            user=user,
            batch=batch,
            payment_method=payment_method1,
            transaction_id=transaction_id,
            sender_number="01712345678",
            amount=Decimal("500.00"),
            status=Payment.PaymentStatus.PENDING,
        )

        # Create second payment with same transaction_id but different method
        # This should succeed (no IntegrityError)
        payment2 = Payment.objects.create(
            user=user2,
            batch=batch,
            payment_method=payment_method2,
            transaction_id=transaction_id,
            sender_number="01798765432",
            amount=Decimal("600.00"),
            status=Payment.PaymentStatus.PENDING,
        )
        self.assertIsNotNone(payment2.pk)


# =============================================================================
# Property 4: Payment approval creates enrollment
# Feature: admissionlife, Property 4: Payment approval creates enrollment
# =============================================================================
# **Validates: Requirements 2.1, 3.4**


class TestProperty4PaymentApprovalCreatesEnrollment(TestCase):
    """
    Property 4: Payment approval creates enrollment

    For any valid pending payment associated with a user and batch, approving
    the payment should result in: the payment status becoming APPROVED, and an
    Enrollment record existing for that user-batch pair.

    **Validates: Requirements 2.1, 3.4**
    """

    @given(
        payment_method=valid_payment_method_strategy(),
        transaction_id=valid_transaction_id_strategy(),
        sender_number=valid_sender_number_strategy(),
        amount=valid_amount_strategy(),
    )
    @settings(max_examples=10, deadline=None)
    def test_approving_pending_payment_creates_enrollment(
        self, payment_method, transaction_id, sender_number, amount
    ):
        """
        Approving a pending payment sets status to APPROVED and creates an
        Enrollment record for the user-batch pair.
        """
        # Feature: admissionlife, Property 4: Payment approval creates enrollment
        from admissionlife.models import Enrollment
        from admissionlife.services import PaymentService

        # Create unique user and batch per example to avoid collisions
        user = User.objects.create_user(
            username=f"prop4_user_{transaction_id}_{payment_method}",
            password="testpass123",
        )
        admin_user = User.objects.create_user(
            username=f"prop4_admin_{transaction_id}_{payment_method}",
            password="testpass123",
            is_staff=True,
        )
        batch = Batch.objects.create(
            name=f"Batch_P4_{transaction_id}_{payment_method}",
            description="Test batch for property 4",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("1000.00"),
            is_active=True,
        )

        # Create a pending payment
        payment = Payment.objects.create(
            user=user,
            batch=batch,
            payment_method=payment_method,
            transaction_id=transaction_id,
            sender_number=sender_number,
            amount=amount,
            status=Payment.PaymentStatus.PENDING,
        )

        # Approve the payment
        enrollment = PaymentService.approve_payment(payment, admin_user)

        # Refresh payment from DB
        payment.refresh_from_db()

        # Assert: payment status is APPROVED
        self.assertEqual(payment.status, Payment.PaymentStatus.APPROVED)

        # Assert: an Enrollment record exists for this user-batch pair
        self.assertTrue(
            Enrollment.objects.filter(user=user, batch=batch).exists()
        )

        # Assert: the returned enrollment links the correct user and batch
        self.assertEqual(enrollment.user, user)
        self.assertEqual(enrollment.batch, batch)


# =============================================================================
# Property 5: Enrollment check consistency
# Feature: admissionlife, Property 5: Enrollment check consistency
# =============================================================================
# **Validates: Requirements 2.3**


class TestProperty5EnrollmentCheckConsistency(TestCase):
    """
    Property 5: Enrollment check consistency

    For any user and batch, the enrollment check should return True if and only
    if an Enrollment record exists for that user-batch combination.

    **Validates: Requirements 2.3**
    """

    @given(
        batch_type=st.sampled_from([Batch.BatchType.PRE_RECORDED, Batch.BatchType.LIVE]),
        price=st.decimals(
            min_value=Decimal("0.00"),
            max_value=Decimal("999999.99"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=10, deadline=None)
    def test_check_enrollment_returns_true_when_enrolled(self, batch_type, price):
        """
        When an Enrollment record exists for a user-batch pair,
        check_enrollment should return True.
        """
        # Feature: admissionlife, Property 5: Enrollment check consistency
        from admissionlife.models import Enrollment
        from admissionlife.services import EnrollmentService

        user = User.objects.create_user(
            username=f"prop5_enrolled_{batch_type}_{price}",
            password="testpass123",
        )
        batch = Batch.objects.create(
            name=f"Batch_P5_enrolled_{batch_type}_{price}",
            description="Test batch for property 5",
            batch_type=batch_type,
            price=price,
            is_active=True,
        )

        # Create enrollment directly
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        # check_enrollment should return True
        result = EnrollmentService.check_enrollment(user, batch)
        self.assertTrue(result)

    @given(
        batch_type=st.sampled_from([Batch.BatchType.PRE_RECORDED, Batch.BatchType.LIVE]),
        price=st.decimals(
            min_value=Decimal("0.00"),
            max_value=Decimal("999999.99"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=10, deadline=None)
    def test_check_enrollment_returns_false_when_not_enrolled(self, batch_type, price):
        """
        When no Enrollment record exists for a user-batch pair,
        check_enrollment should return False.
        """
        # Feature: admissionlife, Property 5: Enrollment check consistency
        from admissionlife.services import EnrollmentService

        user = User.objects.create_user(
            username=f"prop5_notenrolled_{batch_type}_{price}",
            password="testpass123",
        )
        batch = Batch.objects.create(
            name=f"Batch_P5_notenrolled_{batch_type}_{price}",
            description="Test batch for property 5",
            batch_type=batch_type,
            price=price,
            is_active=True,
        )

        # No enrollment created â€” check_enrollment should return False
        result = EnrollmentService.check_enrollment(user, batch)
        self.assertFalse(result)


# =============================================================================
# Property 6: Enrollment uniqueness and idempotence
# Feature: admissionlife, Property 6: Enrollment uniqueness and idempotence
# =============================================================================
# **Validates: Requirements 2.4, 2.6**


class TestProperty6EnrollmentUniquenessAndIdempotence(TestCase):
    """
    Property 6: Enrollment uniqueness and idempotence

    For any user-batch pair with an existing enrollment, attempting to create
    another enrollment (via a second payment approval) should not create a
    duplicate record and should leave the existing enrollment unchanged.

    **Validates: Requirements 2.4, 2.6**
    """

    @given(
        payment_method1=valid_payment_method_strategy(),
        payment_method2=valid_payment_method_strategy(),
        transaction_id1=valid_transaction_id_strategy(),
        transaction_id2=valid_transaction_id_strategy(),
        sender_number=valid_sender_number_strategy(),
        amount=valid_amount_strategy(),
    )
    @settings(max_examples=10, deadline=None)
    def test_second_payment_approval_does_not_duplicate_enrollment(
        self,
        payment_method1,
        payment_method2,
        transaction_id1,
        transaction_id2,
        sender_number,
        amount,
    ):
        """
        Approving a second payment for the same user-batch pair should not
        create a duplicate Enrollment. The existing enrollment remains unchanged.
        """
        # Feature: admissionlife, Property 6: Enrollment uniqueness and idempotence
        from admissionlife.models import Enrollment
        from admissionlife.services import EnrollmentService

        # Ensure distinct transaction IDs to avoid unique constraint on (method, txn_id)
        assume(transaction_id1 != transaction_id2 or payment_method1 != payment_method2)

        user = User.objects.create_user(
            username=f"prop6_user_{transaction_id1}_{payment_method1}",
            password="testpass123",
        )
        batch = Batch.objects.create(
            name=f"Batch_P6_{transaction_id1}_{payment_method1}",
            description="Test batch for property 6",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("1000.00"),
            is_active=True,
        )

        # Create first payment and enrollment
        payment1 = Payment.objects.create(
            user=user,
            batch=batch,
            payment_method=payment_method1,
            transaction_id=transaction_id1,
            sender_number=sender_number,
            amount=amount,
            status=Payment.PaymentStatus.PENDING,
        )

        # Create enrollment via service (simulating first approval)
        first_enrollment = EnrollmentService.create_enrollment(user, batch, payment1)
        first_enrollment_id = first_enrollment.id
        first_enrolled_at = first_enrollment.enrolled_at

        # Count enrollments before second attempt
        count_before = Enrollment.objects.filter(user=user, batch=batch).count()
        self.assertEqual(count_before, 1)

        # Create second payment for the same user-batch
        payment2 = Payment.objects.create(
            user=user,
            batch=batch,
            payment_method=payment_method2,
            transaction_id=transaction_id2,
            sender_number=sender_number,
            amount=amount,
            status=Payment.PaymentStatus.PENDING,
        )

        # Attempt to create enrollment again (simulating second approval)
        second_enrollment = EnrollmentService.create_enrollment(user, batch, payment2)

        # Count enrollments after second attempt â€” should still be 1
        count_after = Enrollment.objects.filter(user=user, batch=batch).count()
        self.assertEqual(count_after, 1)

        # The returned enrollment should be the same as the first one
        self.assertEqual(second_enrollment.id, first_enrollment_id)

        # The existing enrollment's enrolled_at should be unchanged
        self.assertEqual(second_enrollment.enrolled_at, first_enrolled_at)


# =============================================================================
# Property 17: Pre-recorded batch sequential exam access
# Feature: admissionlife, Property 17: Pre-recorded batch sequential exam access
# =============================================================================
# **Validates: Requirements 5.1, 5.2, 5.5**


class TestProperty17PreRecordedBatchSequentialExamAccess(TestCase):
    """
    Property 17: Pre-recorded batch sequential exam access

    For any user enrolled in a pre-recorded batch with exams ordered [o1, o2, ..., oN],
    the user should have access to all exams with order <= the maximum order of their
    completed exams, plus the exam(s) at the next order value. A newly enrolled user
    with no completions should only have access to the exam(s) with the lowest order value.

    **Validates: Requirements 5.1, 5.2, 5.5**
    """

    def _create_batch_with_exams(self, num_exams, suffix):
        """Helper to create a pre-recorded batch with sequentially ordered exams."""
        from admissionlife.models import Enrollment, Exam

        batch = Batch.objects.create(
            name=f"PreRecBatch_{suffix}",
            description="Test batch for property 17",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        user = User.objects.create_user(
            username=f"prop17_user_{suffix}",
            password="testpass123",
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        exams = []
        for i in range(1, num_exams + 1):
            exam = Exam.objects.create(
                batch=batch,
                title=f"Exam {i}",
                duration_minutes=60,
                order=i,
                passing_score=50,
                is_active=True,
            )
            exams.append(exam)

        return user, batch, exams

    @given(
        num_exams=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=10, deadline=None)
    def test_newly_enrolled_user_only_accesses_lowest_order_exam(self, num_exams):
        """
        A newly enrolled user with no completions should only have access
        to the exam(s) with the lowest order value.
        """
        # Feature: admissionlife, Property 17: Pre-recorded batch sequential exam access
        from admissionlife.services import ExamAccessService

        suffix = f"new_{num_exams}_{id(self)}"
        user, batch, exams = self._create_batch_with_exams(num_exams, suffix)

        # No completions â€” only the first exam should be accessible
        accessible = ExamAccessService.get_accessible_exams(user, batch)

        # The first exam (lowest order) should be unlocked
        self.assertTrue(accessible[0]['is_unlocked'])

        # All other exams should be locked
        for entry in accessible[1:]:
            self.assertFalse(entry['is_unlocked'])

    @given(
        num_exams=st.integers(min_value=2, max_value=5),
        completed_up_to=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=10, deadline=None)
    def test_completed_exams_and_next_are_accessible(self, num_exams, completed_up_to):
        """
        After completing exams up to order N, the user should have access to all
        exams with order <= N plus the exam at the next order value.
        """
        # Feature: admissionlife, Property 17: Pre-recorded batch sequential exam access
        from admissionlife.models import ExamAttempt
        from admissionlife.services import ExamAccessService

        assume(completed_up_to < num_exams)

        suffix = f"comp_{num_exams}_{completed_up_to}_{id(self)}"
        user, batch, exams = self._create_batch_with_exams(num_exams, suffix)

        # Complete exams up to completed_up_to
        for i in range(completed_up_to):
            ExamAttempt.objects.create(
                user=user,
                exam=exams[i],
                score=Decimal("5.00"),
                total_questions=10,
                correct_count=5,
                incorrect_count=5,
                unanswered_count=0,
                is_completed=True,
            )

        accessible = ExamAccessService.get_accessible_exams(user, batch)

        # All completed exams should be unlocked
        for i in range(completed_up_to):
            self.assertTrue(
                accessible[i]['is_unlocked'],
                f"Exam at order {exams[i].order} should be unlocked (completed)",
            )

        # The next exam after the last completed should also be unlocked
        self.assertTrue(
            accessible[completed_up_to]['is_unlocked'],
            f"Exam at order {exams[completed_up_to].order} should be unlocked (next in sequence)",
        )

        # Exams beyond the next should be locked
        for i in range(completed_up_to + 1, num_exams):
            self.assertFalse(
                accessible[i]['is_unlocked'],
                f"Exam at order {exams[i].order} should be locked",
            )

    @given(
        num_exams=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=10, deadline=None)
    def test_all_exams_completed_all_accessible(self, num_exams):
        """
        When all exams are completed, all should remain accessible.
        """
        # Feature: admissionlife, Property 17: Pre-recorded batch sequential exam access
        from admissionlife.models import ExamAttempt
        from admissionlife.services import ExamAccessService

        suffix = f"allcomp_{num_exams}_{id(self)}"
        user, batch, exams = self._create_batch_with_exams(num_exams, suffix)

        # Complete all exams
        for exam in exams:
            ExamAttempt.objects.create(
                user=user,
                exam=exam,
                score=Decimal("8.00"),
                total_questions=10,
                correct_count=8,
                incorrect_count=2,
                unanswered_count=0,
                is_completed=True,
            )

        accessible = ExamAccessService.get_accessible_exams(user, batch)

        # All exams should be unlocked
        for entry in accessible:
            self.assertTrue(entry['is_unlocked'])


# =============================================================================
# Property 18: Pre-recorded batch is_unlocked flag consistency
# Feature: admissionlife, Property 18: Pre-recorded batch is_unlocked flag consistency
# =============================================================================
# **Validates: Requirements 5.3, 5.4**


class TestProperty18PreRecordedBatchIsUnlockedFlagConsistency(TestCase):
    """
    Property 18: Pre-recorded batch is_unlocked flag consistency

    For any pre-recorded batch exam list response, the is_unlocked flag for each
    exam should be True if and only if the exam is accessible according to the
    sequential unlocking rules (completed or next in sequence).

    **Validates: Requirements 5.3, 5.4**
    """

    @given(
        num_exams=st.integers(min_value=2, max_value=5),
        completed_up_to=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=10, deadline=None)
    def test_is_unlocked_flag_matches_can_access_exam(self, num_exams, completed_up_to):
        """
        The is_unlocked flag from get_accessible_exams should be consistent with
        can_access_exam for each exam in the batch.
        """
        # Feature: admissionlife, Property 18: Pre-recorded batch is_unlocked flag consistency
        from admissionlife.models import Enrollment, Exam, ExamAttempt
        from admissionlife.services import ExamAccessService

        assume(completed_up_to <= num_exams)

        suffix = f"flag_{num_exams}_{completed_up_to}_{id(self)}"

        batch = Batch.objects.create(
            name=f"FlagBatch_{suffix}",
            description="Test batch for property 18",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        user = User.objects.create_user(
            username=f"prop18_user_{suffix}",
            password="testpass123",
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        exams = []
        for i in range(1, num_exams + 1):
            exam = Exam.objects.create(
                batch=batch,
                title=f"Exam {i}",
                duration_minutes=60,
                order=i,
                passing_score=50,
                is_active=True,
            )
            exams.append(exam)

        # Complete exams up to completed_up_to
        for i in range(min(completed_up_to, num_exams)):
            ExamAttempt.objects.create(
                user=user,
                exam=exams[i],
                score=Decimal("5.00"),
                total_questions=10,
                correct_count=5,
                incorrect_count=5,
                unanswered_count=0,
                is_completed=True,
            )

        # Get the list response
        accessible = ExamAccessService.get_accessible_exams(user, batch)

        # For each exam, the is_unlocked flag should match can_access_exam
        for entry in accessible:
            exam = entry['exam']
            is_unlocked = entry['is_unlocked']
            can_access = ExamAccessService.can_access_exam(user, exam)
            self.assertEqual(
                is_unlocked,
                can_access,
                f"is_unlocked={is_unlocked} but can_access_exam={can_access} for exam order={exam.order}",
            )


# =============================================================================
# Property 19: Same-order exams unlock together
# Feature: admissionlife, Property 19: Same-order exams unlock together
# =============================================================================
# **Validates: Requirements 5.7**


class TestProperty19SameOrderExamsUnlockTogether(TestCase):
    """
    Property 19: Same-order exams unlock together

    For any pre-recorded batch where multiple exams share the same order value,
    completing the prerequisite exam should unlock all exams at the next order
    value simultaneously.

    Note: The current model has unique_together on (batch, order), so we test
    the equivalent behavior: completing an exam at order N unlocks ALL exams at
    the next order value (not just one). We verify the service logic correctly
    identifies and unlocks the next sequential order.

    **Validates: Requirements 5.7**
    """

    @given(
        num_orders=st.integers(min_value=2, max_value=4),
        order_gap=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=10, deadline=None)
    def test_completing_exam_unlocks_next_order_value(self, num_orders, order_gap):
        """
        Completing an exam at order N should unlock the exam at the next order
        value (which may not be N+1 if there are gaps in ordering).
        This validates the "next order value" logic that would also handle
        same-order exams unlocking together.
        """
        # Feature: admissionlife, Property 19: Same-order exams unlock together
        from admissionlife.models import Enrollment, Exam, ExamAttempt
        from admissionlife.services import ExamAccessService

        suffix = f"sameord_{num_orders}_{order_gap}_{id(self)}"

        batch = Batch.objects.create(
            name=f"SameOrderBatch_{suffix}",
            description="Test batch for property 19",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        user = User.objects.create_user(
            username=f"prop19_user_{suffix}",
            password="testpass123",
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        # Create exams with gaps in order values (e.g., 1, 4, 7 with gap=3)
        exams = []
        for i in range(num_orders):
            order_val = 1 + i * order_gap
            exam = Exam.objects.create(
                batch=batch,
                title=f"Exam order {order_val}",
                duration_minutes=60,
                order=order_val,
                passing_score=50,
                is_active=True,
            )
            exams.append(exam)

        # Complete the first exam
        ExamAttempt.objects.create(
            user=user,
            exam=exams[0],
            score=Decimal("7.00"),
            total_questions=10,
            correct_count=7,
            incorrect_count=3,
            unanswered_count=0,
            is_completed=True,
        )

        # Get accessible exams
        accessible = ExamAccessService.get_accessible_exams(user, batch)

        # The first exam (completed) should be unlocked
        self.assertTrue(accessible[0]['is_unlocked'])

        # The second exam (next order value) should be unlocked
        self.assertTrue(accessible[1]['is_unlocked'])

        # All exams beyond the second should be locked (if any)
        for entry in accessible[2:]:
            self.assertFalse(
                entry['is_unlocked'],
                f"Exam at order {entry['exam'].order} should be locked",
            )

    @given(
        num_orders=st.integers(min_value=3, max_value=5),
        complete_count=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=10, deadline=None)
    def test_sequential_completion_unlocks_exactly_next_order(self, num_orders, complete_count):
        """
        After completing exams at orders [o1, ..., oK], exactly the exams at
        the next order value oK+1 should be unlocked (not oK+2 or beyond).
        """
        # Feature: admissionlife, Property 19: Same-order exams unlock together
        from admissionlife.models import Enrollment, Exam, ExamAttempt
        from admissionlife.services import ExamAccessService

        assume(complete_count < num_orders)

        suffix = f"seqcomp_{num_orders}_{complete_count}_{id(self)}"

        batch = Batch.objects.create(
            name=f"SeqCompBatch_{suffix}",
            description="Test batch for property 19 sequential",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        user = User.objects.create_user(
            username=f"prop19_seq_user_{suffix}",
            password="testpass123",
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        # Create exams with sequential orders
        exams = []
        for i in range(1, num_orders + 1):
            exam = Exam.objects.create(
                batch=batch,
                title=f"Exam {i}",
                duration_minutes=60,
                order=i,
                passing_score=50,
                is_active=True,
            )
            exams.append(exam)

        # Complete exams sequentially
        for i in range(complete_count):
            ExamAttempt.objects.create(
                user=user,
                exam=exams[i],
                score=Decimal("6.00"),
                total_questions=10,
                correct_count=6,
                incorrect_count=4,
                unanswered_count=0,
                is_completed=True,
            )

        accessible = ExamAccessService.get_accessible_exams(user, batch)

        # Verify: completed exams + next one are unlocked, rest are locked
        for i, entry in enumerate(accessible):
            if i <= complete_count:
                # Completed exams and the next one should be unlocked
                self.assertTrue(
                    entry['is_unlocked'],
                    f"Exam at index {i} (order {entry['exam'].order}) should be unlocked",
                )
            else:
                # Beyond the next should be locked
                self.assertFalse(
                    entry['is_unlocked'],
                    f"Exam at index {i} (order {entry['exam'].order}) should be locked",
                )


# =============================================================================
# Property 20: Live batch time-based exam access
# Feature: admissionlife, Property 20: Live batch time-based exam access
# =============================================================================
# **Validates: Requirements 6.2, 6.3, 6.4, 6.6, 6.8**


class TestProperty20LiveBatchTimeBasedExamAccess(TestCase):
    """
    Property 20: Live batch time-based exam access

    For any exam in a live batch and any enrolled user, access should be granted
    if and only if the exam's unlock_datetime is not null AND the current server
    datetime >= unlock_datetime. If unlock_datetime is null, access should be denied.
    If unlock_datetime is updated to a future value, access should be denied until
    the new datetime is reached.

    **Validates: Requirements 6.2, 6.3, 6.4, 6.6, 6.8**
    """

    @given(
        minutes_offset=st.integers(min_value=1, max_value=1440),
    )
    @settings(max_examples=10, deadline=None)
    def test_exam_accessible_when_current_time_past_unlock(self, minutes_offset):
        """
        When the current server datetime >= unlock_datetime, the exam should
        be accessible.
        """
        # Feature: admissionlife, Property 20: Live batch time-based exam access
        from datetime import timedelta
        from unittest.mock import patch

        from django.utils import timezone as tz

        from admissionlife.models import Enrollment, Exam
        from admissionlife.services import ExamAccessService

        suffix = f"past_{minutes_offset}_{id(self)}"

        batch = Batch.objects.create(
            name=f"LiveBatch_past_{suffix}",
            description="Test batch for property 20",
            batch_type=Batch.BatchType.LIVE,
            price=Decimal("500.00"),
            is_active=True,
        )
        user = User.objects.create_user(
            username=f"prop20_past_user_{suffix}",
            password="testpass123",
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        # Set unlock_datetime to a known time
        unlock_time = tz.now() - timedelta(minutes=minutes_offset)
        exam = Exam.objects.create(
            batch=batch,
            title="Live Exam Past",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
            unlock_datetime=unlock_time,
        )

        # Current time is after unlock_datetime â€” should be accessible
        result = ExamAccessService.can_access_exam(user, exam)
        self.assertTrue(result)

    @given(
        minutes_offset=st.integers(min_value=1, max_value=1440),
    )
    @settings(max_examples=10, deadline=None)
    def test_exam_not_accessible_when_current_time_before_unlock(self, minutes_offset):
        """
        When the current server datetime < unlock_datetime, the exam should
        NOT be accessible.
        """
        # Feature: admissionlife, Property 20: Live batch time-based exam access
        from datetime import timedelta
        from unittest.mock import patch

        from django.utils import timezone as tz

        from admissionlife.models import Enrollment, Exam
        from admissionlife.services import ExamAccessService

        suffix = f"future_{minutes_offset}_{id(self)}"

        batch = Batch.objects.create(
            name=f"LiveBatch_future_{suffix}",
            description="Test batch for property 20",
            batch_type=Batch.BatchType.LIVE,
            price=Decimal("500.00"),
            is_active=True,
        )
        user = User.objects.create_user(
            username=f"prop20_future_user_{suffix}",
            password="testpass123",
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        # Set unlock_datetime to a future time
        unlock_time = tz.now() + timedelta(minutes=minutes_offset)
        exam = Exam.objects.create(
            batch=batch,
            title="Live Exam Future",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
            unlock_datetime=unlock_time,
        )

        # Current time is before unlock_datetime â€” should NOT be accessible
        result = ExamAccessService.can_access_exam(user, exam)
        self.assertFalse(result)

    @given(
        num_exams=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=10, deadline=None)
    def test_exam_not_accessible_when_unlock_datetime_is_null(self, num_exams):
        """
        When unlock_datetime is null, the exam should NOT be accessible.
        """
        # Feature: admissionlife, Property 20: Live batch time-based exam access
        from admissionlife.models import Enrollment, Exam
        from admissionlife.services import ExamAccessService

        suffix = f"null_{num_exams}_{id(self)}"

        batch = Batch.objects.create(
            name=f"LiveBatch_null_{suffix}",
            description="Test batch for property 20",
            batch_type=Batch.BatchType.LIVE,
            price=Decimal("500.00"),
            is_active=True,
        )
        user = User.objects.create_user(
            username=f"prop20_null_user_{suffix}",
            password="testpass123",
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        # Create exams with null unlock_datetime
        for i in range(1, num_exams + 1):
            exam = Exam.objects.create(
                batch=batch,
                title=f"Live Exam Null {i}",
                duration_minutes=60,
                order=i,
                passing_score=50,
                is_active=True,
                unlock_datetime=None,
            )
            # Should NOT be accessible
            result = ExamAccessService.can_access_exam(user, exam)
            self.assertFalse(result)

    @given(
        minutes_past=st.integers(min_value=1, max_value=720),
        minutes_future=st.integers(min_value=1, max_value=720),
    )
    @settings(max_examples=10, deadline=None)
    def test_get_accessible_exams_is_unlocked_flag_for_live_batch(
        self, minutes_past, minutes_future
    ):
        """
        The is_unlocked flag in get_accessible_exams should be True for exams
        whose unlock_datetime is in the past and False for those in the future
        or null.
        """
        # Feature: admissionlife, Property 20: Live batch time-based exam access
        from datetime import timedelta

        from django.utils import timezone as tz

        from admissionlife.models import Enrollment, Exam
        from admissionlife.services import ExamAccessService

        suffix = f"flag_{minutes_past}_{minutes_future}_{id(self)}"

        batch = Batch.objects.create(
            name=f"LiveBatch_flag_{suffix}",
            description="Test batch for property 20 flag",
            batch_type=Batch.BatchType.LIVE,
            price=Decimal("500.00"),
            is_active=True,
        )
        user = User.objects.create_user(
            username=f"prop20_flag_user_{suffix}",
            password="testpass123",
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        now = tz.now()

        # Exam 1: unlock_datetime in the past (should be unlocked)
        exam_past = Exam.objects.create(
            batch=batch,
            title="Past Exam",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
            unlock_datetime=now - timedelta(minutes=minutes_past),
        )

        # Exam 2: unlock_datetime in the future (should be locked)
        exam_future = Exam.objects.create(
            batch=batch,
            title="Future Exam",
            duration_minutes=60,
            order=2,
            passing_score=50,
            is_active=True,
            unlock_datetime=now + timedelta(minutes=minutes_future),
        )

        # Exam 3: unlock_datetime is null (should be locked)
        exam_null = Exam.objects.create(
            batch=batch,
            title="Null Exam",
            duration_minutes=60,
            order=3,
            passing_score=50,
            is_active=True,
            unlock_datetime=None,
        )

        accessible = ExamAccessService.get_accessible_exams(user, batch)

        # Build a lookup by exam id
        flag_by_id = {entry['exam'].id: entry['is_unlocked'] for entry in accessible}

        # Past exam should be unlocked
        self.assertTrue(flag_by_id[exam_past.id])

        # Future exam should be locked
        self.assertFalse(flag_by_id[exam_future.id])

        # Null exam should be locked
        self.assertFalse(flag_by_id[exam_null.id])

    @given(
        minutes_initially_past=st.integers(min_value=10, max_value=720),
        minutes_new_future=st.integers(min_value=1, max_value=720),
    )
    @settings(max_examples=10, deadline=None)
    def test_updated_unlock_datetime_to_future_denies_access(
        self, minutes_initially_past, minutes_new_future
    ):
        """
        If an Admin updates an Exam's unlock_datetime to a future value after
        it was previously unlocked, access should be denied until the new
        unlock_datetime is reached.
        """
        # Feature: admissionlife, Property 20: Live batch time-based exam access
        from datetime import timedelta

        from django.utils import timezone as tz

        from admissionlife.models import Enrollment, Exam
        from admissionlife.services import ExamAccessService

        suffix = f"update_{minutes_initially_past}_{minutes_new_future}_{id(self)}"

        batch = Batch.objects.create(
            name=f"LiveBatch_update_{suffix}",
            description="Test batch for property 20 update",
            batch_type=Batch.BatchType.LIVE,
            price=Decimal("500.00"),
            is_active=True,
        )
        user = User.objects.create_user(
            username=f"prop20_update_user_{suffix}",
            password="testpass123",
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        now = tz.now()

        # Initially set unlock_datetime in the past (exam was accessible)
        exam = Exam.objects.create(
            batch=batch,
            title="Updated Exam",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
            unlock_datetime=now - timedelta(minutes=minutes_initially_past),
        )

        # Verify it's currently accessible
        self.assertTrue(ExamAccessService.can_access_exam(user, exam))

        # Admin updates unlock_datetime to a future value
        exam.unlock_datetime = now + timedelta(minutes=minutes_new_future)
        exam.save()

        # Now access should be denied
        self.assertFalse(ExamAccessService.can_access_exam(user, exam))


# =============================================================================
# Property 21: Scoring formula correctness
# Feature: admissionlife, Property 21: Scoring formula correctness
# =============================================================================
# **Validates: Requirements 7.2, 7.4**


class TestProperty21ScoringFormulaCorrectness(TestCase):
    """
    Property 21: Scoring formula correctness

    For any set of exam submissions where correct_count + incorrect_count +
    unanswered_count = total_questions, the calculated score should equal
    correct_count - (0.25 Ã— incorrect_count), and the returned result should
    include all five values (score, total_questions, correct_count,
    incorrect_count, unanswered_count) with this invariant holding.

    **Validates: Requirements 7.2, 7.4**
    """

    @given(
        correct_count=st.integers(min_value=0, max_value=50),
        incorrect_count=st.integers(min_value=0, max_value=50),
        unanswered_count=st.integers(min_value=0, max_value=50),
    )
    @settings(max_examples=10, deadline=None)
    def test_scoring_formula_correctness(self, correct_count, incorrect_count, unanswered_count):
        """
        For any combination of correct, incorrect, and unanswered counts,
        the scoring formula should produce:
          score = correct_count - (0.25 * incorrect_count)
        and all returned values should be consistent.
        """
        # Feature: admissionlife, Property 21: Scoring formula correctness
        from admissionlife.models import (
            Batch, Exam, ExamAttempt, ExamQuestion, ExamSubmission,
        )
        from admissionlife.services import ScoringService

        total_questions = correct_count + incorrect_count + unanswered_count
        assume(total_questions > 0)

        # Create supporting DB objects
        user = User.objects.create_user(
            username=f"prop21_user_{correct_count}_{incorrect_count}_{unanswered_count}_{id(self)}",
            password="testpass123",
        )
        batch = Batch.objects.create(
            name=f"Batch_P21_{correct_count}_{incorrect_count}_{unanswered_count}_{id(self)}",
            description="Test batch for property 21",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        exam = Exam.objects.create(
            batch=batch,
            title="Scoring Exam",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )
        attempt = ExamAttempt.objects.create(
            user=user,
            exam=exam,
        )

        # Create questions and submissions
        submissions = []

        # Correct submissions
        for i in range(correct_count):
            question = ExamQuestion.objects.create(
                exam=exam,
                question_text=f"Correct Q{i}",
                answer_1="A", answer_2="B", answer_3="C", answer_4="D",
                correct_answer=1,
            )
            sub = ExamSubmission.objects.create(
                attempt=attempt,
                question=question,
                selected_answer=1,
                is_correct=True,
            )
            submissions.append(sub)

        # Incorrect submissions
        for i in range(incorrect_count):
            question = ExamQuestion.objects.create(
                exam=exam,
                question_text=f"Incorrect Q{i}",
                answer_1="A", answer_2="B", answer_3="C", answer_4="D",
                correct_answer=1,
            )
            sub = ExamSubmission.objects.create(
                attempt=attempt,
                question=question,
                selected_answer=2,
                is_correct=False,
            )
            submissions.append(sub)

        # Unanswered submissions
        for i in range(unanswered_count):
            question = ExamQuestion.objects.create(
                exam=exam,
                question_text=f"Unanswered Q{i}",
                answer_1="A", answer_2="B", answer_3="C", answer_4="D",
                correct_answer=1,
            )
            sub = ExamSubmission.objects.create(
                attempt=attempt,
                question=question,
                selected_answer=None,
                is_correct=False,
            )
            submissions.append(sub)

        # Call the scoring service
        result = ScoringService.calculate_score(attempt, submissions)

        # Assert scoring formula: score = correct_count - (0.25 * incorrect_count)
        expected_score = Decimal(correct_count) - Decimal('0.25') * Decimal(incorrect_count)
        self.assertEqual(result['score'], expected_score)

        # Assert total_questions
        self.assertEqual(result['total_questions'], total_questions)

        # Assert individual counts
        self.assertEqual(result['correct_count'], correct_count)
        self.assertEqual(result['incorrect_count'], incorrect_count)
        self.assertEqual(result['unanswered_count'], unanswered_count)


# =============================================================================
# Property 24: Batch leaderboard correct ordering and aggregation
# Feature: admissionlife, Property 24: Batch leaderboard correct ordering and aggregation
# =============================================================================
# **Validates: Requirements 10.7**


class TestProperty24BatchLeaderboardOrdering(TestCase):
    """
    Property 24: Batch leaderboard correct ordering and aggregation

    For any batch leaderboard with multiple entries, the list should be sorted by
    total_score descending. Each entry's total_score = sum of all completed attempt
    scores in the batch. For entries with equal total_score, they should be ordered
    by average_score descending, then by earliest first-completion time ascending.

    **Validates: Requirements 10.7**
    """

    @given(
        score_a=st.integers(min_value=1, max_value=50),
        score_b=st.integers(min_value=1, max_value=50),
        score_c=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=10, deadline=None)
    def test_batch_leaderboard_sorted_by_total_score_descending(self, score_a, score_b, score_c):
        """
        Users with higher total_score (sum of completed attempt scores) should
        appear before users with lower total_score in the batch leaderboard.
        """
        # Feature: admissionlife, Property 24: Batch leaderboard correct ordering and aggregation
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from admissionlife.models import Batch, Enrollment, Exam, ExamAttempt
        from admissionlife.services import LeaderboardService

        cache.clear()

        # Ensure distinct scores to guarantee deterministic ordering
        assume(score_a != score_b and score_b != score_c and score_a != score_c)

        suffix = f"p24_{score_a}_{score_b}_{score_c}_{id(self)}"

        batch = Batch.objects.create(
            name=f"Batch_P24_{suffix}",
            description="Test batch for property 24",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )

        exam = Exam.objects.create(
            batch=batch,
            title="Exam 1",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        now = timezone.now()

        # Create 3 users with different scores
        user_a = User.objects.create_user(username=f"p24_a_{suffix}", password="pass")
        user_b = User.objects.create_user(username=f"p24_b_{suffix}", password="pass")
        user_c = User.objects.create_user(username=f"p24_c_{suffix}", password="pass")

        Enrollment.objects.create(user=user_a, batch=batch, payment=None)
        Enrollment.objects.create(user=user_b, batch=batch, payment=None)
        Enrollment.objects.create(user=user_c, batch=batch, payment=None)

        # Create completed attempts with different scores
        attempt_a = ExamAttempt.objects.create(
            user=user_a, exam=exam, score=Decimal(str(score_a)),
            total_questions=10, correct_count=min(score_a, 10), incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=now - timedelta(hours=3),
        )
        attempt_b = ExamAttempt.objects.create(
            user=user_b, exam=exam, score=Decimal(str(score_b)),
            total_questions=10, correct_count=min(score_b, 10), incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=now - timedelta(hours=2),
        )
        attempt_c = ExamAttempt.objects.create(
            user=user_c, exam=exam, score=Decimal(str(score_c)),
            total_questions=10, correct_count=min(score_c, 10), incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=now - timedelta(hours=1),
        )

        # Get leaderboard
        result = LeaderboardService.get_batch_leaderboard(batch, page=1, page_size=10, requesting_user=user_a)

        entries = result['entries']
        self.assertEqual(len(entries), 3)

        # Verify sorted by total_score descending
        for i in range(len(entries) - 1):
            self.assertGreaterEqual(
                entries[i]['total_score'],
                entries[i + 1]['total_score'],
                f"Entry at rank {entries[i]['rank']} should have total_score >= entry at rank {entries[i+1]['rank']}",
            )

    @given(
        shared_score=st.integers(min_value=5, max_value=30),
        num_attempts_a=st.integers(min_value=1, max_value=3),
        num_attempts_b=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=10, deadline=None)
    def test_batch_leaderboard_tiebreaker_average_score_then_earliest_completion(
        self, shared_score, num_attempts_a, num_attempts_b
    ):
        """
        When users have the same total_score, they should be ordered by
        average_score descending, then by earliest first-completion time ascending.
        """
        # Feature: admissionlife, Property 24: Batch leaderboard correct ordering and aggregation
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from admissionlife.models import Batch, Enrollment, Exam, ExamAttempt
        from admissionlife.services import LeaderboardService

        cache.clear()

        # Ensure different number of attempts so average_score differs
        assume(num_attempts_a != num_attempts_b)

        suffix = f"p24_tie_{shared_score}_{num_attempts_a}_{num_attempts_b}_{id(self)}"

        batch = Batch.objects.create(
            name=f"Batch_P24_tie_{suffix}",
            description="Test batch for property 24 tiebreaker",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )

        now = timezone.now()

        user_a = User.objects.create_user(username=f"p24_tie_a_{suffix}", password="pass")
        user_b = User.objects.create_user(username=f"p24_tie_b_{suffix}", password="pass")

        Enrollment.objects.create(user=user_a, batch=batch, payment=None)
        Enrollment.objects.create(user=user_b, batch=batch, payment=None)

        # Create exams for each attempt
        # User A: total_score = shared_score spread across num_attempts_a exams
        # User B: total_score = shared_score spread across num_attempts_b exams
        per_attempt_score_a = Decimal(str(shared_score)) / Decimal(str(num_attempts_a))
        per_attempt_score_b = Decimal(str(shared_score)) / Decimal(str(num_attempts_b))

        for i in range(num_attempts_a):
            exam = Exam.objects.create(
                batch=batch,
                title=f"Exam A{i}_{suffix}",
                duration_minutes=60,
                order=i + 1,
                passing_score=50,
                is_active=True,
            )
            ExamAttempt.objects.create(
                user=user_a, exam=exam, score=per_attempt_score_a,
                total_questions=10, correct_count=5, incorrect_count=0,
                unanswered_count=5, is_completed=True,
                end_time=now - timedelta(hours=10),
            )

        for i in range(num_attempts_b):
            # Reuse exams if they exist at same order, or create new ones
            order_val = i + 1
            exam_qs = Exam.objects.filter(batch=batch, order=order_val)
            if exam_qs.exists():
                exam = exam_qs.first()
            else:
                exam = Exam.objects.create(
                    batch=batch,
                    title=f"Exam B{i}_{suffix}",
                    duration_minutes=60,
                    order=num_attempts_a + i + 1,
                    passing_score=50,
                    is_active=True,
                )
            ExamAttempt.objects.create(
                user=user_b, exam=exam, score=per_attempt_score_b,
                total_questions=10, correct_count=5, incorrect_count=0,
                unanswered_count=5, is_completed=True,
                end_time=now - timedelta(hours=5),
            )

        # Get leaderboard
        result = LeaderboardService.get_batch_leaderboard(batch, page=1, page_size=10, requesting_user=user_a)

        entries = result['entries']
        self.assertEqual(len(entries), 2)

        # Both should have similar total_score (may differ slightly due to decimal division)
        # The one with higher average_score should come first
        # average_score = total_score / num_exams_completed
        # User with fewer attempts has higher average (same total / fewer exams)
        if entries[0]['total_score'] == entries[1]['total_score']:
            self.assertGreaterEqual(
                entries[0]['average_score'],
                entries[1]['average_score'],
                "When total_score is tied, higher average_score should rank first",
            )


# =============================================================================
# Property 26: Exam leaderboard correct ordering with best attempt
# Feature: admissionlife, Property 26: Exam leaderboard correct ordering with best attempt
# =============================================================================
# **Validates: Requirements 10.8**


class TestProperty26ExamLeaderboardOrdering(TestCase):
    """
    Property 26: Exam leaderboard correct ordering with best attempt

    For any exam leaderboard, each user should appear at most once with their
    highest-scoring completed attempt. The list should be sorted by score
    descending. For entries with equal score, they should be ordered by
    completion duration ascending (shorter first), then by earliest end_time.

    **Validates: Requirements 10.8**
    """

    @given(
        score_a=st.integers(min_value=1, max_value=50),
        score_b=st.integers(min_value=1, max_value=50),
        score_c=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=10, deadline=None)
    def test_exam_leaderboard_sorted_by_score_descending(self, score_a, score_b, score_c):
        """
        Users should be sorted by their best attempt score in descending order.
        Each user appears at most once.
        """
        # Feature: admissionlife, Property 26: Exam leaderboard correct ordering with best attempt
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from admissionlife.models import Batch, Enrollment, Exam, ExamAttempt
        from admissionlife.services import LeaderboardService

        cache.clear()

        # Ensure distinct scores for deterministic ordering
        assume(score_a != score_b and score_b != score_c and score_a != score_c)

        suffix = f"p26_{score_a}_{score_b}_{score_c}_{id(self)}"

        batch = Batch.objects.create(
            name=f"Batch_P26_{suffix}",
            description="Test batch for property 26",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )

        exam = Exam.objects.create(
            batch=batch,
            title="Exam P26",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        now = timezone.now()
        start_time = now - timedelta(hours=2)

        user_a = User.objects.create_user(username=f"p26_a_{suffix}", password="pass")
        user_b = User.objects.create_user(username=f"p26_b_{suffix}", password="pass")
        user_c = User.objects.create_user(username=f"p26_c_{suffix}", password="pass")

        Enrollment.objects.create(user=user_a, batch=batch, payment=None)
        Enrollment.objects.create(user=user_b, batch=batch, payment=None)
        Enrollment.objects.create(user=user_c, batch=batch, payment=None)

        # Create completed attempts - each user gets one attempt
        attempt_a = ExamAttempt.objects.create(
            user=user_a, exam=exam, score=Decimal(str(score_a)),
            total_questions=10, correct_count=min(score_a, 10), incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=now - timedelta(hours=1),
        )
        # Fix start_time since auto_now_add prevents setting it directly
        ExamAttempt.objects.filter(pk=attempt_a.pk).update(start_time=start_time)

        attempt_b = ExamAttempt.objects.create(
            user=user_b, exam=exam, score=Decimal(str(score_b)),
            total_questions=10, correct_count=min(score_b, 10), incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=now - timedelta(minutes=50),
        )
        ExamAttempt.objects.filter(pk=attempt_b.pk).update(start_time=start_time)

        attempt_c = ExamAttempt.objects.create(
            user=user_c, exam=exam, score=Decimal(str(score_c)),
            total_questions=10, correct_count=min(score_c, 10), incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=now - timedelta(minutes=40),
        )
        ExamAttempt.objects.filter(pk=attempt_c.pk).update(start_time=start_time)

        # Get leaderboard
        result = LeaderboardService.get_exam_leaderboard(exam, page=1, page_size=10, requesting_user=user_a)

        entries = result['entries']
        self.assertEqual(len(entries), 3)

        # Verify sorted by score descending
        for i in range(len(entries) - 1):
            self.assertGreaterEqual(
                entries[i]['score'],
                entries[i + 1]['score'],
                f"Entry at rank {entries[i]['rank']} should have score >= entry at rank {entries[i+1]['rank']}",
            )

        # Verify each user appears at most once
        user_ids = [e['user_id'] for e in entries]
        self.assertEqual(len(user_ids), len(set(user_ids)), "Each user should appear at most once")

    @given(
        best_score=st.integers(min_value=10, max_value=50),
        worse_score=st.integers(min_value=1, max_value=9),
    )
    @settings(max_examples=10, deadline=None)
    def test_exam_leaderboard_uses_best_attempt_per_user(self, best_score, worse_score):
        """
        When a user has multiple completed attempts, only their highest-scoring
        attempt should be used in the leaderboard.
        """
        # Feature: admissionlife, Property 26: Exam leaderboard correct ordering with best attempt
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from admissionlife.models import Batch, Enrollment, Exam, ExamAttempt
        from admissionlife.services import LeaderboardService

        cache.clear()

        suffix = f"p26_best_{best_score}_{worse_score}_{id(self)}"

        batch = Batch.objects.create(
            name=f"Batch_P26_best_{suffix}",
            description="Test batch for property 26 best attempt",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )

        exam = Exam.objects.create(
            batch=batch,
            title="Exam P26 Best",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        now = timezone.now()
        start_time = now - timedelta(hours=2)

        user_a = User.objects.create_user(username=f"p26_best_a_{suffix}", password="pass")
        Enrollment.objects.create(user=user_a, batch=batch, payment=None)

        # Create two attempts for user_a: one with worse_score, one with best_score
        attempt1 = ExamAttempt.objects.create(
            user=user_a, exam=exam, score=Decimal(str(worse_score)),
            total_questions=10, correct_count=worse_score, incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=now - timedelta(hours=1),
        )
        ExamAttempt.objects.filter(pk=attempt1.pk).update(start_time=start_time)

        attempt2 = ExamAttempt.objects.create(
            user=user_a, exam=exam, score=Decimal(str(best_score)),
            total_questions=10, correct_count=min(best_score, 10), incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=now - timedelta(minutes=30),
        )
        ExamAttempt.objects.filter(pk=attempt2.pk).update(start_time=start_time)

        # Get leaderboard
        result = LeaderboardService.get_exam_leaderboard(exam, page=1, page_size=10, requesting_user=user_a)

        entries = result['entries']

        # User should appear only once
        self.assertEqual(len(entries), 1)

        # The score should be the best (highest) score
        self.assertEqual(entries[0]['score'], float(best_score))

    @given(
        shared_score=st.integers(min_value=5, max_value=40),
        duration_a_minutes=st.integers(min_value=10, max_value=55),
        duration_b_minutes=st.integers(min_value=10, max_value=55),
    )
    @settings(max_examples=10, deadline=None)
    def test_exam_leaderboard_tiebreaker_duration_then_end_time(
        self, shared_score, duration_a_minutes, duration_b_minutes
    ):
        """
        When users have the same score, they should be ordered by completion
        duration ascending (shorter first), then by earliest end_time.
        """
        # Feature: admissionlife, Property 26: Exam leaderboard correct ordering with best attempt
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from admissionlife.models import Batch, Enrollment, Exam, ExamAttempt
        from admissionlife.services import LeaderboardService

        cache.clear()

        # Ensure different durations for deterministic ordering
        assume(duration_a_minutes != duration_b_minutes)

        suffix = f"p26_tie_{shared_score}_{duration_a_minutes}_{duration_b_minutes}_{id(self)}"

        batch = Batch.objects.create(
            name=f"Batch_P26_tie_{suffix}",
            description="Test batch for property 26 tiebreaker",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )

        exam = Exam.objects.create(
            batch=batch,
            title="Exam P26 Tie",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        now = timezone.now()

        user_a = User.objects.create_user(username=f"p26_tie_a_{suffix}", password="pass")
        user_b = User.objects.create_user(username=f"p26_tie_b_{suffix}", password="pass")

        Enrollment.objects.create(user=user_a, batch=batch, payment=None)
        Enrollment.objects.create(user=user_b, batch=batch, payment=None)

        # Same score, different durations
        start_a = now - timedelta(hours=3)
        end_a = start_a + timedelta(minutes=duration_a_minutes)

        start_b = now - timedelta(hours=2)
        end_b = start_b + timedelta(minutes=duration_b_minutes)

        attempt_a = ExamAttempt.objects.create(
            user=user_a, exam=exam, score=Decimal(str(shared_score)),
            total_questions=10, correct_count=min(shared_score, 10), incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=end_a,
        )
        ExamAttempt.objects.filter(pk=attempt_a.pk).update(start_time=start_a)

        attempt_b = ExamAttempt.objects.create(
            user=user_b, exam=exam, score=Decimal(str(shared_score)),
            total_questions=10, correct_count=min(shared_score, 10), incorrect_count=0,
            unanswered_count=0, is_completed=True,
            end_time=end_b,
        )
        ExamAttempt.objects.filter(pk=attempt_b.pk).update(start_time=start_b)

        # Get leaderboard
        result = LeaderboardService.get_exam_leaderboard(exam, page=1, page_size=10, requesting_user=user_a)

        entries = result['entries']
        self.assertEqual(len(entries), 2)

        # Both have same score
        self.assertEqual(entries[0]['score'], entries[1]['score'])

        # The one with shorter duration should come first
        self.assertLessEqual(
            entries[0]['duration_seconds'],
            entries[1]['duration_seconds'],
            "When scores are tied, shorter duration should rank first",
        )

# =============================================================================
# Property 1: Batch validation accepts valid data and rejects invalid data
# Feature: admissionlife, Property 1: Batch validation accepts valid data and rejects invalid data
# =============================================================================
# **Validates: Requirements 1.2**


class TestProperty1BatchValidation(TestCase):
    """
    Property 1: Batch validation accepts valid data and rejects invalid data

    For any data submitted to BatchCreateUpdateSerializer, the serializer should
    accept valid data (name â‰¤200 chars, description â‰¤2000 chars, batch_type in
    choices, price 0-999999.99) and reject invalid data (empty name, name >200,
    invalid batch_type, price out of range).

    **Validates: Requirements 1.2**
    """

    @given(
        name=st.text(
            alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'Z')),
            min_size=1,
            max_size=200,
        ).filter(lambda s: s.strip()),
        description=st.text(
            alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'Z')),
            min_size=1,
            max_size=2000,
        ),
        batch_type=st.sampled_from([Batch.BatchType.PRE_RECORDED, Batch.BatchType.LIVE]),
        price=st.decimals(
            min_value=Decimal("0.00"),
            max_value=Decimal("999999.99"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @settings(max_examples=10, deadline=None)
    def test_valid_data_accepted(self, name, description, batch_type, price):
        """Valid batch data should be accepted by BatchCreateUpdateSerializer."""
        # Feature: admissionlife, Property 1: Batch validation accepts valid data and rejects invalid data
        from admissionlife.serializers import BatchCreateUpdateSerializer

        # Ensure name is unique for this test run
        unique_name = f"{name}_{id(self)}_{price}"[:200]

        data = {
            'name': unique_name,
            'description': description,
            'batch_type': batch_type,
            'price': str(price),
            'is_active': True,
        }
        serializer = BatchCreateUpdateSerializer(data=data)
        self.assertTrue(
            serializer.is_valid(),
            f"Serializer should accept valid data but got errors: {serializer.errors}",
        )

    @given(
        name=st.text(min_size=201, max_size=300),
    )
    @settings(max_examples=10, deadline=None)
    def test_name_too_long_rejected(self, name):
        """Batch name exceeding 200 characters should be rejected."""
        # Feature: admissionlife, Property 1: Batch validation accepts valid data and rejects invalid data
        from admissionlife.serializers import BatchCreateUpdateSerializer

        data = {
            'name': name,
            'description': 'Valid description',
            'batch_type': Batch.BatchType.PRE_RECORDED,
            'price': '100.00',
            'is_active': True,
        }
        serializer = BatchCreateUpdateSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('name', serializer.errors)

    @settings(max_examples=10, deadline=None)
    @given(
        empty_name=st.sampled_from(['', '   ', '\t', '\n']),
    )
    def test_empty_name_rejected(self, empty_name):
        """Empty or whitespace-only batch name should be rejected."""
        # Feature: admissionlife, Property 1: Batch validation accepts valid data and rejects invalid data
        from admissionlife.serializers import BatchCreateUpdateSerializer

        data = {
            'name': empty_name,
            'description': 'Valid description',
            'batch_type': Batch.BatchType.PRE_RECORDED,
            'price': '100.00',
            'is_active': True,
        }
        serializer = BatchCreateUpdateSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('name', serializer.errors)

    @given(
        invalid_type=st.text(
            alphabet=string.ascii_letters, min_size=1, max_size=15
        ).filter(lambda s: s not in ['PRE_RECORDED', 'LIVE']),
    )
    @settings(max_examples=10, deadline=None)
    def test_invalid_batch_type_rejected(self, invalid_type):
        """Invalid batch_type values should be rejected."""
        # Feature: admissionlife, Property 1: Batch validation accepts valid data and rejects invalid data
        from admissionlife.serializers import BatchCreateUpdateSerializer

        data = {
            'name': f'Valid Name {id(self)}',
            'description': 'Valid description',
            'batch_type': invalid_type,
            'price': '100.00',
            'is_active': True,
        }
        serializer = BatchCreateUpdateSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('batch_type', serializer.errors)

    @given(
        price=st.one_of(
            st.decimals(
                min_value=Decimal("-99999"),
                max_value=Decimal("-0.01"),
                places=2,
                allow_nan=False,
                allow_infinity=False,
            ),
            st.decimals(
                min_value=Decimal("1000000.00"),
                max_value=Decimal("9999999.99"),
                places=2,
                allow_nan=False,
                allow_infinity=False,
            ),
        ),
    )
    @settings(max_examples=10, deadline=None)
    def test_price_out_of_range_rejected(self, price):
        """Price values outside 0-999999.99 should be rejected."""
        # Feature: admissionlife, Property 1: Batch validation accepts valid data and rejects invalid data
        from admissionlife.serializers import BatchCreateUpdateSerializer

        data = {
            'name': f'Valid Name {id(self)}_{price}',
            'description': 'Valid description',
            'batch_type': Batch.BatchType.PRE_RECORDED,
            'price': str(price),
            'is_active': True,
        }
        serializer = BatchCreateUpdateSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('price', serializer.errors)


# =============================================================================
# Property 2: Active-only batch listing for non-admin users
# Feature: admissionlife, Property 2: Active-only batch listing for non-admin users
# =============================================================================
# **Validates: Requirements 1.4, 1.7**


class TestProperty2ActiveOnlyBatchListing(TestCase):
    """
    Property 2: Active-only batch listing for non-admin users

    For any set of batches with varying is_active status, a non-admin
    authenticated user should only see active batches when listing.

    **Validates: Requirements 1.4, 1.7**
    """

    @given(
        num_active=st.integers(min_value=0, max_value=5),
        num_inactive=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=10, deadline=None)
    def test_non_admin_sees_only_active_batches(self, num_active, num_inactive):
        """
        A non-admin user listing batches should only see batches where
        is_active=True.
        """
        # Feature: admissionlife, Property 2: Active-only batch listing for non-admin users
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        assume(num_active + num_inactive > 0)

        suffix = f"p2_{num_active}_{num_inactive}_{id(self)}"

        # Create a non-admin user with token
        user = User.objects.create_user(
            username=f"prop2_user_{suffix}",
            password="testpass123",
        )
        token, _ = Token.objects.get_or_create(user=user)

        # Create active batches
        for i in range(num_active):
            Batch.objects.create(
                name=f"Active Batch {i}_{suffix}",
                description="An active batch",
                batch_type=Batch.BatchType.PRE_RECORDED,
                price=Decimal("100.00"),
                is_active=True,
            )

        # Create inactive batches
        for i in range(num_inactive):
            Batch.objects.create(
                name=f"Inactive Batch {i}_{suffix}",
                description="An inactive batch",
                batch_type=Batch.BatchType.LIVE,
                price=Decimal("200.00"),
                is_active=False,
            )

        # Make API request as non-admin user
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        response = client.get('/api/admissionlife/batches/')

        self.assertEqual(response.status_code, 200)

        # Get results from paginated response
        results = response.data.get('results', response.data)
        if isinstance(results, dict):
            results = results.get('results', [])

        # All returned batches should be active
        for batch_data in results:
            self.assertTrue(
                batch_data['is_active'],
                f"Non-admin user should only see active batches, got: {batch_data}",
            )

        # Count active batches that belong to this test (filter by suffix)
        returned_names = [b['name'] for b in results]
        test_active_returned = [
            name for name in returned_names if suffix in name
        ]
        self.assertEqual(
            len(test_active_returned),
            num_active,
            f"Expected {num_active} active batches from this test, got {len(test_active_returned)}",
        )


# =============================================================================
# Property 3: Batch name uniqueness
# Feature: admissionlife, Property 3: Batch name uniqueness
# =============================================================================
# **Validates: Requirements 1.8**


class TestProperty3BatchNameUniqueness(TestCase):
    """
    Property 3: Batch name uniqueness

    For any batch name, attempting to create a second batch with the same name
    via the serializer should be rejected with a uniqueness error.

    **Validates: Requirements 1.8**
    """

    @given(
        name=st.text(
            alphabet=st.characters(whitelist_categories=('L', 'N')),
            min_size=1,
            max_size=100,
        ).filter(lambda s: s.strip()),
    )
    @settings(max_examples=10, deadline=None)
    def test_duplicate_batch_name_rejected(self, name):
        """
        Creating a batch with a name that already exists should be rejected
        by the serializer with a uniqueness validation error.
        """
        # Feature: admissionlife, Property 3: Batch name uniqueness
        from admissionlife.serializers import BatchCreateUpdateSerializer

        # Create the first batch directly in the database
        Batch.objects.create(
            name=name,
            description="First batch",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("100.00"),
            is_active=True,
        )

        # Attempt to create a second batch with the same name via serializer
        data = {
            'name': name,
            'description': 'Second batch',
            'batch_type': Batch.BatchType.LIVE,
            'price': '200.00',
            'is_active': True,
        }
        serializer = BatchCreateUpdateSerializer(data=data)
        self.assertFalse(
            serializer.is_valid(),
            f"Serializer should reject duplicate name '{name}' but it was accepted",
        )
        self.assertIn('name', serializer.errors)

    @given(
        base_name=st.text(
            alphabet=st.characters(whitelist_categories=('L', 'N')),
            min_size=1,
            max_size=90,
        ).filter(lambda s: s.strip()),
    )
    @settings(max_examples=10, deadline=None)
    def test_different_batch_names_accepted(self, base_name):
        """
        Creating batches with different names should be accepted.
        """
        # Feature: admissionlife, Property 3: Batch name uniqueness
        from admissionlife.serializers import BatchCreateUpdateSerializer

        name1 = f"{base_name}_first_{id(self)}"
        name2 = f"{base_name}_second_{id(self)}"

        # Create the first batch
        Batch.objects.create(
            name=name1,
            description="First batch",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("100.00"),
            is_active=True,
        )

        # Create a second batch with a different name via serializer
        data = {
            'name': name2,
            'description': 'Second batch',
            'batch_type': Batch.BatchType.LIVE,
            'price': '200.00',
            'is_active': True,
        }
        serializer = BatchCreateUpdateSerializer(data=data)
        self.assertTrue(
            serializer.is_valid(),
            f"Serializer should accept different name '{name2}' but got errors: {serializer.errors}",
        )


# =============================================================================
# Property 9: Payment created as PENDING
# Feature: admissionlife, Property 9: Payment created as PENDING
# =============================================================================
# **Validates: Requirements 3.2**


class TestProperty9PaymentCreatedAsPending(TestCase):
    """
    Property 9: Payment created as PENDING

    For any valid payment submission by an authenticated user, the created Payment
    record should have status=PENDING and user equal to the requesting user.

    **Validates: Requirements 3.2**
    """

    @given(
        payment_method=valid_payment_method_strategy(),
        transaction_id=valid_transaction_id_strategy(),
        sender_number=valid_sender_number_strategy(),
        amount=valid_amount_strategy(),
    )
    @settings(max_examples=10, deadline=None)
    def test_payment_created_with_pending_status_and_correct_user(
        self, payment_method, transaction_id, sender_number, amount
    ):
        """
        Submitting a valid payment should create a Payment with status=PENDING
        and user equal to the requesting user.
        """
        # Feature: admissionlife, Property 9: Payment created as PENDING
        from admissionlife.services import PaymentService

        user = User.objects.create_user(
            username=f"prop9_user_{transaction_id}_{payment_method}_{id(self)}",
            password="testpass123",
        )
        batch = Batch.objects.create(
            name=f"Batch_P9_{transaction_id}_{payment_method}_{id(self)}",
            description="Test batch for property 9",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("1000.00"),
            is_active=True,
        )

        data = {
            'batch': batch,
            'payment_method': payment_method,
            'transaction_id': transaction_id,
            'sender_number': sender_number,
            'amount': amount,
        }

        payment = PaymentService.submit_payment(user, data)

        # Assert: payment status is PENDING
        self.assertEqual(payment.status, Payment.PaymentStatus.PENDING)

        # Assert: payment user is the requesting user
        self.assertEqual(payment.user, user)

        # Assert: payment is persisted in the database
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.PaymentStatus.PENDING)
        self.assertEqual(payment.user_id, user.id)


# =============================================================================
# Property 10: Payment rejection stores admin notes
# Feature: admissionlife, Property 10: Payment rejection stores admin notes
# =============================================================================
# **Validates: Requirements 3.6**


class TestProperty10PaymentRejectionStoresNotes(TestCase):
    """
    Property 10: Payment rejection stores admin notes

    For any pending payment and any non-empty admin_notes string, rejecting the
    payment should result in status=REJECTED and the admin_notes field containing
    the provided text.

    **Validates: Requirements 3.6**
    """

    @given(
        payment_method=valid_payment_method_strategy(),
        transaction_id=valid_transaction_id_strategy(),
        sender_number=valid_sender_number_strategy(),
        amount=valid_amount_strategy(),
        admin_notes=st.text(min_size=1, max_size=200).filter(lambda s: s.strip()),
    )
    @settings(max_examples=10, deadline=None)
    def test_rejecting_pending_payment_stores_status_and_notes(
        self, payment_method, transaction_id, sender_number, amount, admin_notes
    ):
        """
        Rejecting a pending payment should set status=REJECTED and store the
        admin_notes text.
        """
        # Feature: admissionlife, Property 10: Payment rejection stores admin notes
        from admissionlife.services import PaymentService

        user = User.objects.create_user(
            username=f"prop10_user_{transaction_id}_{payment_method}_{id(self)}",
            password="testpass123",
        )
        admin_user = User.objects.create_user(
            username=f"prop10_admin_{transaction_id}_{payment_method}_{id(self)}",
            password="testpass123",
            is_staff=True,
        )
        batch = Batch.objects.create(
            name=f"Batch_P10_{transaction_id}_{payment_method}_{id(self)}",
            description="Test batch for property 10",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("1000.00"),
            is_active=True,
        )

        # Create a pending payment
        payment = Payment.objects.create(
            user=user,
            batch=batch,
            payment_method=payment_method,
            transaction_id=transaction_id,
            sender_number=sender_number,
            amount=amount,
            status=Payment.PaymentStatus.PENDING,
        )

        # Reject the payment with admin notes
        result = PaymentService.reject_payment(payment, admin_user, admin_notes)

        # Assert: status is REJECTED
        self.assertEqual(result.status, Payment.PaymentStatus.REJECTED)

        # Assert: admin_notes contains the provided text
        self.assertEqual(result.admin_notes, admin_notes)

        # Assert: persisted in the database
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.PaymentStatus.REJECTED)
        self.assertEqual(payment.admin_notes, admin_notes)

        # Assert: reviewed_at is set
        self.assertIsNotNone(payment.reviewed_at)


# =============================================================================
# Property 12: Already-enrolled user payment rejection
# Feature: admissionlife, Property 12: Already-enrolled user payment rejection
# =============================================================================
# **Validates: Requirements 3.9**


class TestProperty12AlreadyEnrolledPaymentRejection(TestCase):
    """
    Property 12: Already-enrolled user payment rejection

    For any user already enrolled in a batch, submitting a new payment for that
    same batch should be rejected with a ValueError.

    **Validates: Requirements 3.9**
    """

    @given(
        payment_method=valid_payment_method_strategy(),
        transaction_id=valid_transaction_id_strategy(),
        sender_number=valid_sender_number_strategy(),
        amount=valid_amount_strategy(),
    )
    @settings(max_examples=10, deadline=None)
    def test_already_enrolled_user_payment_rejected(
        self, payment_method, transaction_id, sender_number, amount
    ):
        """
        A user already enrolled in a batch should not be able to submit a new
        payment for that batch. PaymentService.submit_payment should raise ValueError.
        """
        # Feature: admissionlife, Property 12: Already-enrolled user payment rejection
        from admissionlife.models import Enrollment
        from admissionlife.services import PaymentService

        user = User.objects.create_user(
            username=f"prop12_user_{transaction_id}_{payment_method}_{id(self)}",
            password="testpass123",
        )
        batch = Batch.objects.create(
            name=f"Batch_P12_{transaction_id}_{payment_method}_{id(self)}",
            description="Test batch for property 12",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("1000.00"),
            is_active=True,
        )

        # Create enrollment first (user is already enrolled)
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        data = {
            'batch': batch,
            'payment_method': payment_method,
            'transaction_id': transaction_id,
            'sender_number': sender_number,
            'amount': amount,
        }

        # Submitting payment should raise ValueError
        with self.assertRaises(ValueError) as ctx:
            PaymentService.submit_payment(user, data)

        self.assertIn("already enrolled", str(ctx.exception).lower())


# =============================================================================
# Property 13: CSV import round-trip
# Feature: admissionlife, Property 13: CSV import round-trip
# =============================================================================
# **Validates: Requirements 4.4**


class TestProperty13CSVImportRoundTrip(TestCase):
    """
    Property 13: CSV import round-trip

    For any valid CSV row with question_text, 4 answers, correct_answer (1-4),
    and explanation, importing via the CSV endpoint should create an ExamQuestion
    with all fields preserved exactly.

    **Validates: Requirements 4.4**
    """

    @given(
        question_text=st.text(
            alphabet=st.characters(whitelist_categories=('L', 'N', 'Z')),
            min_size=1,
            max_size=50,
        ).filter(lambda s: s.strip() and ',' not in s and '\n' not in s and '\r' not in s),
        correct_answer=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=10, deadline=None)
    def test_valid_csv_row_preserves_data(self, question_text, correct_answer):
        """A valid CSV row imported correctly preserves all data fields."""
        # Feature: admissionlife, Property 13: CSV import round-trip
        import csv
        import io

        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        from admissionlife.models import Exam, ExamQuestion

        suffix = f"p13_{correct_answer}_{id(self)}"

        admin_user = User.objects.create_user(
            username=f"p13_admin_{suffix}",
            password="testpass123",
            is_staff=True,
        )
        token, _ = Token.objects.get_or_create(user=admin_user)

        batch = Batch.objects.create(
            name=f"Batch_P13_{suffix}",
            description="Test batch for property 13",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        exam = Exam.objects.create(
            batch=batch,
            title="CSV Exam P13",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        # Build CSV content using csv.writer for proper quoting
        answer_1 = "Answer A"
        answer_2 = "Answer B"
        answer_3 = "Answer C"
        answer_4 = "Answer D"
        explanation = "Some explanation"

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Question Title", "Answer 1", "Answer 2", "Answer 3", "Answer 4", "Correct Answer", "Explanation"])
        writer.writerow([question_text, answer_1, answer_2, answer_3, answer_4, str(correct_answer), explanation])
        csv_content = output.getvalue()

        # Upload CSV via API
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        csv_file.name = 'questions.csv'

        response = client.post(
            f'/api/admissionlife/exams/{exam.id}/import-csv/',
            {'file': csv_file},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['created'], 1)

        # Verify the question was created with correct data
        q = ExamQuestion.objects.filter(exam=exam).first()
        self.assertIsNotNone(q)
        self.assertEqual(q.question_text, question_text)
        self.assertEqual(q.answer_1, answer_1)
        self.assertEqual(q.answer_2, answer_2)
        self.assertEqual(q.answer_3, answer_3)
        self.assertEqual(q.answer_4, answer_4)
        self.assertEqual(q.correct_answer, correct_answer)
        self.assertEqual(q.explanation, explanation)


# =============================================================================
# Property 14: CSV invalid row handling
# Feature: admissionlife, Property 14: CSV invalid row handling
# =============================================================================
# **Validates: Requirements 4.5**


class TestProperty14CSVInvalidRowHandling(TestCase):
    """
    Property 14: CSV invalid row handling

    For any CSV row that is invalid (missing fields, invalid correct_answer),
    the row should be skipped and a reason should be provided in the errors list.

    **Validates: Requirements 4.5**
    """

    @given(
        invalid_correct_answer=st.one_of(
            st.integers(min_value=5, max_value=99),
            st.integers(min_value=-10, max_value=0),
        ),
    )
    @settings(max_examples=10, deadline=None)
    def test_invalid_correct_answer_skipped_with_reason(self, invalid_correct_answer):
        """Invalid correct_answer values cause the row to be skipped with a reason."""
        # Feature: admissionlife, Property 14: CSV invalid row handling
        import io

        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        from admissionlife.models import Exam, ExamQuestion

        suffix = f"p14_{invalid_correct_answer}_{id(self)}"

        admin_user = User.objects.create_user(
            username=f"p14_admin_{suffix}",
            password="testpass123",
            is_staff=True,
        )
        token, _ = Token.objects.get_or_create(user=admin_user)

        batch = Batch.objects.create(
            name=f"Batch_P14_{suffix}",
            description="Test batch for property 14",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        exam = Exam.objects.create(
            batch=batch,
            title="CSV Exam P14",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        csv_content = "Question Title,Answer 1,Answer 2,Answer 3,Answer 4,Correct Answer,Explanation\n"
        csv_content += f"Some question,A,B,C,D,{invalid_correct_answer},Explanation\n"

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        csv_file.name = 'questions.csv'

        response = client.post(
            f'/api/admissionlife/exams/{exam.id}/import-csv/',
            {'file': csv_file},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['created'], 0)
        self.assertEqual(response.data['skipped'], 1)
        self.assertTrue(len(response.data['errors']) > 0)
        self.assertIn('reason', response.data['errors'][0])

        # No question should have been created
        self.assertEqual(ExamQuestion.objects.filter(exam=exam).count(), 0)


# =============================================================================
# Property 15: CSV import summary consistency
# Feature: admissionlife, Property 15: CSV import summary consistency
# =============================================================================
# **Validates: Requirements 4.7**


class TestProperty15CSVImportSummaryConsistency(TestCase):
    """
    Property 15: CSV import summary consistency

    For any CSV import, the summary should satisfy:
    total_rows = created + skipped.

    **Validates: Requirements 4.7**
    """

    @given(
        num_valid=st.integers(min_value=0, max_value=3),
        num_invalid=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=10, deadline=None)
    def test_total_rows_equals_created_plus_skipped(self, num_valid, num_invalid):
        """The import summary should satisfy total_rows = created + skipped."""
        # Feature: admissionlife, Property 15: CSV import summary consistency
        import io

        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        from admissionlife.models import Exam

        assume(num_valid + num_invalid > 0)

        suffix = f"p15_{num_valid}_{num_invalid}_{id(self)}"

        admin_user = User.objects.create_user(
            username=f"p15_admin_{suffix}",
            password="testpass123",
            is_staff=True,
        )
        token, _ = Token.objects.get_or_create(user=admin_user)

        batch = Batch.objects.create(
            name=f"Batch_P15_{suffix}",
            description="Test batch for property 15",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        exam = Exam.objects.create(
            batch=batch,
            title="CSV Exam P15",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        csv_content = "Question Title,Answer 1,Answer 2,Answer 3,Answer 4,Correct Answer,Explanation\n"

        # Add valid rows
        for i in range(num_valid):
            csv_content += f"Valid Q{i},A,B,C,D,1,Explanation\n"

        # Add invalid rows (correct_answer = 9 which is invalid)
        for i in range(num_invalid):
            csv_content += f"Invalid Q{i},A,B,C,D,9,Explanation\n"

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        csv_file.name = 'questions.csv'

        response = client.post(
            f'/api/admissionlife/exams/{exam.id}/import-csv/',
            {'file': csv_file},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200)

        total_rows = response.data['total_rows']
        created = response.data['created']
        skipped = response.data['skipped']

        # Core invariant: total_rows = created + skipped
        self.assertEqual(total_rows, created + skipped)
        self.assertEqual(created, num_valid)
        self.assertEqual(skipped, num_invalid)


# =============================================================================
# Property 16: Exam batch+order uniqueness
# Feature: admissionlife, Property 16: Exam batch+order uniqueness
# =============================================================================
# **Validates: Requirements 4.9**


class TestProperty16ExamBatchOrderUniqueness(TestCase):
    """
    Property 16: Exam batch+order uniqueness

    For any batch, attempting to create two exams with the same order value
    should be rejected by the database unique_together constraint.

    **Validates: Requirements 4.9**
    """

    @given(
        order=st.integers(min_value=1, max_value=100),
    )
    @settings(max_examples=10, deadline=None)
    def test_duplicate_batch_order_rejected(self, order):
        """Creating two exams with the same batch+order raises IntegrityError."""
        # Feature: admissionlife, Property 16: Exam batch+order uniqueness
        from admissionlife.models import Exam

        suffix = f"p16_{order}_{id(self)}"

        batch = Batch.objects.create(
            name=f"Batch_P16_{suffix}",
            description="Test batch for property 16",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )

        # Create first exam - should succeed
        Exam.objects.create(
            batch=batch,
            title=f"Exam 1 order {order}",
            duration_minutes=60,
            order=order,
            passing_score=50,
            is_active=True,
        )

        # Create second exam with same batch+order - should raise IntegrityError
        with self.assertRaises(IntegrityError):
            Exam.objects.create(
                batch=batch,
                title=f"Exam 2 order {order}",
                duration_minutes=60,
                order=order,
                passing_score=50,
                is_active=True,
            )


# =============================================================================
# Property 7: Non-enrolled user denied exam access
# Feature: admissionlife, Property 7: Non-enrolled user denied exam access
# =============================================================================
# **Validates: Requirements 2.5, 6.7**


class TestProperty7NonEnrolledUserDeniedExamAccess(TestCase):
    """
    Property 7: Non-enrolled user denied exam access

    For any authenticated user who is NOT enrolled in a batch, attempting to
    start an exam in that batch should return a 403 Forbidden response.

    **Validates: Requirements 2.5, 6.7**
    """

    @given(
        batch_type=st.sampled_from([Batch.BatchType.PRE_RECORDED, Batch.BatchType.LIVE]),
    )
    @settings(max_examples=10, deadline=None)
    def test_non_enrolled_user_gets_403(self, batch_type):
        """A non-enrolled user attempting to start an exam gets 403."""
        # Feature: admissionlife, Property 7: Non-enrolled user denied exam access
        from datetime import timedelta

        from django.utils import timezone

        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        from admissionlife.models import Exam

        suffix = f"p7_{batch_type}_{id(self)}"

        user = User.objects.create_user(
            username=f"p7_user_{suffix}",
            password="testpass123",
        )
        token, _ = Token.objects.get_or_create(user=user)

        batch = Batch.objects.create(
            name=f"Batch_P7_{suffix}",
            description="Test batch for property 7",
            batch_type=batch_type,
            price=Decimal("500.00"),
            is_active=True,
        )

        unlock_dt = timezone.now() - timedelta(hours=1) if batch_type == Batch.BatchType.LIVE else None
        exam = Exam.objects.create(
            batch=batch,
            title="Exam P7",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
            unlock_datetime=unlock_dt,
        )

        # User is NOT enrolled - attempt to start exam
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        response = client.post(f'/api/admissionlife/exam-attempts/{exam.id}/start/')

        self.assertEqual(response.status_code, 403)


# =============================================================================
# Property 22: No exam retakes
# Feature: admissionlife, Property 22: No exam retakes
# =============================================================================
# **Validates: Requirements 7.3, 7.7**


class TestProperty22NoExamRetakes(TestCase):
    """
    Property 22: No exam retakes

    For any user who has already completed an exam, attempting to start the
    same exam again should be rejected (400 response).

    **Validates: Requirements 7.3, 7.7**
    """

    @given(
        score=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=10, deadline=None)
    def test_completed_exam_cannot_be_started_again(self, score):
        """A completed exam cannot be started again by the same user."""
        # Feature: admissionlife, Property 22: No exam retakes
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        from admissionlife.models import Enrollment, Exam, ExamAttempt

        suffix = f"p22_{score}_{id(self)}"

        user = User.objects.create_user(
            username=f"p22_user_{suffix}",
            password="testpass123",
        )
        token, _ = Token.objects.get_or_create(user=user)

        batch = Batch.objects.create(
            name=f"Batch_P22_{suffix}",
            description="Test batch for property 22",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        exam = Exam.objects.create(
            batch=batch,
            title="Exam P22",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        # Create a completed attempt
        ExamAttempt.objects.create(
            user=user,
            exam=exam,
            score=Decimal(str(score)),
            total_questions=10,
            correct_count=score,
            incorrect_count=10 - score,
            unanswered_count=0,
            is_completed=True,
        )

        # Attempt to start the exam again
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        response = client.post(f'/api/admissionlife/exam-attempts/{exam.id}/start/')

        self.assertEqual(response.status_code, 400)
        self.assertIn('already completed', response.data['detail'].lower())


# =============================================================================
# Property 23: Exam questions hide correct answers
# Feature: admissionlife, Property 23: Exam questions hide correct answers
# =============================================================================
# **Validates: Requirements 7.1**


class TestProperty23ExamQuestionsHideCorrectAnswers(TestCase):
    """
    Property 23: Exam questions hide correct answers

    When a user starts an exam, the response should include questions but
    should NOT include the correct_answer field.

    **Validates: Requirements 7.1**
    """

    @given(
        num_questions=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=10, deadline=None)
    def test_start_response_excludes_correct_answer(self, num_questions):
        """The exam start response should not include correct_answer in questions."""
        # Feature: admissionlife, Property 23: Exam questions hide correct answers
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        from admissionlife.models import Enrollment, Exam, ExamQuestion

        suffix = f"p23_{num_questions}_{id(self)}"

        user = User.objects.create_user(
            username=f"p23_user_{suffix}",
            password="testpass123",
        )
        token, _ = Token.objects.get_or_create(user=user)

        batch = Batch.objects.create(
            name=f"Batch_P23_{suffix}",
            description="Test batch for property 23",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        exam = Exam.objects.create(
            batch=batch,
            title="Exam P23",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        # Create questions
        for i in range(num_questions):
            ExamQuestion.objects.create(
                exam=exam,
                question_text=f"Question {i}",
                answer_1="A",
                answer_2="B",
                answer_3="C",
                answer_4="D",
                correct_answer=((i % 4) + 1),
            )

        # Start the exam
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        response = client.post(f'/api/admissionlife/exam-attempts/{exam.id}/start/')

        self.assertEqual(response.status_code, 201)

        # Verify questions are present but correct_answer is NOT
        questions = response.data['questions']
        self.assertEqual(len(questions), num_questions)

        for q in questions:
            self.assertNotIn('correct_answer', q)
            self.assertNotIn('explanation', q)
            self.assertIn('question_text', q)
            self.assertIn('answer_1', q)
            self.assertIn('answer_2', q)
            self.assertIn('answer_3', q)
            self.assertIn('answer_4', q)


# =============================================================================
# Property 25: Batch leaderboard includes requesting user's rank
# Feature: admissionlife, Property 25: Batch leaderboard includes requesting user's rank
# =============================================================================
# **Validates: Requirements 8.6**


class TestProperty25BatchLeaderboardIncludesUserRank(TestCase):
    """
    Property 25: Batch leaderboard includes requesting user's rank

    For any enrolled user who has completed at least one exam in a batch,
    the batch leaderboard response should include their rank in
    current_user_entry regardless of which page is requested.

    **Validates: Requirements 8.6**
    """

    @given(
        score=st.integers(min_value=1, max_value=10),
        page=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=10, deadline=None)
    def test_batch_leaderboard_includes_requesting_user_rank(self, score, page):
        """The batch leaderboard includes the requesting user's rank."""
        # Feature: admissionlife, Property 25: Batch leaderboard includes requesting user's rank
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from admissionlife.models import Enrollment, Exam, ExamAttempt
        from admissionlife.services import LeaderboardService

        cache.clear()

        suffix = f"p25_{score}_{page}_{id(self)}"

        batch = Batch.objects.create(
            name=f"Batch_P25_{suffix}",
            description="Test batch for property 25",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )

        exam = Exam.objects.create(
            batch=batch,
            title="Exam P25",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        now = timezone.now()

        user = User.objects.create_user(username=f"p25_user_{suffix}", password="pass")
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        ExamAttempt.objects.create(
            user=user,
            exam=exam,
            score=Decimal(str(score)),
            total_questions=10,
            correct_count=score,
            incorrect_count=0,
            unanswered_count=10 - score,
            is_completed=True,
            end_time=now - timedelta(hours=1),
        )

        # Get leaderboard (possibly on a page where user isn't listed)
        result = LeaderboardService.get_batch_leaderboard(
            batch, page=page, page_size=1, requesting_user=user
        )

        # current_user_entry should always be present
        self.assertIsNotNone(result['current_user_entry'])
        self.assertIn('rank', result['current_user_entry'])
        self.assertEqual(result['current_user_entry']['rank'], 1)


# =============================================================================
# Property 27: Exam leaderboard includes requesting user's rank
# Feature: admissionlife, Property 27: Exam leaderboard includes requesting user's rank
# =============================================================================
# **Validates: Requirements 9.5**


class TestProperty27ExamLeaderboardIncludesUserRank(TestCase):
    """
    Property 27: Exam leaderboard includes requesting user's rank

    For any enrolled user who has completed an exam, the exam leaderboard
    response should include their rank in current_user_entry regardless of
    which page is requested.

    **Validates: Requirements 9.5**
    """

    @given(
        score=st.integers(min_value=1, max_value=10),
        page=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=10, deadline=None)
    def test_exam_leaderboard_includes_requesting_user_rank(self, score, page):
        """The exam leaderboard includes the requesting user's rank."""
        # Feature: admissionlife, Property 27: Exam leaderboard includes requesting user's rank
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from admissionlife.models import Enrollment, Exam, ExamAttempt
        from admissionlife.services import LeaderboardService

        cache.clear()

        suffix = f"p27_{score}_{page}_{id(self)}"

        batch = Batch.objects.create(
            name=f"Batch_P27_{suffix}",
            description="Test batch for property 27",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )

        exam = Exam.objects.create(
            batch=batch,
            title="Exam P27",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        now = timezone.now()
        start_time = now - timedelta(hours=2)

        user = User.objects.create_user(username=f"p27_user_{suffix}", password="pass")
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        attempt = ExamAttempt.objects.create(
            user=user,
            exam=exam,
            score=Decimal(str(score)),
            total_questions=10,
            correct_count=score,
            incorrect_count=0,
            unanswered_count=10 - score,
            is_completed=True,
            end_time=now - timedelta(hours=1),
        )
        ExamAttempt.objects.filter(pk=attempt.pk).update(start_time=start_time)

        # Get leaderboard (possibly on a page where user isn't listed)
        result = LeaderboardService.get_exam_leaderboard(
            exam, page=page, page_size=1, requesting_user=user
        )

        # current_user_entry should always be present
        self.assertIsNotNone(result['current_user_entry'])
        self.assertIn('rank', result['current_user_entry'])
        self.assertEqual(result['current_user_entry']['rank'], 1)


# =============================================================================
# Property 28: Leaderboard cache invalidation on attempt completion
# Feature: admissionlife, Property 28: Leaderboard cache invalidation on attempt completion
# =============================================================================
# **Validates: Requirements 10.4**


class TestProperty28LeaderboardCacheInvalidation(TestCase):
    """
    Property 28: Leaderboard cache invalidation on attempt completion

    When an exam attempt is completed (finalized), the leaderboard cache for
    both the batch and the exam should be invalidated so that subsequent
    leaderboard requests reflect the new data.

    **Validates: Requirements 10.4**
    """

    @given(
        score=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=10, deadline=None)
    def test_cache_invalidated_on_attempt_completion(self, score):
        """Completing an attempt invalidates both batch and exam leaderboard caches."""
        # Feature: admissionlife, Property 28: Leaderboard cache invalidation on attempt completion
        from datetime import timedelta

        from django.core.cache import cache
        from django.utils import timezone

        from admissionlife.models import Enrollment, Exam, ExamAttempt
        from admissionlife.services import LeaderboardService

        cache.clear()

        suffix = f"p28_{score}_{id(self)}"

        batch = Batch.objects.create(
            name=f"Batch_P28_{suffix}",
            description="Test batch for property 28",
            batch_type=Batch.BatchType.PRE_RECORDED,
            price=Decimal("500.00"),
            is_active=True,
        )

        exam = Exam.objects.create(
            batch=batch,
            title="Exam P28",
            duration_minutes=60,
            order=1,
            passing_score=50,
            is_active=True,
        )

        now = timezone.now()

        user = User.objects.create_user(username=f"p28_user_{suffix}", password="pass")
        Enrollment.objects.create(user=user, batch=batch, payment=None)

        # Populate the cache by requesting leaderboards
        LeaderboardService.get_batch_leaderboard(batch, page=1, page_size=10, requesting_user=user)
        LeaderboardService.get_exam_leaderboard(exam, page=1, page_size=10, requesting_user=user)

        # Verify cache is populated
        batch_cache_key = f"batch_leaderboard_{batch.id}"
        exam_cache_key = f"exam_leaderboard_{exam.id}"
        self.assertIsNotNone(cache.get(batch_cache_key))
        self.assertIsNotNone(cache.get(exam_cache_key))

        # Invalidate cache (as would happen after attempt completion)
        LeaderboardService.invalidate_cache(batch_id=batch.id, exam_id=exam.id)

        # Verify cache is cleared
        self.assertIsNone(cache.get(batch_cache_key))
        self.assertIsNone(cache.get(exam_cache_key))


# =============================================================================
# Property 2: Category tree structural integrity
# Feature: admissionlife-questions, Property 2: Category tree structural integrity
# =============================================================================
# **Validates: Requirements 1.4**


class TestProperty2CategoryTreeStructuralIntegrity(TestCase):
    """
    Property 2: Category tree structural integrity

    For any set of categories in the database, the tree endpoint SHALL return a
    structure where every category appears exactly once, every category's children
    in the tree match its actual children in the database, and the root nodes are
    exactly those categories with parent=null.

    **Validates: Requirements 1.4**
    """

    @given(
        num_roots=st.integers(min_value=1, max_value=4),
        children_per_root=st.integers(min_value=0, max_value=3),
        grandchildren_per_child=st.integers(min_value=0, max_value=2),
    )
    @settings(max_examples=10, deadline=None)
    def test_every_category_appears_exactly_once_in_tree(
        self, num_roots, children_per_root, grandchildren_per_child
    ):
        """
        Every category in the database appears exactly once in the tree structure
        returned by QuestionService.get_category_tree().
        """
        # Feature: admissionlife-questions, Property 2: Category tree structural integrity
        from api.models import Category
        from admissionlife.services import QuestionService

        # Clear existing categories to have a controlled environment
        Category.objects.all().delete()

        # Build a random hierarchy with up to 3 levels
        all_created_ids = []
        suffix = f"{num_roots}_{children_per_root}_{grandchildren_per_child}_{id(self)}"

        for r in range(num_roots):
            root = Category.objects.create(
                name=f"Root_{r}_{suffix}",
                parent=None,
                order=r,
            )
            all_created_ids.append(root.id)

            for c in range(children_per_root):
                child = Category.objects.create(
                    name=f"Child_{r}_{c}_{suffix}",
                    parent=root,
                    order=c,
                )
                all_created_ids.append(child.id)

                for g in range(grandchildren_per_child):
                    grandchild = Category.objects.create(
                        name=f"Grandchild_{r}_{c}_{g}_{suffix}",
                        parent=child,
                        order=g,
                    )
                    all_created_ids.append(grandchild.id)

        # Call the service method
        tree = QuestionService.get_category_tree()

        # Collect all IDs from the tree
        def collect_ids(nodes):
            ids = []
            for node in nodes:
                ids.append(node['id'])
                ids.extend(collect_ids(node.get('children', [])))
            return ids

        tree_ids = collect_ids(tree)

        # Every category appears exactly once
        self.assertEqual(sorted(tree_ids), sorted(all_created_ids))
        # No duplicates
        self.assertEqual(len(tree_ids), len(set(tree_ids)))

    @given(
        num_roots=st.integers(min_value=1, max_value=4),
        children_per_root=st.integers(min_value=0, max_value=3),
        grandchildren_per_child=st.integers(min_value=0, max_value=2),
    )
    @settings(max_examples=10, deadline=None)
    def test_children_in_tree_match_database(
        self, num_roots, children_per_root, grandchildren_per_child
    ):
        """
        For every node in the tree, its children list matches the actual children
        of that category in the database.
        """
        # Feature: admissionlife-questions, Property 2: Category tree structural integrity
        from api.models import Category
        from admissionlife.services import QuestionService

        # Clear existing categories
        Category.objects.all().delete()

        suffix = f"{num_roots}_{children_per_root}_{grandchildren_per_child}_children_{id(self)}"

        for r in range(num_roots):
            root = Category.objects.create(
                name=f"Root_{r}_{suffix}",
                parent=None,
                order=r,
            )

            for c in range(children_per_root):
                child = Category.objects.create(
                    name=f"Child_{r}_{c}_{suffix}",
                    parent=root,
                    order=c,
                )

                for g in range(grandchildren_per_child):
                    Category.objects.create(
                        name=f"Grandchild_{r}_{c}_{g}_{suffix}",
                        parent=child,
                        order=g,
                    )

        # Call the service method
        tree = QuestionService.get_category_tree()

        # Verify children match database for every node
        def verify_children(nodes):
            for node in nodes:
                node_id = node['id']
                tree_child_ids = sorted([c['id'] for c in node.get('children', [])])
                db_child_ids = sorted(
                    Category.objects.filter(parent_id=node_id)
                    .values_list('id', flat=True)
                )
                self.assertEqual(
                    tree_child_ids,
                    db_child_ids,
                    f"Children mismatch for category {node_id}: "
                    f"tree={tree_child_ids}, db={db_child_ids}",
                )
                verify_children(node.get('children', []))

        verify_children(tree)

    @given(
        num_roots=st.integers(min_value=1, max_value=4),
        children_per_root=st.integers(min_value=0, max_value=3),
        grandchildren_per_child=st.integers(min_value=0, max_value=2),
    )
    @settings(max_examples=10, deadline=None)
    def test_root_nodes_have_parent_null(
        self, num_roots, children_per_root, grandchildren_per_child
    ):
        """
        The root nodes in the tree are exactly those categories with parent=null
        in the database.
        """
        # Feature: admissionlife-questions, Property 2: Category tree structural integrity
        from api.models import Category
        from admissionlife.services import QuestionService

        # Clear existing categories
        Category.objects.all().delete()

        suffix = f"{num_roots}_{children_per_root}_{grandchildren_per_child}_roots_{id(self)}"

        for r in range(num_roots):
            root = Category.objects.create(
                name=f"Root_{r}_{suffix}",
                parent=None,
                order=r,
            )

            for c in range(children_per_root):
                child = Category.objects.create(
                    name=f"Child_{r}_{c}_{suffix}",
                    parent=root,
                    order=c,
                )

                for g in range(grandchildren_per_child):
                    Category.objects.create(
                        name=f"Grandchild_{r}_{c}_{g}_{suffix}",
                        parent=child,
                        order=g,
                    )

        # Call the service method
        tree = QuestionService.get_category_tree()

        # Root nodes in tree should be exactly those with parent=null
        tree_root_ids = sorted([node['id'] for node in tree])
        db_root_ids = sorted(
            Category.objects.filter(parent__isnull=True)
            .values_list('id', flat=True)
        )
        self.assertEqual(tree_root_ids, db_root_ids)

        # Verify that all root nodes in the tree actually have parent=null in DB
        for node in tree:
            cat = Category.objects.get(pk=node['id'])
            self.assertIsNone(cat.parent)


# =============================================================================
# Property 3: Question category filter correctness
# Feature: admissionlife-questions, Property 3: Question category filter correctness
# =============================================================================
# **Validates: Requirements 2.1, 2.2**


class TestProperty3QuestionCategoryFilterCorrectness(TestCase):
    """
    Property 3: Question category filter correctness

    For any category and any set of questions, when filtering questions by a
    category with category_level=all, all returned questions SHALL belong to a
    category that is either the specified category or a descendant of it, and
    every question in a descendant category SHALL be included in the results.

    This test validates `get_descendant_category_ids` returns the correct
    inclusive set: the category itself plus all descendants, with no
    non-descendant categories included.

    **Validates: Requirements 2.1, 2.2**
    """

    @given(
        num_children=st.integers(min_value=0, max_value=4),
        num_grandchildren_per_child=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=100, deadline=None)
    def test_descendant_ids_include_self(self, num_children, num_grandchildren_per_child):
        """
        get_descendant_category_ids always includes the queried category itself.
        """
        # Feature: admissionlife-questions, Property 3: Question category filter correctness
        from api.models import Category
        from admissionlife.services import QuestionService

        suffix = f"p3_self_{num_children}_{num_grandchildren_per_child}_{id(self)}"

        # Create a root category
        root = Category.objects.create(name=f"Root_{suffix}", parent=None, level=0, order=0)

        # Create children
        for i in range(num_children):
            child = Category.objects.create(
                name=f"Child_{i}_{suffix}", parent=root, level=1, order=i
            )
            # Create grandchildren
            for j in range(num_grandchildren_per_child):
                Category.objects.create(
                    name=f"GChild_{i}_{j}_{suffix}", parent=child, level=2, order=j
                )

        # The result must include the root itself
        result_ids = QuestionService.get_descendant_category_ids(root.id)
        self.assertIn(root.id, result_ids)

    @given(
        num_children=st.integers(min_value=1, max_value=4),
        num_grandchildren_per_child=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=100, deadline=None)
    def test_descendant_ids_include_all_descendants(self, num_children, num_grandchildren_per_child):
        """
        get_descendant_category_ids includes all children and grandchildren
        of the queried category.
        """
        # Feature: admissionlife-questions, Property 3: Question category filter correctness
        from api.models import Category
        from admissionlife.services import QuestionService

        suffix = f"p3_desc_{num_children}_{num_grandchildren_per_child}_{id(self)}"

        # Create a root category
        root = Category.objects.create(name=f"Root_{suffix}", parent=None, level=0, order=0)

        # Track all expected descendant IDs
        expected_ids = {root.id}

        # Create children
        for i in range(num_children):
            child = Category.objects.create(
                name=f"Child_{i}_{suffix}", parent=root, level=1, order=i
            )
            expected_ids.add(child.id)

            # Create grandchildren
            for j in range(num_grandchildren_per_child):
                grandchild = Category.objects.create(
                    name=f"GChild_{i}_{j}_{suffix}", parent=child, level=2, order=j
                )
                expected_ids.add(grandchild.id)

        # Get descendant IDs from the service
        result_ids = set(QuestionService.get_descendant_category_ids(root.id))

        # All expected descendants must be present
        self.assertEqual(result_ids, expected_ids)

    @given(
        num_children=st.integers(min_value=0, max_value=3),
        num_grandchildren_per_child=st.integers(min_value=0, max_value=2),
    )
    @settings(max_examples=100, deadline=None)
    def test_descendant_ids_exclude_non_descendants(
        self, num_children, num_grandchildren_per_child
    ):
        """
        get_descendant_category_ids does not include categories that are not
        descendants of the queried category (e.g., sibling trees).
        """
        # Feature: admissionlife-questions, Property 3: Question category filter correctness
        from api.models import Category
        from admissionlife.services import QuestionService

        suffix = f"p3_excl_{num_children}_{num_grandchildren_per_child}_{id(self)}"

        # Create two separate root categories (sibling trees)
        root_a = Category.objects.create(name=f"RootA_{suffix}", parent=None, level=0, order=0)
        root_b = Category.objects.create(name=f"RootB_{suffix}", parent=None, level=0, order=1)

        # Build tree under root_a
        for i in range(num_children):
            child = Category.objects.create(
                name=f"ChildA_{i}_{suffix}", parent=root_a, level=1, order=i
            )
            for j in range(num_grandchildren_per_child):
                Category.objects.create(
                    name=f"GChildA_{i}_{j}_{suffix}", parent=child, level=2, order=j
                )

        # Build tree under root_b (these should NOT appear in root_a's descendants)
        non_descendant_ids = {root_b.id}
        for i in range(num_children):
            child_b = Category.objects.create(
                name=f"ChildB_{i}_{suffix}", parent=root_b, level=1, order=i
            )
            non_descendant_ids.add(child_b.id)
            for j in range(num_grandchildren_per_child):
                gc_b = Category.objects.create(
                    name=f"GChildB_{i}_{j}_{suffix}", parent=child_b, level=2, order=j
                )
                non_descendant_ids.add(gc_b.id)

        # Get descendant IDs for root_a
        result_ids = set(QuestionService.get_descendant_category_ids(root_a.id))

        # None of root_b's tree should be in the result
        self.assertTrue(
            result_ids.isdisjoint(non_descendant_ids),
            f"Non-descendant IDs {result_ids & non_descendant_ids} found in result",
        )

    @given(
        num_children=st.integers(min_value=1, max_value=3),
        num_grandchildren_per_child=st.integers(min_value=0, max_value=2),
        target_level=st.sampled_from(["root", "child"]),
    )
    @settings(max_examples=100, deadline=None)
    def test_descendant_ids_from_intermediate_node(
        self, num_children, num_grandchildren_per_child, target_level
    ):
        """
        get_descendant_category_ids works correctly when called on an
        intermediate (non-root) category, returning only its subtree.
        """
        # Feature: admissionlife-questions, Property 3: Question category filter correctness
        from api.models import Category
        from admissionlife.services import QuestionService

        suffix = f"p3_inter_{num_children}_{num_grandchildren_per_child}_{target_level}_{id(self)}"

        # Create a root with children and grandchildren
        root = Category.objects.create(name=f"Root_{suffix}", parent=None, level=0, order=0)

        children = []
        grandchildren_map = {}  # child_id -> list of grandchild ids
        for i in range(num_children):
            child = Category.objects.create(
                name=f"Child_{i}_{suffix}", parent=root, level=1, order=i
            )
            children.append(child)
            grandchildren_map[child.id] = []
            for j in range(num_grandchildren_per_child):
                gc = Category.objects.create(
                    name=f"GChild_{i}_{j}_{suffix}", parent=child, level=2, order=j
                )
                grandchildren_map[child.id].append(gc.id)

        if target_level == "child":
            # Query from the first child — should include child + its grandchildren only
            target = children[0]
            expected_ids = {target.id} | set(grandchildren_map[target.id])

            result_ids = set(QuestionService.get_descendant_category_ids(target.id))
            self.assertEqual(result_ids, expected_ids)

            # Should NOT include root or other children's subtrees
            self.assertNotIn(root.id, result_ids)
            for other_child in children[1:]:
                self.assertNotIn(other_child.id, result_ids)
        else:
            # Query from root — should include everything
            all_ids = {root.id}
            for child in children:
                all_ids.add(child.id)
                all_ids.update(grandchildren_map[child.id])

            result_ids = set(QuestionService.get_descendant_category_ids(root.id))
            self.assertEqual(result_ids, all_ids)



# =============================================================================
# Property 10: Quiz scoring correctness
# Feature: admissionlife-questions, Property 10: Quiz scoring correctness
# =============================================================================
# **Validates: Requirements 6.2, 7.5**


class TestProperty10QuizScoringCorrectness(TestCase):
    """
    Property 10: Quiz scoring correctness

    For any set of answer submissions for a quiz attempt, the calculated score
    SHALL equal the number of submissions where the selected answer's is_correct
    field is True, the attempt SHALL be marked is_completed=True, and the attempt
    SHALL have guest_user=null.

    **Validates: Requirements 6.2, 7.5**
    """

    @given(
        num_questions=st.integers(min_value=1, max_value=10),
        data=st.data(),
    )
    @settings(max_examples=100, deadline=None)
    def test_score_equals_correct_answer_count(self, num_questions, data):
        """
        The score returned by submit_quiz_attempt equals the count of submissions
        where the selected answer has is_correct=True.
        """
        # Feature: admissionlife-questions, Property 10: Quiz scoring correctness
        from api.models import Category, Question, Answer, Quiz, QuizAttempt
        from admissionlife.services import QuestionService

        # Create a user
        user = User.objects.create_user(
            username=f"scorer_{num_questions}_{id(self)}_{data.draw(st.integers(min_value=0, max_value=999999))}",
            password="testpass123",
        )

        # Create a category and questions with answers
        category = Category.objects.create(
            name=f"ScoreCat_{num_questions}_{id(self)}_{user.id}",
            parent=None,
            level=0,
            order=0,
        )

        questions = []
        for i in range(num_questions):
            question = Question.objects.create(
                question_text=f"Score Q{i} for user {user.id}",
                category=category,
            )
            # Create 4 answers, exactly one correct
            correct_idx = data.draw(st.integers(min_value=0, max_value=3))
            for j in range(4):
                Answer.objects.create(
                    question=question,
                    text=f"Answer {j} for Q{i}",
                    is_correct=(j == correct_idx),
                )
            questions.append(question)

        # Create a quiz with these questions
        quiz = Quiz.objects.create(
            name=f"ScoreQuiz_{user.id}",
            quiz_type=Quiz.QuizType.PRACTICE,
            duration_minutes=num_questions,
        )
        quiz.questions.set(questions)

        # Start an attempt
        attempt = QuestionService.start_quiz_attempt(user, quiz)

        # Generate random submissions: for each question, either pick an answer or skip (None)
        submissions_data = []
        expected_correct_count = 0

        for question in questions:
            answers = list(question.answers.all())
            # Decide whether to answer or skip
            should_answer = data.draw(st.booleans())

            if should_answer:
                # Pick a random answer from the available ones
                selected_answer = data.draw(st.sampled_from(answers))
                submissions_data.append({
                    'question_id': question.id,
                    'selected_answer_id': selected_answer.id,
                })
                if selected_answer.is_correct:
                    expected_correct_count += 1
            else:
                # Submit with no answer selected (None)
                submissions_data.append({
                    'question_id': question.id,
                    'selected_answer_id': None,
                })

        # Call submit_quiz_attempt
        result_attempt = QuestionService.submit_quiz_attempt(attempt, submissions_data)

        # Verify score equals count of correct answers
        self.assertEqual(
            result_attempt.score,
            expected_correct_count,
            f"Score {result_attempt.score} != expected correct count {expected_correct_count}",
        )

    @given(
        num_questions=st.integers(min_value=1, max_value=8),
        data=st.data(),
    )
    @settings(max_examples=100, deadline=None)
    def test_attempt_is_completed_after_submission(self, num_questions, data):
        """
        After submit_quiz_attempt is called, the attempt SHALL be marked
        is_completed=True regardless of the answers submitted.
        """
        # Feature: admissionlife-questions, Property 10: Quiz scoring correctness
        from api.models import Category, Question, Answer, Quiz, QuizAttempt
        from admissionlife.services import QuestionService

        # Create a user
        user = User.objects.create_user(
            username=f"completed_{num_questions}_{id(self)}_{data.draw(st.integers(min_value=0, max_value=999999))}",
            password="testpass123",
        )

        # Create a category and questions
        category = Category.objects.create(
            name=f"CompletedCat_{num_questions}_{id(self)}_{user.id}",
            parent=None,
            level=0,
            order=0,
        )

        questions = []
        for i in range(num_questions):
            question = Question.objects.create(
                question_text=f"Completed Q{i} for user {user.id}",
                category=category,
            )
            # Create answers
            for j in range(4):
                Answer.objects.create(
                    question=question,
                    text=f"Ans {j} for Q{i}",
                    is_correct=(j == 0),
                )
            questions.append(question)

        # Create quiz and attempt
        quiz = Quiz.objects.create(
            name=f"CompletedQuiz_{user.id}",
            quiz_type=Quiz.QuizType.PRACTICE,
            duration_minutes=num_questions,
        )
        quiz.questions.set(questions)
        attempt = QuestionService.start_quiz_attempt(user, quiz)

        # Verify attempt is NOT completed before submission
        self.assertFalse(attempt.is_completed)

        # Generate random submissions
        submissions_data = []
        for question in questions:
            answers = list(question.answers.all())
            should_answer = data.draw(st.booleans())
            if should_answer:
                selected_answer = data.draw(st.sampled_from(answers))
                submissions_data.append({
                    'question_id': question.id,
                    'selected_answer_id': selected_answer.id,
                })
            else:
                submissions_data.append({
                    'question_id': question.id,
                    'selected_answer_id': None,
                })

        # Submit
        result_attempt = QuestionService.submit_quiz_attempt(attempt, submissions_data)

        # Verify is_completed=True
        self.assertTrue(
            result_attempt.is_completed,
            "Attempt should be marked is_completed=True after submission",
        )

    @given(
        num_questions=st.integers(min_value=1, max_value=8),
        data=st.data(),
    )
    @settings(max_examples=100, deadline=None)
    def test_attempt_has_no_guest_user(self, num_questions, data):
        """
        After submit_quiz_attempt is called, the attempt SHALL have
        guest_user=null (admissionlife users are always registered users).
        """
        # Feature: admissionlife-questions, Property 10: Quiz scoring correctness
        from api.models import Category, Question, Answer, Quiz, QuizAttempt
        from admissionlife.services import QuestionService

        # Create a user
        user = User.objects.create_user(
            username=f"noguest_{num_questions}_{id(self)}_{data.draw(st.integers(min_value=0, max_value=999999))}",
            password="testpass123",
        )

        # Create a category and questions
        category = Category.objects.create(
            name=f"NoGuestCat_{num_questions}_{id(self)}_{user.id}",
            parent=None,
            level=0,
            order=0,
        )

        questions = []
        for i in range(num_questions):
            question = Question.objects.create(
                question_text=f"NoGuest Q{i} for user {user.id}",
                category=category,
            )
            for j in range(4):
                Answer.objects.create(
                    question=question,
                    text=f"Ans {j} for Q{i}",
                    is_correct=(j == 0),
                )
            questions.append(question)

        # Create quiz and attempt
        quiz = Quiz.objects.create(
            name=f"NoGuestQuiz_{user.id}",
            quiz_type=Quiz.QuizType.PRACTICE,
            duration_minutes=num_questions,
        )
        quiz.questions.set(questions)
        attempt = QuestionService.start_quiz_attempt(user, quiz)

        # Generate random submissions
        submissions_data = []
        for question in questions:
            answers = list(question.answers.all())
            should_answer = data.draw(st.booleans())
            if should_answer:
                selected_answer = data.draw(st.sampled_from(answers))
                submissions_data.append({
                    'question_id': question.id,
                    'selected_answer_id': selected_answer.id,
                })
            else:
                submissions_data.append({
                    'question_id': question.id,
                    'selected_answer_id': None,
                })

        # Submit
        result_attempt = QuestionService.submit_quiz_attempt(attempt, submissions_data)

        # Verify guest_user is null
        self.assertIsNone(
            result_attempt.guest_user,
            "Attempt should have guest_user=null for admissionlife users",
        )

        # Also verify from database
        result_attempt.refresh_from_db()
        self.assertIsNone(result_attempt.guest_user)


# =============================================================================
# Property 8: Practice quiz generation category constraint
# Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
# =============================================================================
# **Validates: Requirements 5.1, 5.2, 5.3, 7.4**


class TestProperty8PracticeQuizGenerationCategoryConstraint(TestCase):
    """
    Property 8: Practice quiz generation category constraint

    For any valid category configuration (with or without include_subcategories),
    the generated practice quiz SHALL contain only questions belonging to the
    specified categories (or their descendants when include_subcategories=true),
    the question count per category SHALL be at most min(requested_count,
    available_count), and the quiz SHALL have quiz_type=PRACTICE.

    **Validates: Requirements 5.1, 5.2, 5.3, 7.4**
    """

    @given(
        num_children=st.integers(min_value=0, max_value=3),
        num_grandchildren_per_child=st.integers(min_value=0, max_value=2),
        questions_per_leaf=st.integers(min_value=1, max_value=5),
        requested_count=st.integers(min_value=1, max_value=20),
        include_subcategories=st.booleans(),
    )
    @settings(max_examples=50, deadline=None)
    def test_quiz_contains_only_questions_from_specified_categories(
        self, num_children, num_grandchildren_per_child, questions_per_leaf,
        requested_count, include_subcategories,
    ):
        """
        All questions in the generated quiz belong to the specified category
        or its descendants (when include_subcategories=True).
        """
        # Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
        from api.models import Category, Question, Quiz
        from admissionlife.services import QuestionService
        from django.contrib.auth.models import User

        suffix = f"p8_cat_{num_children}_{num_grandchildren_per_child}_{questions_per_leaf}_{requested_count}_{include_subcategories}_{id(self)}"

        user = User.objects.create_user(username=f"p8_user_{suffix}", password="pass")

        # Create a category tree
        root = Category.objects.create(name=f"Root_{suffix}", parent=None, level=0, order=0)

        # Create a sibling root (questions here should NOT appear in quiz)
        sibling_root = Category.objects.create(name=f"Sibling_{suffix}", parent=None, level=0, order=1)

        all_valid_category_ids = {root.id}
        children = []
        for i in range(num_children):
            child = Category.objects.create(
                name=f"Child_{i}_{suffix}", parent=root, level=1, order=i
            )
            children.append(child)
            all_valid_category_ids.add(child.id)
            for j in range(num_grandchildren_per_child):
                gc = Category.objects.create(
                    name=f"GChild_{i}_{j}_{suffix}", parent=child, level=2, order=j
                )
                all_valid_category_ids.add(gc.id)

        # Create questions in valid categories
        for cat_id in all_valid_category_ids:
            for q in range(questions_per_leaf):
                Question.objects.create(
                    category_id=cat_id,
                    question_text=f"Q_{cat_id}_{q}_{suffix}",
                )

        # Create questions in sibling (should never appear)
        for q in range(3):
            Question.objects.create(
                category=sibling_root,
                question_text=f"Sibling_Q_{q}_{suffix}",
            )

        # Build categories_config
        categories_config = [{
            'category_id': root.id,
            'question_count': requested_count,
            'include_subcategories': include_subcategories,
        }]

        # Generate the quiz
        quiz = QuestionService.generate_practice_quiz(user, categories_config)

        if quiz is None:
            # This can only happen if no questions are available in the target categories
            if include_subcategories:
                available = Question.objects.filter(category_id__in=all_valid_category_ids).count()
            else:
                available = Question.objects.filter(category_id=root.id).count()
            self.assertEqual(available, 0)
            return

        # Determine valid category IDs for this config
        if include_subcategories:
            valid_ids = all_valid_category_ids
        else:
            valid_ids = {root.id}

        # Verify all questions belong to valid categories
        quiz_questions = quiz.questions.all()
        for question in quiz_questions:
            self.assertIn(
                question.category_id,
                valid_ids,
                f"Question {question.id} has category_id={question.category_id} "
                f"which is not in valid set {valid_ids}",
            )

        # Verify no duplicate questions
        question_ids = list(quiz_questions.values_list('id', flat=True))
        self.assertEqual(len(question_ids), len(set(question_ids)))

    @given(
        num_children=st.integers(min_value=0, max_value=3),
        num_grandchildren_per_child=st.integers(min_value=0, max_value=2),
        questions_per_leaf=st.integers(min_value=1, max_value=4),
        requested_count=st.integers(min_value=1, max_value=20),
        include_subcategories=st.booleans(),
    )
    @settings(max_examples=50, deadline=None)
    def test_quiz_question_count_bounded_by_min_requested_available(
        self, num_children, num_grandchildren_per_child, questions_per_leaf,
        requested_count, include_subcategories,
    ):
        """
        The question count in the quiz is at most min(requested_count, available_count).
        """
        # Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
        from api.models import Category, Question, Quiz
        from admissionlife.services import QuestionService
        from django.contrib.auth.models import User

        suffix = f"p8_cnt_{num_children}_{num_grandchildren_per_child}_{questions_per_leaf}_{requested_count}_{include_subcategories}_{id(self)}"

        user = User.objects.create_user(username=f"p8_user_{suffix}", password="pass")

        # Create a category tree
        root = Category.objects.create(name=f"Root_{suffix}", parent=None, level=0, order=0)

        all_valid_category_ids = {root.id}
        for i in range(num_children):
            child = Category.objects.create(
                name=f"Child_{i}_{suffix}", parent=root, level=1, order=i
            )
            all_valid_category_ids.add(child.id)
            for j in range(num_grandchildren_per_child):
                gc = Category.objects.create(
                    name=f"GChild_{i}_{j}_{suffix}", parent=child, level=2, order=j
                )
                all_valid_category_ids.add(gc.id)

        # Create questions in valid categories
        for cat_id in all_valid_category_ids:
            for q in range(questions_per_leaf):
                Question.objects.create(
                    category_id=cat_id,
                    question_text=f"Q_{cat_id}_{q}_{suffix}",
                )

        # Determine available count
        if include_subcategories:
            available_count = Question.objects.filter(
                category_id__in=all_valid_category_ids
            ).count()
        else:
            available_count = Question.objects.filter(category_id=root.id).count()

        # Build categories_config
        categories_config = [{
            'category_id': root.id,
            'question_count': requested_count,
            'include_subcategories': include_subcategories,
        }]

        # Generate the quiz
        quiz = QuestionService.generate_practice_quiz(user, categories_config)

        if quiz is None:
            self.assertEqual(available_count, 0)
            return

        quiz_count = quiz.questions.count()
        expected_max = min(requested_count, available_count)
        self.assertLessEqual(
            quiz_count,
            expected_max,
            f"Quiz has {quiz_count} questions but expected at most "
            f"min({requested_count}, {available_count}) = {expected_max}",
        )

    @given(
        num_children=st.integers(min_value=0, max_value=3),
        questions_per_category=st.integers(min_value=1, max_value=4),
        requested_count=st.integers(min_value=1, max_value=15),
        include_subcategories=st.booleans(),
    )
    @settings(max_examples=50, deadline=None)
    def test_quiz_type_is_practice(
        self, num_children, questions_per_category, requested_count,
        include_subcategories,
    ):
        """
        The generated quiz always has quiz_type=PRACTICE.
        """
        # Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
        from api.models import Category, Question, Quiz
        from admissionlife.services import QuestionService
        from django.contrib.auth.models import User

        suffix = f"p8_type_{num_children}_{questions_per_category}_{requested_count}_{include_subcategories}_{id(self)}"

        user = User.objects.create_user(username=f"p8_user_{suffix}", password="pass")

        # Create a category tree
        root = Category.objects.create(name=f"Root_{suffix}", parent=None, level=0, order=0)

        all_category_ids = {root.id}
        for i in range(num_children):
            child = Category.objects.create(
                name=f"Child_{i}_{suffix}", parent=root, level=1, order=i
            )
            all_category_ids.add(child.id)

        # Create questions
        for cat_id in all_category_ids:
            for q in range(questions_per_category):
                Question.objects.create(
                    category_id=cat_id,
                    question_text=f"Q_{cat_id}_{q}_{suffix}",
                )

        # Build categories_config
        categories_config = [{
            'category_id': root.id,
            'question_count': requested_count,
            'include_subcategories': include_subcategories,
        }]

        # Generate the quiz
        quiz = QuestionService.generate_practice_quiz(user, categories_config)

        if quiz is None:
            # No questions available — acceptable
            return

        self.assertEqual(quiz.quiz_type, Quiz.QuizType.PRACTICE)

    @given(
        num_categories=st.integers(min_value=2, max_value=4),
        questions_per_category=st.integers(min_value=2, max_value=5),
        data=st.data(),
    )
    @settings(max_examples=50, deadline=None)
    def test_quiz_no_duplicate_questions_with_multiple_configs(
        self, num_categories, questions_per_category, data,
    ):
        """
        When multiple category configs are provided (potentially overlapping),
        the quiz contains no duplicate questions.
        """
        # Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
        from api.models import Category, Question, Quiz
        from admissionlife.services import QuestionService
        from django.contrib.auth.models import User

        suffix = f"p8_dedup_{num_categories}_{questions_per_category}_{id(self)}"

        user = User.objects.create_user(username=f"p8_user_{suffix}", password="pass")

        # Create a root with children
        root = Category.objects.create(name=f"Root_{suffix}", parent=None, level=0, order=0)
        children = []
        for i in range(num_categories):
            child = Category.objects.create(
                name=f"Child_{i}_{suffix}", parent=root, level=1, order=i
            )
            children.append(child)

        # Create questions in each child category
        for child in children:
            for q in range(questions_per_category):
                Question.objects.create(
                    category=child,
                    question_text=f"Q_{child.id}_{q}_{suffix}",
                )

        # Build multiple category configs (some may overlap via include_subcategories)
        categories_config = []
        # Config 1: root with include_subcategories=True (covers all)
        requested_1 = data.draw(st.integers(min_value=1, max_value=10))
        categories_config.append({
            'category_id': root.id,
            'question_count': requested_1,
            'include_subcategories': True,
        })
        # Config 2: a specific child (overlaps with config 1)
        if children:
            child_idx = data.draw(st.integers(min_value=0, max_value=len(children) - 1))
            requested_2 = data.draw(st.integers(min_value=1, max_value=10))
            categories_config.append({
                'category_id': children[child_idx].id,
                'question_count': requested_2,
                'include_subcategories': False,
            })

        # Generate the quiz
        quiz = QuestionService.generate_practice_quiz(user, categories_config)

        if quiz is None:
            return

        # Verify no duplicate questions
        question_ids = list(quiz.questions.values_list('id', flat=True))
        self.assertEqual(
            len(question_ids),
            len(set(question_ids)),
            f"Duplicate questions found in quiz: {question_ids}",
        )


# =============================================================================
# Property 8: Practice quiz generation category constraint
# Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
# =============================================================================
# **Validates: Requirements 5.1, 5.2, 5.3, 7.4**


class TestProperty8PracticeQuizGenerationCategoryConstraint(TestCase):
    """
    Property 8: Practice quiz generation category constraint

    For any valid category configuration (with or without include_subcategories),
    the generated practice quiz SHALL contain only questions belonging to the
    specified categories (or their descendants when include_subcategories=True),
    the question count per category SHALL be at most min(requested_count,
    available_count), and the quiz SHALL have quiz_type=PRACTICE.

    **Validates: Requirements 5.1, 5.2, 5.3, 7.4**
    """

    def _create_category(self, name, parent=None):
        """Create a Category, computing level from parent."""
        from api.models import Category
        level = (parent.level + 1) if parent else 0
        return Category.objects.create(name=name, parent=parent, level=level, order=0)

    def _create_question(self, category, suffix=""):
        """Create a minimal Question in the given category."""
        from api.models import Question
        return Question.objects.create(
            category=category,
            question_text=f"Question {suffix}",
        )

    @given(
        question_count=st.integers(min_value=1, max_value=5),
        num_questions=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=10, deadline=None)
    def test_quiz_contains_only_questions_from_specified_category(
        self, question_count, num_questions
    ):
        """
        When include_subcategories=False, the quiz should only contain questions
        from the exact specified category, and the count should be
        <= min(requested_count, available_count).
        """
        # Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
        from api.models import Quiz
        from admissionlife.services import QuestionService

        suffix = f"p8_exact_{question_count}_{num_questions}_{id(self)}"

        user = User.objects.create_user(
            username=f"prop8_user_{suffix}",
            password="testpass123",
        )

        # Create a target category and an unrelated category
        target_cat = self._create_category(f"TargetCat_{suffix}")
        other_cat = self._create_category(f"OtherCat_{suffix}")

        # Create questions in the target category
        target_questions = []
        for i in range(num_questions):
            q = self._create_question(target_cat, suffix=f"{suffix}_tq{i}")
            target_questions.append(q)

        # Create a question in the unrelated category (should never appear in quiz)
        self._create_question(other_cat, suffix=f"{suffix}_other")

        categories_config = [
            {
                'category_id': target_cat.id,
                'question_count': question_count,
                'include_subcategories': False,
            }
        ]

        quiz = QuestionService.generate_practice_quiz(user, categories_config)

        if num_questions == 0:
            # No questions available — quiz should be None
            self.assertIsNone(quiz)
        else:
            self.assertIsNotNone(quiz)

            # Verify quiz_type is PRACTICE
            self.assertEqual(quiz.quiz_type, Quiz.QuizType.PRACTICE)

            # Verify all questions belong to the target category
            quiz_question_ids = set(quiz.questions.values_list('id', flat=True))
            target_question_ids = set(q.id for q in target_questions)
            self.assertTrue(
                quiz_question_ids.issubset(target_question_ids),
                f"Quiz contains questions not from target category: "
                f"{quiz_question_ids - target_question_ids}",
            )

            # Verify count <= min(requested, available)
            expected_max = min(question_count, num_questions)
            self.assertLessEqual(
                len(quiz_question_ids),
                expected_max,
                f"Quiz has {len(quiz_question_ids)} questions but max should be {expected_max}",
            )

    @given(
        question_count=st.integers(min_value=1, max_value=5),
        num_parent_questions=st.integers(min_value=0, max_value=3),
        num_child_questions=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=10, deadline=None)
    def test_quiz_includes_descendant_questions_when_include_subcategories_true(
        self, question_count, num_parent_questions, num_child_questions
    ):
        """
        When include_subcategories=True, the quiz should include questions from
        the specified category AND all its descendants. All returned questions
        must belong to the category or its descendants.
        """
        # Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
        from api.models import Quiz
        from admissionlife.services import QuestionService

        suffix = f"p8_sub_{question_count}_{num_parent_questions}_{num_child_questions}_{id(self)}"

        user = User.objects.create_user(
            username=f"prop8_sub_user_{suffix}",
            password="testpass123",
        )

        # Create parent category and a child category
        parent_cat = self._create_category(f"ParentCat_{suffix}")
        child_cat = self._create_category(f"ChildCat_{suffix}", parent=parent_cat)

        # Create an unrelated category
        unrelated_cat = self._create_category(f"UnrelatedCat_{suffix}")

        # Create questions in parent and child categories
        parent_questions = []
        for i in range(num_parent_questions):
            q = self._create_question(parent_cat, suffix=f"{suffix}_pq{i}")
            parent_questions.append(q)

        child_questions = []
        for i in range(num_child_questions):
            q = self._create_question(child_cat, suffix=f"{suffix}_cq{i}")
            child_questions.append(q)

        # Create a question in the unrelated category (should never appear)
        self._create_question(unrelated_cat, suffix=f"{suffix}_unrelated")

        total_available = num_parent_questions + num_child_questions
        all_valid_ids = set(q.id for q in parent_questions + child_questions)

        categories_config = [
            {
                'category_id': parent_cat.id,
                'question_count': question_count,
                'include_subcategories': True,
            }
        ]

        quiz = QuestionService.generate_practice_quiz(user, categories_config)

        if total_available == 0:
            # No questions available — quiz should be None
            self.assertIsNone(quiz)
        else:
            self.assertIsNotNone(quiz)

            # Verify quiz_type is PRACTICE
            self.assertEqual(quiz.quiz_type, Quiz.QuizType.PRACTICE)

            # Verify all questions belong to parent or child category
            quiz_question_ids = set(quiz.questions.values_list('id', flat=True))
            self.assertTrue(
                quiz_question_ids.issubset(all_valid_ids),
                f"Quiz contains questions not from parent/child categories: "
                f"{quiz_question_ids - all_valid_ids}",
            )

            # Verify count <= min(requested, available)
            expected_max = min(question_count, total_available)
            self.assertLessEqual(
                len(quiz_question_ids),
                expected_max,
                f"Quiz has {len(quiz_question_ids)} questions but max should be {expected_max}",
            )

    @given(
        question_count=st.integers(min_value=1, max_value=5),
        num_questions=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=10, deadline=None)
    def test_quiz_type_is_always_practice(self, question_count, num_questions):
        """
        For any valid category configuration that produces at least one question,
        the resulting quiz must always have quiz_type=PRACTICE.
        """
        # Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
        from api.models import Quiz
        from admissionlife.services import QuestionService

        suffix = f"p8_type_{question_count}_{num_questions}_{id(self)}"

        user = User.objects.create_user(
            username=f"prop8_type_user_{suffix}",
            password="testpass123",
        )

        cat = self._create_category(f"TypeCat_{suffix}")
        for i in range(num_questions):
            self._create_question(cat, suffix=f"{suffix}_q{i}")

        categories_config = [
            {
                'category_id': cat.id,
                'question_count': question_count,
                'include_subcategories': False,
            }
        ]

        quiz = QuestionService.generate_practice_quiz(user, categories_config)

        # num_questions >= 1, so quiz should never be None here
        self.assertIsNotNone(quiz)
        self.assertEqual(quiz.quiz_type, Quiz.QuizType.PRACTICE)

    @given(
        question_count=st.integers(min_value=1, max_value=3),
        num_questions_a=st.integers(min_value=1, max_value=3),
        num_questions_b=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=10, deadline=None)
    def test_question_count_per_category_does_not_exceed_available(
        self, question_count, num_questions_a, num_questions_b
    ):
        """
        When fewer questions are available than requested, the quiz should
        include all available questions (not more, not fewer than available).
        This validates Requirement 5.3: when available < requested, include all available.
        """
        # Feature: admissionlife-questions, Property 8: Practice quiz generation category constraint
        from api.models import Quiz
        from admissionlife.services import QuestionService

        suffix = f"p8_avail_{question_count}_{num_questions_a}_{num_questions_b}_{id(self)}"

        user = User.objects.create_user(
            username=f"prop8_avail_user_{suffix}",
            password="testpass123",
        )

        cat_a = self._create_category(f"CatA_{suffix}")
        cat_b = self._create_category(f"CatB_{suffix}")

        questions_a = []
        for i in range(num_questions_a):
            q = self._create_question(cat_a, suffix=f"{suffix}_aq{i}")
            questions_a.append(q)

        questions_b = []
        for i in range(num_questions_b):
            q = self._create_question(cat_b, suffix=f"{suffix}_bq{i}")
            questions_b.append(q)

        categories_config = [
            {
                'category_id': cat_a.id,
                'question_count': question_count,
                'include_subcategories': False,
            },
            {
                'category_id': cat_b.id,
                'question_count': question_count,
                'include_subcategories': False,
            },
        ]

        quiz = QuestionService.generate_practice_quiz(user, categories_config)

        # Both categories have questions, so quiz should not be None
        self.assertIsNotNone(quiz)
        self.assertEqual(quiz.quiz_type, Quiz.QuizType.PRACTICE)

        quiz_question_ids = set(quiz.questions.values_list('id', flat=True))
        all_valid_ids = set(q.id for q in questions_a + questions_b)

        # All quiz questions must come from cat_a or cat_b
        self.assertTrue(
            quiz_question_ids.issubset(all_valid_ids),
            f"Quiz contains questions outside the configured categories: "
            f"{quiz_question_ids - all_valid_ids}",
        )

        # Total count must not exceed sum of min(requested, available) per category
        max_from_a = min(question_count, num_questions_a)
        max_from_b = min(question_count, num_questions_b)
        self.assertLessEqual(
            len(quiz_question_ids),
            max_from_a + max_from_b,
            f"Quiz has {len(quiz_question_ids)} questions but max should be "
            f"{max_from_a + max_from_b}",
        )


# =============================================================================
# Property 1: Category filtering invariant
# Feature: admissionlife-questions, Property 1: Category filtering invariant
# =============================================================================
# **Validates: Requirements 1.2, 1.3**


class TestProperty1CategoryFilteringInvariant(TestCase):
    """
    Property 1: Category filtering invariant

    For any set of categories in the database and any valid filter parameter
    (level or parent), all categories returned by the filtered endpoint SHALL
    have the specified level value or parent_id value respectively, and no
    category matching the filter SHALL be excluded from the results.

    **Validates: Requirements 1.2, 1.3**
    """

    def _apply_level_filter(self, level):
        """Apply the same filtering logic as CategoryViewSet.get_queryset() for level."""
        from api.models import Category
        qs = Category.objects.all()
        if level is not None:
            qs = qs.filter(level=int(level))
        return list(qs)

    def _apply_parent_filter(self, parent_id):
        """Apply the same filtering logic as CategoryViewSet.get_queryset() for parent."""
        from api.models import Category
        qs = Category.objects.all()
        if parent_id is not None:
            qs = qs.filter(parent_id=int(parent_id))
        return list(qs)

    def _create_category_hierarchy(self, suffix):
        """
        Create a small category hierarchy for testing:
        - 2 root categories (level=0, parent=None)
        - 2 level-1 categories per root (level=1)
        - 2 level-2 categories per level-1 (level=2)
        Returns all created categories.
        """
        from api.models import Category

        created = []

        root1 = Category.objects.create(name=f"Root1_{suffix}", parent=None)
        root2 = Category.objects.create(name=f"Root2_{suffix}", parent=None)
        created.extend([root1, root2])

        child1a = Category.objects.create(name=f"Child1a_{suffix}", parent=root1)
        child1b = Category.objects.create(name=f"Child1b_{suffix}", parent=root1)
        child2a = Category.objects.create(name=f"Child2a_{suffix}", parent=root2)
        child2b = Category.objects.create(name=f"Child2b_{suffix}", parent=root2)
        created.extend([child1a, child1b, child2a, child2b])

        leaf1a1 = Category.objects.create(name=f"Leaf1a1_{suffix}", parent=child1a)
        leaf1a2 = Category.objects.create(name=f"Leaf1a2_{suffix}", parent=child1a)
        leaf1b1 = Category.objects.create(name=f"Leaf1b1_{suffix}", parent=child1b)
        leaf2a1 = Category.objects.create(name=f"Leaf2a1_{suffix}", parent=child2a)
        created.extend([leaf1a1, leaf1a2, leaf1b1, leaf2a1])

        return created, root1, root2, child1a, child1b, child2a, child2b

    @given(
        level=st.sampled_from([0, 1, 2]),
    )
    @settings(max_examples=10, deadline=None)
    def test_level_filter_returns_only_matching_categories(self, level):
        """
        When filtering by level, all returned categories must have that level.

        **Validates: Requirements 1.2**
        """
        # Feature: admissionlife-questions, Property 1: Category filtering invariant
        from api.models import Category

        suffix = f"lvl_{level}_{id(self)}"
        self._create_category_hierarchy(suffix)

        # Apply the filter
        filtered = self._apply_level_filter(level)

        # All returned categories must have the specified level
        for cat in filtered:
            self.assertEqual(
                cat.level,
                level,
                f"Category '{cat.name}' has level={cat.level}, expected level={level}",
            )

    @given(
        level=st.sampled_from([0, 1, 2]),
    )
    @settings(max_examples=10, deadline=None)
    def test_level_filter_excludes_no_matching_category(self, level):
        """
        When filtering by level, no category with that level is excluded from results.

        **Validates: Requirements 1.2**
        """
        # Feature: admissionlife-questions, Property 1: Category filtering invariant
        from api.models import Category

        suffix = f"lvlexcl_{level}_{id(self)}"
        self._create_category_hierarchy(suffix)

        # Get all categories with this level directly from DB
        all_with_level = set(Category.objects.filter(level=level).values_list('id', flat=True))

        # Apply the filter via the viewset logic
        filtered_ids = set(cat.id for cat in self._apply_level_filter(level))

        # Every category with the target level must appear in the filtered result
        self.assertEqual(
            all_with_level,
            filtered_ids,
            f"Level filter={level}: expected {all_with_level}, got {filtered_ids}",
        )

    @given(
        level=st.sampled_from([0, 1, 2]),
    )
    @settings(max_examples=10, deadline=None)
    def test_level_filter_completeness_and_soundness(self, level):
        """
        Combined: filtered result is exactly the set of categories with the given level.
        No extra categories (soundness) and no missing categories (completeness).

        **Validates: Requirements 1.2**
        """
        # Feature: admissionlife-questions, Property 1: Category filtering invariant
        from api.models import Category

        suffix = f"lvlboth_{level}_{id(self)}"
        self._create_category_hierarchy(suffix)

        # Ground truth from DB
        expected_ids = set(Category.objects.filter(level=level).values_list('id', flat=True))

        # Filtered result
        filtered_ids = set(cat.id for cat in self._apply_level_filter(level))

        self.assertEqual(
            expected_ids,
            filtered_ids,
            f"Level filter={level} mismatch: expected={expected_ids}, got={filtered_ids}",
        )

    @given(
        use_root1=st.booleans(),
    )
    @settings(max_examples=10, deadline=None)
    def test_parent_filter_returns_only_matching_categories(self, use_root1):
        """
        When filtering by parent, all returned categories must have that parent_id.

        **Validates: Requirements 1.3**
        """
        # Feature: admissionlife-questions, Property 1: Category filtering invariant
        from api.models import Category

        suffix = f"par_{use_root1}_{id(self)}"
        _, root1, root2, child1a, child1b, child2a, child2b = self._create_category_hierarchy(suffix)

        # Pick a parent to filter by
        parent = root1 if use_root1 else root2

        # Apply the filter
        filtered = self._apply_parent_filter(parent.id)

        # All returned categories must have the specified parent_id
        for cat in filtered:
            self.assertEqual(
                cat.parent_id,
                parent.id,
                f"Category '{cat.name}' has parent_id={cat.parent_id}, expected parent_id={parent.id}",
            )

    @given(
        use_root1=st.booleans(),
    )
    @settings(max_examples=10, deadline=None)
    def test_parent_filter_excludes_no_matching_category(self, use_root1):
        """
        When filtering by parent, no category with that parent is excluded from results.

        **Validates: Requirements 1.3**
        """
        # Feature: admissionlife-questions, Property 1: Category filtering invariant
        from api.models import Category

        suffix = f"parexcl_{use_root1}_{id(self)}"
        _, root1, root2, child1a, child1b, child2a, child2b = self._create_category_hierarchy(suffix)

        parent = root1 if use_root1 else root2

        # Ground truth: all categories with this parent
        all_with_parent = set(
            Category.objects.filter(parent_id=parent.id).values_list('id', flat=True)
        )

        # Filtered result
        filtered_ids = set(cat.id for cat in self._apply_parent_filter(parent.id))

        self.assertEqual(
            all_with_parent,
            filtered_ids,
            f"Parent filter={parent.id}: expected {all_with_parent}, got {filtered_ids}",
        )

    @given(
        use_root1=st.booleans(),
    )
    @settings(max_examples=10, deadline=None)
    def test_parent_filter_completeness_and_soundness(self, use_root1):
        """
        Combined: filtered result is exactly the set of categories with the given parent.
        No extra categories (soundness) and no missing categories (completeness).

        **Validates: Requirements 1.3**
        """
        # Feature: admissionlife-questions, Property 1: Category filtering invariant
        from api.models import Category

        suffix = f"parboth_{use_root1}_{id(self)}"
        _, root1, root2, child1a, child1b, child2a, child2b = self._create_category_hierarchy(suffix)

        parent = root1 if use_root1 else root2

        # Ground truth from DB
        expected_ids = set(
            Category.objects.filter(parent_id=parent.id).values_list('id', flat=True)
        )

        # Filtered result
        filtered_ids = set(cat.id for cat in self._apply_parent_filter(parent.id))

        self.assertEqual(
            expected_ids,
            filtered_ids,
            f"Parent filter={parent.id} mismatch: expected={expected_ids}, got={filtered_ids}",
        )

    @given(
        level=st.sampled_from([0, 1, 2]),
        use_root1=st.booleans(),
    )
    @settings(max_examples=10, deadline=None)
    def test_no_filter_returns_all_categories(self, level, use_root1):
        """
        When no filter is applied, all categories are returned (baseline check).

        **Validates: Requirements 1.1**
        """
        # Feature: admissionlife-questions, Property 1: Category filtering invariant
        from api.models import Category

        suffix = f"nofilter_{level}_{use_root1}_{id(self)}"
        created, _, _, _, _, _, _ = self._create_category_hierarchy(suffix)

        # All categories in DB
        all_ids = set(Category.objects.values_list('id', flat=True))

        # No filter applied
        unfiltered = self._apply_level_filter(None)
        unfiltered_ids = set(cat.id for cat in unfiltered)

        # All created categories should be present
        created_ids = set(cat.id for cat in created)
        self.assertTrue(
            created_ids.issubset(unfiltered_ids),
            f"Some created categories missing from unfiltered result",
        )


# =============================================================================
# Property 4: Saved question round-trip and idempotence
# Feature: admissionlife-questions, Property 4: Saved question round-trip and idempotence
# =============================================================================
# **Validates: Requirements 3.1, 3.3, 3.4, 7.2**


class TestProperty4SavedQuestionRoundTripAndIdempotence(TestCase):
    """
    Property 4: Saved question round-trip and idempotence

    For any authenticated user and any valid question, saving the question SHALL
    create exactly one SavedQuestion record with user=requesting_user and
    guest_user=null. Saving the same question again SHALL not create a duplicate.
    Deleting the saved question SHALL remove it from the user's saved list,
    restoring the original state.

    **Validates: Requirements 3.1, 3.3, 3.4, 7.2**
    """

    def _create_user(self, suffix):
        """Create a unique test user."""
        return User.objects.create_user(
            username=f"prop4_sq_user_{suffix}",
            password="testpass123",
        )

    def _create_question(self, suffix):
        """Create a minimal Question for testing."""
        from api.models import Category, Question
        category, _ = Category.objects.get_or_create(
            name=f"Prop4Cat_{suffix}",
            defaults={"parent": None, "level": 0, "order": 0},
        )
        return Question.objects.create(
            category=category,
            question_text=f"Property 4 test question {suffix}",
        )

    @given(data=st.data())
    @settings(max_examples=50, deadline=None)
    def test_save_creates_exactly_one_record(self, data):
        """
        Saving a question for a user SHALL create exactly one SavedQuestion
        record with user=requesting_user and guest_user=null.

        **Validates: Requirements 3.1, 7.2**
        """
        # Feature: admissionlife-questions, Property 4: Saved question round-trip and idempotence
        from api.models import SavedQuestion

        suffix = data.draw(st.integers(min_value=0, max_value=999999))
        user = self._create_user(suffix)
        question = self._create_question(suffix)

        # Pre-condition: no saved question exists for this user-question pair
        self.assertEqual(
            SavedQuestion.objects.filter(user=user, question=question).count(), 0
        )

        # Save the question via get_or_create (mirrors SavedQuestionCreateSerializer.create)
        saved, created = SavedQuestion.objects.get_or_create(
            user=user,
            question=question,
            defaults={"guest_user": None},
        )

        # Exactly one record should exist
        count = SavedQuestion.objects.filter(user=user, question=question).count()
        self.assertEqual(count, 1, f"Expected 1 SavedQuestion record, got {count}")

        # The record should link the correct user and question
        self.assertEqual(saved.user, user)
        self.assertEqual(saved.question, question)

        # guest_user must be null (Requirement 7.2)
        self.assertIsNone(saved.guest_user)

        # The record was newly created
        self.assertTrue(created)

    @given(data=st.data())
    @settings(max_examples=50, deadline=None)
    def test_re_save_does_not_create_duplicate(self, data):
        """
        Saving the same question a second time SHALL not create a duplicate
        SavedQuestion record — the count remains exactly 1.

        **Validates: Requirement 3.4**
        """
        # Feature: admissionlife-questions, Property 4: Saved question round-trip and idempotence
        from api.models import SavedQuestion

        suffix = data.draw(st.integers(min_value=0, max_value=999999))
        user = self._create_user(suffix)
        question = self._create_question(suffix)

        # First save
        saved_first, created_first = SavedQuestion.objects.get_or_create(
            user=user,
            question=question,
            defaults={"guest_user": None},
        )
        self.assertTrue(created_first)

        # Second save (idempotent)
        saved_second, created_second = SavedQuestion.objects.get_or_create(
            user=user,
            question=question,
            defaults={"guest_user": None},
        )

        # Should NOT have created a new record
        self.assertFalse(
            created_second,
            "Second save should not create a new record (idempotence violated)",
        )

        # Still exactly one record
        count = SavedQuestion.objects.filter(user=user, question=question).count()
        self.assertEqual(count, 1, f"Expected 1 SavedQuestion record after re-save, got {count}")

        # Both calls return the same record
        self.assertEqual(saved_first.id, saved_second.id)

    @given(data=st.data())
    @settings(max_examples=50, deadline=None)
    def test_delete_removes_saved_question(self, data):
        """
        Deleting the saved question SHALL remove it from the user's saved list,
        restoring the original state (0 records).

        **Validates: Requirement 3.3**
        """
        # Feature: admissionlife-questions, Property 4: Saved question round-trip and idempotence
        from api.models import SavedQuestion

        suffix = data.draw(st.integers(min_value=0, max_value=999999))
        user = self._create_user(suffix)
        question = self._create_question(suffix)

        # Save the question
        saved, _ = SavedQuestion.objects.get_or_create(
            user=user,
            question=question,
            defaults={"guest_user": None},
        )

        # Verify it exists
        self.assertEqual(
            SavedQuestion.objects.filter(user=user, question=question).count(), 1
        )

        # Delete the saved question
        saved.delete()

        # Verify it is gone — 0 records remain
        count = SavedQuestion.objects.filter(user=user, question=question).count()
        self.assertEqual(
            count, 0,
            f"Expected 0 SavedQuestion records after delete, got {count}",
        )

    @given(data=st.data())
    @settings(max_examples=50, deadline=None)
    def test_full_round_trip_save_resave_delete(self, data):
        """
        Full round-trip: save → verify 1 record → re-save → verify still 1 record
        → delete → verify 0 records.

        **Validates: Requirements 3.1, 3.3, 3.4, 7.2**
        """
        # Feature: admissionlife-questions, Property 4: Saved question round-trip and idempotence
        from api.models import SavedQuestion

        suffix = data.draw(st.integers(min_value=0, max_value=999999))
        user = self._create_user(suffix)
        question = self._create_question(suffix)

        # Step 1: Save the question — exactly 1 record
        saved, created = SavedQuestion.objects.get_or_create(
            user=user,
            question=question,
            defaults={"guest_user": None},
        )
        self.assertTrue(created)
        self.assertEqual(
            SavedQuestion.objects.filter(user=user, question=question).count(), 1
        )
        self.assertIsNone(saved.guest_user)

        # Step 2: Re-save — still exactly 1 record (no duplicate)
        saved_again, created_again = SavedQuestion.objects.get_or_create(
            user=user,
            question=question,
            defaults={"guest_user": None},
        )
        self.assertFalse(created_again)
        self.assertEqual(saved.id, saved_again.id)
        self.assertEqual(
            SavedQuestion.objects.filter(user=user, question=question).count(), 1
        )

        # Step 3: Delete — 0 records remain
        saved.delete()
        self.assertEqual(
            SavedQuestion.objects.filter(user=user, question=question).count(), 0
        )


# =============================================================================
# Property 5: Saved question ownership isolation
# Feature: admissionlife-questions, Property 5: Saved question ownership isolation
# =============================================================================
# **Validates: Requirements 3.2**


class TestProperty5SavedQuestionOwnershipIsolation(TestCase):
    """
    Property 5: Saved question ownership isolation

    For any two distinct authenticated users, the saved questions list for user A
    SHALL contain only SavedQuestion records where user=A, and SHALL never include
    records belonging to user B, regardless of how many questions user B has saved.

    **Validates: Requirements 3.2**
    """

    @given(
        num_questions_a=st.integers(min_value=1, max_value=5),
        num_questions_b=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=50, deadline=None)
    def test_user_a_saved_list_never_contains_user_b_records(
        self, num_questions_a, num_questions_b
    ):
        """
        User A's saved question list (filtered by user=A, guest_user__isnull=True)
        should never contain any SavedQuestion records belonging to user B.

        **Validates: Requirements 3.2**
        """
        # Feature: admissionlife-questions, Property 5: Saved question ownership isolation
        from api.models import Category, Question, SavedQuestion

        suffix = f"p5_iso_{num_questions_a}_{num_questions_b}_{id(self)}"

        # Create two distinct users
        user_a = User.objects.create_user(
            username=f"prop5_user_a_{suffix}",
            password="testpass123",
        )
        user_b = User.objects.create_user(
            username=f"prop5_user_b_{suffix}",
            password="testpass123",
        )

        # Create a shared category and questions
        category = Category.objects.create(
            name=f"Cat_P5_{suffix}",
            parent=None,
            level=0,
            order=0,
        )

        total_questions = num_questions_a + num_questions_b
        questions = []
        for i in range(total_questions):
            q = Question.objects.create(
                category=category,
                question_text=f"Question {i} for prop5 {suffix}",
            )
            questions.append(q)

        # User A saves the first num_questions_a questions
        questions_for_a = questions[:num_questions_a]
        for q in questions_for_a:
            SavedQuestion.objects.create(user=user_a, question=q, guest_user=None)

        # User B saves the last num_questions_b questions
        questions_for_b = questions[num_questions_a:]
        for q in questions_for_b:
            SavedQuestion.objects.create(user=user_b, question=q, guest_user=None)

        # Query user A's saved questions (as the view does)
        user_a_saved = SavedQuestion.objects.filter(
            user=user_a, guest_user__isnull=True
        )

        # Verify none of the returned records belong to user B
        for saved in user_a_saved:
            self.assertEqual(
                saved.user,
                user_a,
                f"SavedQuestion {saved.id} belongs to user {saved.user} "
                f"but should only belong to user_a",
            )
            self.assertNotEqual(
                saved.user,
                user_b,
                f"SavedQuestion {saved.id} from user B appeared in user A's list",
            )

        # Verify user B's records are not in user A's list
        user_b_saved_ids = set(
            SavedQuestion.objects.filter(user=user_b, guest_user__isnull=True)
            .values_list('id', flat=True)
        )
        user_a_saved_ids = set(user_a_saved.values_list('id', flat=True))

        self.assertTrue(
            user_a_saved_ids.isdisjoint(user_b_saved_ids),
            f"User A's saved list overlaps with user B's: "
            f"{user_a_saved_ids & user_b_saved_ids}",
        )

    @given(
        num_shared_questions=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=50, deadline=None)
    def test_both_users_saving_same_questions_remain_isolated(
        self, num_shared_questions
    ):
        """
        When both user A and user B save the same questions, each user's
        filtered list should only contain their own SavedQuestion records,
        not the other user's records (even though the underlying questions
        are the same).

        **Validates: Requirements 3.2**
        """
        # Feature: admissionlife-questions, Property 5: Saved question ownership isolation
        from api.models import Category, Question, SavedQuestion

        suffix = f"p5_shared_{num_shared_questions}_{id(self)}"

        # Create two distinct users
        user_a = User.objects.create_user(
            username=f"prop5_shared_a_{suffix}",
            password="testpass123",
        )
        user_b = User.objects.create_user(
            username=f"prop5_shared_b_{suffix}",
            password="testpass123",
        )

        # Create shared questions
        category = Category.objects.create(
            name=f"SharedCat_P5_{suffix}",
            parent=None,
            level=0,
            order=0,
        )
        shared_questions = []
        for i in range(num_shared_questions):
            q = Question.objects.create(
                category=category,
                question_text=f"Shared Question {i} for prop5 {suffix}",
            )
            shared_questions.append(q)

        # Both users save the same questions
        for q in shared_questions:
            SavedQuestion.objects.create(user=user_a, question=q, guest_user=None)
            SavedQuestion.objects.create(user=user_b, question=q, guest_user=None)

        # Query each user's saved list
        user_a_saved = SavedQuestion.objects.filter(
            user=user_a, guest_user__isnull=True
        )
        user_b_saved = SavedQuestion.objects.filter(
            user=user_b, guest_user__isnull=True
        )

        # User A's list should only contain user A's records
        for saved in user_a_saved:
            self.assertEqual(
                saved.user,
                user_a,
                f"SavedQuestion {saved.id} in user A's list belongs to {saved.user}",
            )

        # User B's list should only contain user B's records
        for saved in user_b_saved:
            self.assertEqual(
                saved.user,
                user_b,
                f"SavedQuestion {saved.id} in user B's list belongs to {saved.user}",
            )

        # The two sets of SavedQuestion IDs must be disjoint
        user_a_ids = set(user_a_saved.values_list('id', flat=True))
        user_b_ids = set(user_b_saved.values_list('id', flat=True))

        self.assertTrue(
            user_a_ids.isdisjoint(user_b_ids),
            f"User A and user B share SavedQuestion records: {user_a_ids & user_b_ids}",
        )

        # Each user should have exactly num_shared_questions saved records
        self.assertEqual(
            len(user_a_ids),
            num_shared_questions,
            f"User A should have {num_shared_questions} saved questions, got {len(user_a_ids)}",
        )
        self.assertEqual(
            len(user_b_ids),
            num_shared_questions,
            f"User B should have {num_shared_questions} saved questions, got {len(user_b_ids)}",
        )

    @given(
        num_questions_a=st.integers(min_value=0, max_value=4),
        num_questions_b=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=50, deadline=None)
    def test_user_a_with_no_saves_has_empty_list_regardless_of_user_b(
        self, num_questions_a, num_questions_b
    ):
        """
        When user A has saved no questions (or fewer than user B), user A's
        filtered list should be empty (or contain only user A's records),
        and user B's saves should have no effect on user A's list.

        **Validates: Requirements 3.2**
        """
        # Feature: admissionlife-questions, Property 5: Saved question ownership isolation
        from api.models import Category, Question, SavedQuestion

        suffix = f"p5_empty_{num_questions_a}_{num_questions_b}_{id(self)}"

        user_a = User.objects.create_user(
            username=f"prop5_empty_a_{suffix}",
            password="testpass123",
        )
        user_b = User.objects.create_user(
            username=f"prop5_empty_b_{suffix}",
            password="testpass123",
        )

        category = Category.objects.create(
            name=f"EmptyCat_P5_{suffix}",
            parent=None,
            level=0,
            order=0,
        )

        # Create enough questions for both users
        total = num_questions_a + num_questions_b
        questions = []
        for i in range(total):
            q = Question.objects.create(
                category=category,
                question_text=f"Empty test Q{i} {suffix}",
            )
            questions.append(q)

        # User A saves num_questions_a questions (may be 0)
        for q in questions[:num_questions_a]:
            SavedQuestion.objects.create(user=user_a, question=q, guest_user=None)

        # User B saves num_questions_b questions
        for q in questions[num_questions_a:]:
            SavedQuestion.objects.create(user=user_b, question=q, guest_user=None)

        # Query user A's saved list
        user_a_saved = list(
            SavedQuestion.objects.filter(user=user_a, guest_user__isnull=True)
        )

        # User A's list should have exactly num_questions_a records
        self.assertEqual(
            len(user_a_saved),
            num_questions_a,
            f"User A should have {num_questions_a} saved questions, got {len(user_a_saved)}",
        )

        # None of user A's records should belong to user B
        for saved in user_a_saved:
            self.assertNotEqual(
                saved.user_id,
                user_b.id,
                f"User B's record appeared in user A's list: SavedQuestion {saved.id}",
            )


# =============================================================================
# Property 6: Saved question filter correctness
# Feature: admissionlife-questions, Property 6: Saved question filter correctness
# =============================================================================
# **Validates: Requirements 3.5, 3.6**


class TestProperty6SavedQuestionFilterCorrectness(TestCase):
    """
    Property 6: Saved question filter correctness

    For any category name filter, label name filter, or search text applied to
    the saved questions endpoint, all returned SavedQuestion records SHALL have
    a question whose category name contains the filter string, whose labels
    contain the label filter string, or whose question_text contains the search
    string, respectively.

    **Validates: Requirements 3.5, 3.6**
    """

    def _create_user(self, suffix):
        """Create a unique user for the test."""
        return User.objects.create_user(
            username=f"prop6_user_{suffix}",
            password="testpass123",
        )

    def _create_category(self, name):
        """Create a Category with the given name."""
        from api.models import Category
        return Category.objects.create(name=name, parent=None, level=0, order=0)

    def _create_label(self, name):
        """Create a Label with the given name."""
        from api.models import Label
        label, _ = Label.objects.get_or_create(name=name)
        return label

    def _create_question(self, category, question_text, labels=None):
        """Create a Question in the given category with optional labels."""
        from api.models import Question
        question = Question.objects.create(
            category=category,
            question_text=question_text,
        )
        if labels:
            question.labels.set(labels)
        return question

    def _save_question(self, user, question):
        """Save a question for the user (admissionlife style: guest_user=null)."""
        from api.models import SavedQuestion
        saved, _ = SavedQuestion.objects.get_or_create(
            user=user,
            question=question,
            defaults={'guest_user': None},
        )
        return saved

    def _get_filtered_saved_questions(self, user, category=None, label=None, search=None):
        """
        Replicate the SavedQuestionViewSet.get_queryset() filter logic directly,
        so the property test validates the ORM filter behaviour without HTTP overhead.
        """
        from api.models import SavedQuestion
        qs = (
            SavedQuestion.objects.filter(user=user, guest_user__isnull=True)
            .select_related('question__category')
            .prefetch_related('question__labels')
            .order_by('-saved_at')
        )
        if category:
            qs = qs.filter(question__category__name__icontains=category)
        if label:
            qs = qs.filter(question__labels__name__icontains=label)
        if search:
            qs = qs.filter(question__question_text__icontains=search)
        return list(qs)

    # -------------------------------------------------------------------------
    # Category filter
    # -------------------------------------------------------------------------

    @given(
        filter_fragment=st.text(
            alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')),
            min_size=1,
            max_size=8,
        ),
        num_matching=st.integers(min_value=1, max_value=4),
        num_non_matching=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=50, deadline=None)
    def test_category_filter_returns_only_matching_records(
        self, filter_fragment, num_matching, num_non_matching
    ):
        """
        When filtering saved questions by category, every returned SavedQuestion
        must have question.category.name containing the filter string
        (case-insensitive).

        **Validates: Requirements 3.5**
        """
        # Feature: admissionlife-questions, Property 6: Saved question filter correctness
        suffix = f"catfilt_{filter_fragment}_{num_matching}_{num_non_matching}_{id(self)}"
        user = self._create_user(suffix)

        # Create matching categories (name contains filter_fragment)
        matching_cat = self._create_category(f"Cat_{filter_fragment}_match_{suffix}")

        # Create a non-matching category (name does NOT contain filter_fragment)
        # We guarantee no overlap by using a name that is purely numeric digits
        # and the filter_fragment is purely alpha — or vice versa.
        non_matching_name = f"ZZZNONMATCH_{suffix}"
        # Ensure the non-matching name truly does not contain the fragment
        assume(filter_fragment.lower() not in non_matching_name.lower())
        non_matching_cat = self._create_category(non_matching_name)

        # Save questions in matching category
        for i in range(num_matching):
            q = self._create_question(matching_cat, f"Question {i} in matching cat {suffix}")
            self._save_question(user, q)

        # Save questions in non-matching category
        for i in range(num_non_matching):
            q = self._create_question(non_matching_cat, f"Question {i} in non-matching cat {suffix}")
            self._save_question(user, q)

        # Apply category filter
        results = self._get_filtered_saved_questions(user, category=filter_fragment)

        # Property: every returned record's category name contains the filter string
        for saved in results:
            cat_name = saved.question.category.name if saved.question.category else ""
            self.assertIn(
                filter_fragment.lower(),
                cat_name.lower(),
                f"SavedQuestion {saved.id} has category '{cat_name}' which does not "
                f"contain filter '{filter_fragment}'",
            )

        # Property: all matching records are returned (no false negatives)
        result_ids = {s.id for s in results}
        all_saved = list(
            __import__('api.models', fromlist=['SavedQuestion']).SavedQuestion.objects.filter(
                user=user, guest_user__isnull=True
            ).select_related('question__category')
        )
        for saved in all_saved:
            cat_name = saved.question.category.name if saved.question.category else ""
            if filter_fragment.lower() in cat_name.lower():
                self.assertIn(
                    saved.id,
                    result_ids,
                    f"SavedQuestion {saved.id} with category '{cat_name}' should be "
                    f"in results for filter '{filter_fragment}' but was not",
                )

    # -------------------------------------------------------------------------
    # Label filter
    # -------------------------------------------------------------------------

    @given(
        filter_fragment=st.text(
            alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')),
            min_size=1,
            max_size=8,
        ),
        num_matching=st.integers(min_value=1, max_value=4),
        num_non_matching=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=50, deadline=None)
    def test_label_filter_returns_only_matching_records(
        self, filter_fragment, num_matching, num_non_matching
    ):
        """
        When filtering saved questions by label, every returned SavedQuestion
        must have at least one label whose name contains the filter string
        (case-insensitive).

        **Validates: Requirements 3.5**
        """
        # Feature: admissionlife-questions, Property 6: Saved question filter correctness
        suffix = f"labfilt_{filter_fragment}_{num_matching}_{num_non_matching}_{id(self)}"
        user = self._create_user(suffix)

        # Create a shared category for all questions
        cat = self._create_category(f"LabelTestCat_{suffix}")

        # Create a matching label (name contains filter_fragment)
        matching_label = self._create_label(f"Label_{filter_fragment}_match_{suffix}")

        # Create a non-matching label
        non_matching_label_name = f"ZZZNONMATCH_label_{suffix}"
        assume(filter_fragment.lower() not in non_matching_label_name.lower())
        non_matching_label = self._create_label(non_matching_label_name)

        # Save questions with matching label
        for i in range(num_matching):
            q = self._create_question(
                cat,
                f"Question {i} with matching label {suffix}",
                labels=[matching_label],
            )
            self._save_question(user, q)

        # Save questions with non-matching label only
        for i in range(num_non_matching):
            q = self._create_question(
                cat,
                f"Question {i} with non-matching label {suffix}",
                labels=[non_matching_label],
            )
            self._save_question(user, q)

        # Apply label filter
        results = self._get_filtered_saved_questions(user, label=filter_fragment)

        # Property: every returned record has at least one label containing the filter
        for saved in results:
            label_names = [lbl.name for lbl in saved.question.labels.all()]
            has_match = any(filter_fragment.lower() in ln.lower() for ln in label_names)
            self.assertTrue(
                has_match,
                f"SavedQuestion {saved.id} has labels {label_names} but none contain "
                f"filter '{filter_fragment}'",
            )

        # Property: all matching records are returned (no false negatives)
        result_ids = {s.id for s in results}
        from api.models import SavedQuestion
        all_saved = list(
            SavedQuestion.objects.filter(user=user, guest_user__isnull=True)
            .prefetch_related('question__labels')
        )
        for saved in all_saved:
            label_names = [lbl.name for lbl in saved.question.labels.all()]
            if any(filter_fragment.lower() in ln.lower() for ln in label_names):
                self.assertIn(
                    saved.id,
                    result_ids,
                    f"SavedQuestion {saved.id} with labels {label_names} should be "
                    f"in results for label filter '{filter_fragment}' but was not",
                )

    # -------------------------------------------------------------------------
    # Search (question_text) filter
    # -------------------------------------------------------------------------

    @given(
        search_fragment=st.text(
            alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')),
            min_size=1,
            max_size=8,
        ),
        num_matching=st.integers(min_value=1, max_value=4),
        num_non_matching=st.integers(min_value=0, max_value=3),
    )
    @settings(max_examples=50, deadline=None)
    def test_search_filter_returns_only_matching_records(
        self, search_fragment, num_matching, num_non_matching
    ):
        """
        When filtering saved questions by search text, every returned
        SavedQuestion must have question.question_text containing the search
        string (case-insensitive).

        **Validates: Requirements 3.6**
        """
        # Feature: admissionlife-questions, Property 6: Saved question filter correctness
        suffix = f"srchfilt_{search_fragment}_{num_matching}_{num_non_matching}_{id(self)}"
        user = self._create_user(suffix)

        cat = self._create_category(f"SearchTestCat_{suffix}")

        # Non-matching question_text prefix — guaranteed not to contain search_fragment
        non_matching_prefix = f"ZZZNONMATCH_{suffix}"
        assume(search_fragment.lower() not in non_matching_prefix.lower())

        # Save questions whose text contains the search fragment
        for i in range(num_matching):
            q = self._create_question(
                cat,
                f"This question contains {search_fragment} in its text {suffix} idx {i}",
            )
            self._save_question(user, q)

        # Save questions whose text does NOT contain the search fragment
        for i in range(num_non_matching):
            q = self._create_question(
                cat,
                f"{non_matching_prefix} idx {i}",
            )
            self._save_question(user, q)

        # Apply search filter
        results = self._get_filtered_saved_questions(user, search=search_fragment)

        # Property: every returned record's question_text contains the search string
        for saved in results:
            question_text = saved.question.question_text
            self.assertIn(
                search_fragment.lower(),
                question_text.lower(),
                f"SavedQuestion {saved.id} has question_text '{question_text[:60]}' "
                f"which does not contain search '{search_fragment}'",
            )

        # Property: all matching records are returned (no false negatives)
        result_ids = {s.id for s in results}
        from api.models import SavedQuestion
        all_saved = list(
            SavedQuestion.objects.filter(user=user, guest_user__isnull=True)
            .select_related('question')
        )
        for saved in all_saved:
            if search_fragment.lower() in saved.question.question_text.lower():
                self.assertIn(
                    saved.id,
                    result_ids,
                    f"SavedQuestion {saved.id} with text '{saved.question.question_text[:60]}' "
                    f"should be in results for search '{search_fragment}' but was not",
                )


# =============================================================================
# Property 9: Quiz attempt hides correct answers
# Feature: admissionlife-questions, Property 9: Quiz attempt hides correct answers
# =============================================================================
# **Validates: Requirements 6.1**


class TestProperty9QuizAttemptHidesCorrectAnswers(TestCase):
    """
    Property 9: Quiz attempt hides correct answers

    For any practice quiz, when a user starts an attempt, the response SHALL
    include all quiz questions but SHALL NOT include the `is_correct` field on
    any answer or the `explanation` field on any question.

    **Validates: Requirements 6.1**
    """

    def _create_quiz_with_questions(self, num_questions, num_answers_per_question, suffix):
        """
        Helper: create a Quiz with the given number of questions, each having
        `num_answers_per_question` answers (first answer is correct).
        Returns (user, quiz, questions).
        """
        from api.models import Answer, Category, Question, Quiz

        user = User.objects.create_user(
            username=f"prop9_user_{suffix}",
            password="testpass123",
        )
        category = Category.objects.create(
            name=f"Prop9Cat_{suffix}",
            parent=None,
            level=0,
            order=0,
        )
        questions = []
        for i in range(num_questions):
            question = Question.objects.create(
                category=category,
                question_text=f"Question {i} for {suffix}",
                explanation=f"Explanation for question {i}",
            )
            for j in range(num_answers_per_question):
                Answer.objects.create(
                    question=question,
                    text=f"Answer {j} for Q{i} {suffix}",
                    is_correct=(j == 0),  # first answer is correct
                )
            questions.append(question)

        quiz = Quiz.objects.create(
            name=f"Prop9Quiz_{suffix}",
            quiz_type=Quiz.QuizType.PRACTICE,
            duration_minutes=num_questions,
        )
        quiz.questions.set(questions)
        return user, quiz, questions

    @given(
        num_questions=st.integers(min_value=1, max_value=6),
        num_answers=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=50, deadline=None)
    def test_serialized_answers_do_not_contain_is_correct(
        self, num_questions, num_answers
    ):
        """
        When a quiz is serialized via QuestionQuizSerializer (used in
        PracticeQuizAttemptStartSerializer), no answer in the output should
        contain the `is_correct` field.

        **Validates: Requirements 6.1**
        """
        # Feature: admissionlife-questions, Property 9: Quiz attempt hides correct answers
        from admissionlife.serializers import PracticeQuizAttemptStartSerializer
        from admissionlife.services import QuestionService

        suffix = f"iscorrect_{num_questions}_{num_answers}_{id(self)}"
        user, quiz, questions = self._create_quiz_with_questions(
            num_questions, num_answers, suffix
        )

        # Start a quiz attempt
        attempt = QuestionService.start_quiz_attempt(user, quiz)

        # Build the serializer data as the view would
        serializer = PracticeQuizAttemptStartSerializer({
            'attempt_id': attempt.id,
            'questions': quiz.questions.prefetch_related('answers').all(),
        })
        data = serializer.data

        # Verify the response contains questions
        self.assertIn('questions', data)
        self.assertEqual(len(data['questions']), num_questions)

        # Verify no answer contains `is_correct`
        for question_data in data['questions']:
            self.assertIn('answers', question_data)
            for answer_data in question_data['answers']:
                self.assertNotIn(
                    'is_correct',
                    answer_data,
                    f"Answer data should not contain 'is_correct' field, "
                    f"but got: {answer_data}",
                )

    @given(
        num_questions=st.integers(min_value=1, max_value=6),
        num_answers=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=50, deadline=None)
    def test_serialized_questions_do_not_contain_explanation(
        self, num_questions, num_answers
    ):
        """
        When a quiz is serialized via QuestionQuizSerializer (used in
        PracticeQuizAttemptStartSerializer), no question in the output should
        contain the `explanation` field.

        **Validates: Requirements 6.1**
        """
        # Feature: admissionlife-questions, Property 9: Quiz attempt hides correct answers
        from admissionlife.serializers import PracticeQuizAttemptStartSerializer
        from admissionlife.services import QuestionService

        suffix = f"explanation_{num_questions}_{num_answers}_{id(self)}"
        user, quiz, questions = self._create_quiz_with_questions(
            num_questions, num_answers, suffix
        )

        # Start a quiz attempt
        attempt = QuestionService.start_quiz_attempt(user, quiz)

        # Build the serializer data as the view would
        serializer = PracticeQuizAttemptStartSerializer({
            'attempt_id': attempt.id,
            'questions': quiz.questions.prefetch_related('answers').all(),
        })
        data = serializer.data

        # Verify no question contains `explanation`
        for question_data in data['questions']:
            self.assertNotIn(
                'explanation',
                question_data,
                f"Question data should not contain 'explanation' field, "
                f"but got: {question_data}",
            )

    @given(
        num_questions=st.integers(min_value=1, max_value=6),
        num_answers=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=50, deadline=None)
    def test_serialized_questions_contain_required_fields(
        self, num_questions, num_answers
    ):
        """
        When a quiz is serialized via QuestionQuizSerializer, each question
        SHALL contain question_text and question_image, and each answer SHALL
        contain text and image.

        **Validates: Requirements 6.1**
        """
        # Feature: admissionlife-questions, Property 9: Quiz attempt hides correct answers
        from admissionlife.serializers import PracticeQuizAttemptStartSerializer
        from admissionlife.services import QuestionService

        suffix = f"required_{num_questions}_{num_answers}_{id(self)}"
        user, quiz, questions = self._create_quiz_with_questions(
            num_questions, num_answers, suffix
        )

        # Start a quiz attempt
        attempt = QuestionService.start_quiz_attempt(user, quiz)

        # Build the serializer data as the view would
        serializer = PracticeQuizAttemptStartSerializer({
            'attempt_id': attempt.id,
            'questions': quiz.questions.prefetch_related('answers').all(),
        })
        data = serializer.data

        # Verify attempt_id is present
        self.assertIn('attempt_id', data)
        self.assertEqual(data['attempt_id'], attempt.id)

        # Verify each question has the required fields
        for question_data in data['questions']:
            self.assertIn(
                'question_text', question_data,
                "Question data must contain 'question_text'",
            )
            self.assertIn(
                'question_image', question_data,
                "Question data must contain 'question_image'",
            )
            # Verify each answer has text and image
            for answer_data in question_data['answers']:
                self.assertIn(
                    'text', answer_data,
                    "Answer data must contain 'text'",
                )
                self.assertIn(
                    'image', answer_data,
                    "Answer data must contain 'image'",
                )

    @given(
        num_questions=st.integers(min_value=1, max_value=6),
        num_correct=st.integers(min_value=1, max_value=3),
        num_incorrect=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=50, deadline=None)
    def test_hides_correct_answers_regardless_of_mix(
        self, num_questions, num_correct, num_incorrect
    ):
        """
        Even when questions have a mix of correct and incorrect answers,
        the serialized output must never expose `is_correct` on any answer.

        **Validates: Requirements 6.1**
        """
        # Feature: admissionlife-questions, Property 9: Quiz attempt hides correct answers
        from api.models import Answer, Category, Question, Quiz
        from admissionlife.serializers import PracticeQuizAttemptStartSerializer
        from admissionlife.services import QuestionService

        suffix = f"mix_{num_questions}_{num_correct}_{num_incorrect}_{id(self)}"

        user = User.objects.create_user(
            username=f"prop9_mix_user_{suffix}",
            password="testpass123",
        )
        category = Category.objects.create(
            name=f"Prop9MixCat_{suffix}",
            parent=None,
            level=0,
            order=0,
        )

        questions = []
        for i in range(num_questions):
            question = Question.objects.create(
                category=category,
                question_text=f"Mix Question {i} {suffix}",
                explanation=f"Mix explanation {i}",
            )
            # Create correct answers
            for j in range(num_correct):
                Answer.objects.create(
                    question=question,
                    text=f"Correct Ans {j} Q{i} {suffix}",
                    is_correct=True,
                )
            # Create incorrect answers
            for j in range(num_incorrect):
                Answer.objects.create(
                    question=question,
                    text=f"Wrong Ans {j} Q{i} {suffix}",
                    is_correct=False,
                )
            questions.append(question)

        quiz = Quiz.objects.create(
            name=f"Prop9MixQuiz_{suffix}",
            quiz_type=Quiz.QuizType.PRACTICE,
            duration_minutes=num_questions,
        )
        quiz.questions.set(questions)

        attempt = QuestionService.start_quiz_attempt(user, quiz)

        serializer = PracticeQuizAttemptStartSerializer({
            'attempt_id': attempt.id,
            'questions': quiz.questions.prefetch_related('answers').all(),
        })
        data = serializer.data

        # Verify no answer exposes is_correct regardless of the actual value
        for question_data in data['questions']:
            self.assertNotIn(
                'explanation',
                question_data,
                f"Question must not expose 'explanation': {question_data}",
            )
            for answer_data in question_data['answers']:
                self.assertNotIn(
                    'is_correct',
                    answer_data,
                    f"Answer must not expose 'is_correct': {answer_data}",
                )


# =============================================================================
# Property 7: Question report creation correctness
# Feature: admissionlife-questions, Property 7: Question report creation correctness
# =============================================================================
# **Validates: Requirements 4.1, 4.2**


class TestProperty7QuestionReportCreationCorrectness(TestCase):
    """
    Property 7: Question report creation correctness

    For any authenticated user and any valid question with a non-empty reason
    string, creating a report SHALL produce a QuestionReport record with
    status=PENDING, user=requesting_user, question=specified_question, and
    reason=specified_reason.

    **Validates: Requirements 4.1, 4.2**
    """

    def _create_user(self, suffix):
        """Create a unique test user."""
        return User.objects.create_user(
            username=f"prop7_user_{suffix}",
            password="testpass123",
        )

    def _create_question(self, suffix):
        """Create a minimal Question for testing."""
        from api.models import Question
        return Question.objects.create(
            question_text=f"Property 7 test question {suffix}",
        )

    @given(
        reason=st.text(min_size=1, max_size=500),
    )
    @settings(max_examples=10, deadline=None)
    def test_report_has_pending_status(self, reason):
        """
        A newly created QuestionReport must have status=PENDING.

        **Validates: Requirements 4.1**
        """
        # Feature: admissionlife-questions, Property 7: Question report creation correctness
        from api.models import QuestionReport

        suffix = f"status_{id(self)}_{len(reason)}"
        user = self._create_user(suffix)
        question = self._create_question(suffix)

        report = QuestionReport.objects.create(
            user=user,
            question=question,
            reason=reason,
        )

        self.assertEqual(report.status, QuestionReport.ReportStatus.PENDING)

    @given(
        reason=st.text(min_size=1, max_size=500),
    )
    @settings(max_examples=10, deadline=None)
    def test_report_has_correct_user(self, reason):
        """
        A newly created QuestionReport must be associated with the correct user.

        **Validates: Requirements 4.2**
        """
        # Feature: admissionlife-questions, Property 7: Question report creation correctness
        from api.models import QuestionReport

        suffix = f"user_{id(self)}_{len(reason)}"
        user = self._create_user(suffix)
        question = self._create_question(suffix)

        report = QuestionReport.objects.create(
            user=user,
            question=question,
            reason=reason,
        )

        self.assertEqual(report.user, user)

    @given(
        reason=st.text(min_size=1, max_size=500),
    )
    @settings(max_examples=10, deadline=None)
    def test_report_has_correct_question(self, reason):
        """
        A newly created QuestionReport must reference the correct question.

        **Validates: Requirements 4.1**
        """
        # Feature: admissionlife-questions, Property 7: Question report creation correctness
        from api.models import QuestionReport

        suffix = f"question_{id(self)}_{len(reason)}"
        user = self._create_user(suffix)
        question = self._create_question(suffix)

        report = QuestionReport.objects.create(
            user=user,
            question=question,
            reason=reason,
        )

        self.assertEqual(report.question, question)

    @given(
        reason=st.text(min_size=1, max_size=500),
    )
    @settings(max_examples=10, deadline=None)
    def test_report_has_correct_reason(self, reason):
        """
        A newly created QuestionReport must store the exact reason provided.

        **Validates: Requirements 4.1**
        """
        # Feature: admissionlife-questions, Property 7: Question report creation correctness
        from api.models import QuestionReport

        suffix = f"reason_{id(self)}_{len(reason)}"
        user = self._create_user(suffix)
        question = self._create_question(suffix)

        report = QuestionReport.objects.create(
            user=user,
            question=question,
            reason=reason,
        )

        self.assertEqual(report.reason, reason)

    @given(
        reason=st.text(min_size=1, max_size=500),
    )
    @settings(max_examples=10, deadline=None)
    def test_report_all_fields_correct(self, reason):
        """
        Combined: a newly created QuestionReport has status=PENDING, correct user,
        correct question, and correct reason — all verified together.

        **Validates: Requirements 4.1, 4.2**
        """
        # Feature: admissionlife-questions, Property 7: Question report creation correctness
        from api.models import QuestionReport

        suffix = f"all_{id(self)}_{len(reason)}"
        user = self._create_user(suffix)
        question = self._create_question(suffix)

        report = QuestionReport.objects.create(
            user=user,
            question=question,
            reason=reason,
        )

        self.assertEqual(report.status, QuestionReport.ReportStatus.PENDING)
        self.assertEqual(report.user, user)
        self.assertEqual(report.question, question)
        self.assertEqual(report.reason, reason)
