import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from admissionlife.models import Batch, Enrollment, Exam, ExamAttempt, ExamSubmission, Payment

logger = logging.getLogger(__name__)


class EnrollmentService:
    """Handles enrollment creation and status checks."""

    @staticmethod
    def create_enrollment(user, batch, payment):
        """
        Create an Enrollment record for the given user and batch.

        If a unique constraint violation occurs (user already enrolled in batch),
        returns the existing enrollment without error (idempotent).
        For any other failure, raises the exception.

        Args:
            user: Django User instance
            batch: Batch instance
            payment: Payment instance (linked via OneToOneField)

        Returns:
            Enrollment instance (newly created or existing)
        """
        try:
            with transaction.atomic():
                enrollment = Enrollment.objects.create(
                    user=user,
                    batch=batch,
                    payment=payment,
                )
            return enrollment
        except IntegrityError:
            # Unique constraint on (user, batch) violated — return existing
            existing = Enrollment.objects.get(user=user, batch=batch)
            return existing

    @staticmethod
    def check_enrollment(user, batch):
        """
        Check whether a user is enrolled in a batch.

        Args:
            user: Django User instance
            batch: Batch instance

        Returns:
            bool: True if enrollment exists, False otherwise
        """
        return Enrollment.objects.filter(user=user, batch=batch).exists()


class PaymentService:
    """Handles payment submission, approval, and rejection."""

    @staticmethod
    def submit_payment(user, data):
        """
        Create a new Payment record with status=PENDING.

        Validates:
        - No duplicate transaction_id for the same payment_method
        - User is not already enrolled in the batch

        Args:
            user: Django User instance
            data: dict with keys: batch, payment_method, transaction_id,
                  sender_number, amount

        Returns:
            Payment instance

        Raises:
            ValueError: If duplicate transaction_id or user already enrolled
        """
        batch = data['batch']
        payment_method = data['payment_method']
        transaction_id = data['transaction_id']
        sender_number = data['sender_number']
        amount = data['amount']

        # Check for duplicate transaction_id per payment_method
        if Payment.objects.filter(
            payment_method=payment_method,
            transaction_id=transaction_id,
        ).exists():
            raise ValueError(
                "This transaction ID is already used for this payment method."
            )

        # Check if user is already enrolled in the batch
        if EnrollmentService.check_enrollment(user, batch):
            raise ValueError("You are already enrolled in this batch.")

        payment = Payment.objects.create(
            user=user,
            batch=batch,
            payment_method=payment_method,
            transaction_id=transaction_id,
            sender_number=sender_number,
            amount=amount,
            status=Payment.PaymentStatus.PENDING,
        )
        return payment

    @staticmethod
    def approve_payment(payment, admin):
        """
        Approve a pending payment and create an enrollment.

        Sets payment status to APPROVED and reviewed_at to now.
        Calls EnrollmentService.create_enrollment() to link user to batch.
        If enrollment creation fails, keeps payment as APPROVED but raises error.

        Args:
            payment: Payment instance (must be PENDING)
            admin: Django User instance (staff user performing approval)

        Returns:
            Enrollment instance

        Raises:
            ValueError: If payment is not in PENDING status
            RuntimeError: If enrollment creation fails
        """
        if payment.status != Payment.PaymentStatus.PENDING:
            raise ValueError("This payment has already been reviewed.")

        payment.status = Payment.PaymentStatus.APPROVED
        payment.reviewed_at = timezone.now()
        payment.save()

        try:
            enrollment = EnrollmentService.create_enrollment(
                user=payment.user,
                batch=payment.batch,
                payment=payment,
            )
            return enrollment
        except Exception as e:
            # Keep payment as APPROVED but raise error about enrollment failure
            raise RuntimeError(
                f"Enrollment creation failed: {str(e)}"
            )

    @staticmethod
    def reject_payment(payment, admin, notes):
        """
        Reject a pending payment with admin notes.

        Sets payment status to REJECTED, stores admin_notes, and sets reviewed_at.

        Args:
            payment: Payment instance (must be PENDING)
            admin: Django User instance (staff user performing rejection)
            notes: str with rejection reason

        Returns:
            Payment instance

        Raises:
            ValueError: If payment is not in PENDING status
        """
        if payment.status != Payment.PaymentStatus.PENDING:
            raise ValueError("This payment has already been reviewed.")

        payment.status = Payment.PaymentStatus.REJECTED
        payment.admin_notes = notes
        payment.reviewed_at = timezone.now()
        payment.save()

        return payment


class ExamAccessService:
    """Handles exam access control for both pre-recorded and live batches."""

    @staticmethod
    def get_accessible_exams(user, batch):
        """
        Return all exams in a batch with an is_unlocked flag for the given user.

        For PRE_RECORDED batches:
        - The exam(s) with the lowest order value are always unlocked.
        - After completing an exam at order N, all exams at the next order
          value unlock.
        - Previously completed exams remain accessible.
        - Same-order exams unlock together (Requirement 5.7).

        For LIVE batches:
        - An exam is unlocked if unlock_datetime is not null AND
          timezone.now() >= unlock_datetime.

        Args:
            user: Django User instance
            batch: Batch instance

        Returns:
            List of dicts with keys: exam, is_unlocked
        """
        exams = Exam.objects.filter(batch=batch, is_active=True).order_by('order')
        result = []

        if batch.batch_type == Batch.BatchType.PRE_RECORDED:
            # Get all distinct order values for completed attempts by this user in this batch
            completed_orders = set(
                ExamAttempt.objects.filter(
                    user=user,
                    exam__batch=batch,
                    is_completed=True,
                ).values_list('exam__order', flat=True)
            )

            # Get all distinct order values in this batch (sorted)
            all_orders = sorted(exams.values_list('order', flat=True).distinct())

            if not all_orders:
                return result

            if completed_orders:
                max_completed_order = max(completed_orders)
                # Find the next order value after the max completed order
                next_order = None
                for order_val in all_orders:
                    if order_val > max_completed_order:
                        next_order = order_val
                        break

                for exam in exams:
                    is_unlocked = (
                        exam.order <= max_completed_order
                        or exam.order == next_order
                    )
                    result.append({'exam': exam, 'is_unlocked': is_unlocked})
            else:
                # No exams completed — only the lowest order exams are unlocked
                lowest_order = all_orders[0]
                for exam in exams:
                    is_unlocked = (exam.order == lowest_order)
                    result.append({'exam': exam, 'is_unlocked': is_unlocked})

        elif batch.batch_type == Batch.BatchType.LIVE:
            now = timezone.now()
            for exam in exams:
                is_unlocked = (
                    exam.unlock_datetime is not None
                    and now >= exam.unlock_datetime
                )
                result.append({'exam': exam, 'is_unlocked': is_unlocked})

        return result

    @staticmethod
    def can_access_exam(user, exam):
        """
        Check whether a user can access a specific exam.

        Dispatches to pre-recorded or live logic based on the exam's batch type.

        For PRE_RECORDED:
        - The exam is accessible if its order is the lowest order in the batch
          (no completions needed), OR if its order <= max completed order, OR
          if its order is the next order value after the max completed order.

        For LIVE:
        - The exam is accessible if unlock_datetime is not null AND
          timezone.now() >= unlock_datetime.

        Args:
            user: Django User instance
            exam: Exam instance

        Returns:
            bool: True if the user can access the exam, False otherwise
        """
        batch = exam.batch

        if batch.batch_type == Batch.BatchType.PRE_RECORDED:
            # Get all distinct order values in this batch (sorted)
            all_orders = sorted(
                Exam.objects.filter(batch=batch, is_active=True)
                .values_list('order', flat=True)
                .distinct()
            )

            if not all_orders:
                return False

            # If this exam's order is the lowest, it's always accessible
            lowest_order = all_orders[0]
            if exam.order == lowest_order:
                return True

            # Get completed attempt orders for this user in this batch
            completed_orders = set(
                ExamAttempt.objects.filter(
                    user=user,
                    exam__batch=batch,
                    is_completed=True,
                ).values_list('exam__order', flat=True)
            )

            if not completed_orders:
                # No completions — only lowest order is accessible (handled above)
                return False

            max_completed_order = max(completed_orders)

            # Exam is accessible if its order <= max completed order
            if exam.order <= max_completed_order:
                return True

            # Or if its order is the next order value after max completed
            next_order = None
            for order_val in all_orders:
                if order_val > max_completed_order:
                    next_order = order_val
                    break

            return exam.order == next_order

        elif batch.batch_type == Batch.BatchType.LIVE:
            if exam.unlock_datetime is None:
                return False
            return timezone.now() >= exam.unlock_datetime

        return False


class ScoringService:
    """Handles exam scoring with negative marking and attempt finalization."""

    @staticmethod
    def calculate_score(attempt, submissions):
        """
        Calculate the score for an exam attempt based on its submissions.

        Scoring rules:
        - +1 for each correct answer (is_correct=True)
        - -0.25 for each incorrect answer (selected_answer is not None and is_correct=False)
        - 0 for each unanswered question (selected_answer is None)

        Deleted questions or questions without a valid correct_answer (1-4) are
        excluded from scoring and do not count toward total_questions.

        Args:
            attempt: ExamAttempt instance
            submissions: queryset or list of ExamSubmission objects for this attempt

        Returns:
            dict with keys: score, total_questions, correct_count,
                  incorrect_count, unanswered_count
        """
        correct_count = 0
        incorrect_count = 0
        unanswered_count = 0

        for submission in submissions:
            # Exclude submissions for deleted questions (question_id exists but
            # the related object may have been deleted — check via the FK)
            try:
                question = submission.question
            except Exception:
                # Question has been deleted; skip this submission
                continue

            # Exclude questions without a valid correct_answer (must be 1-4)
            if question.correct_answer not in (1, 2, 3, 4):
                continue

            if submission.selected_answer is None:
                unanswered_count += 1
            elif submission.is_correct:
                correct_count += 1
            else:
                incorrect_count += 1

        total_questions = correct_count + incorrect_count + unanswered_count
        score = Decimal(correct_count) + Decimal(incorrect_count) * Decimal('-0.25')

        return {
            'score': score,
            'total_questions': total_questions,
            'correct_count': correct_count,
            'incorrect_count': incorrect_count,
            'unanswered_count': unanswered_count,
        }

    @staticmethod
    def finalize_attempt(attempt):
        """
        Finalize an exam attempt by calculating the score and marking it complete.

        Fetches all submissions for the attempt, calculates the score using
        calculate_score(), then updates the attempt with the results and saves.

        Sets:
        - attempt.score
        - attempt.total_questions
        - attempt.correct_count
        - attempt.incorrect_count
        - attempt.unanswered_count
        - attempt.end_time = timezone.now()
        - attempt.is_completed = True

        Args:
            attempt: ExamAttempt instance

        Returns:
            The updated ExamAttempt instance (saved)
        """
        submissions = attempt.submissions.select_related('question').all()
        result = ScoringService.calculate_score(attempt, submissions)

        attempt.score = result['score']
        attempt.total_questions = result['total_questions']
        attempt.correct_count = result['correct_count']
        attempt.incorrect_count = result['incorrect_count']
        attempt.unanswered_count = result['unanswered_count']
        attempt.end_time = timezone.now()
        attempt.is_completed = True
        attempt.save()

        return attempt


from django.core.cache import cache
from django.db.models import Sum, Count, Min, F, ExpressionWrapper, DecimalField, Q
from django.contrib.auth.models import User


class LeaderboardService:
    """Handles leaderboard calculations with caching for batch and exam leaderboards."""

    CACHE_TTL = 300  # seconds

    @staticmethod
    def _get_user_display_name(user):
        """Return user display name: first_name + last_name, or username if blank."""
        full_name = f"{user.first_name} {user.last_name}".strip()
        return full_name or user.username

    @staticmethod
    def get_batch_leaderboard(batch, page, page_size, requesting_user):
        """
        Get the batch leaderboard with aggregated scores across all exams.

        Uses annotated querysets with database-level aggregation (Requirement 10.7).
        Cached with 300-second TTL (Requirement 10.3).

        Sorting: total_score DESC, average_score DESC, earliest_completion ASC.

        Args:
            batch: Batch instance
            page: int, 1-indexed page number
            page_size: int, number of entries per page
            requesting_user: User instance making the request

        Returns:
            dict with keys: entries, total_count, current_user_entry
        """
        cache_key = f"batch_leaderboard_{batch.id}"
        cached_result = cache.get(cache_key)

        if cached_result is not None:
            # Rebuild current_user_entry from cached data for the requesting user
            return LeaderboardService._build_batch_response(
                cached_result, page, page_size, requesting_user
            )

        # Build annotated queryset: users enrolled in this batch with completed attempts
        leaderboard_qs = (
            User.objects.filter(
                admissionlife_enrollments__batch=batch,
                admissionlife_attempts__exam__batch=batch,
                admissionlife_attempts__is_completed=True,
            )
            .distinct()
            .annotate(
                total_score=Sum(
                    'admissionlife_attempts__score',
                    filter=Q(
                        admissionlife_attempts__exam__batch=batch,
                        admissionlife_attempts__is_completed=True,
                    ),
                ),
                total_exams_completed=Count(
                    'admissionlife_attempts',
                    filter=Q(
                        admissionlife_attempts__exam__batch=batch,
                        admissionlife_attempts__is_completed=True,
                    ),
                ),
                earliest_completion=Min(
                    'admissionlife_attempts__end_time',
                    filter=Q(
                        admissionlife_attempts__exam__batch=batch,
                        admissionlife_attempts__is_completed=True,
                    ),
                ),
            )
            .annotate(
                average_score=ExpressionWrapper(
                    F('total_score') / F('total_exams_completed'),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                ),
            )
            .order_by('-total_score', '-average_score', 'earliest_completion')
        )

        # Materialize the full ranked list for caching
        ranked_entries = []
        for rank, user_obj in enumerate(leaderboard_qs, start=1):
            ranked_entries.append({
                'rank': rank,
                'user_id': user_obj.id,
                'user_display_name': LeaderboardService._get_user_display_name(user_obj),
                'total_score': float(user_obj.total_score) if user_obj.total_score else 0.0,
                'total_exams_completed': user_obj.total_exams_completed or 0,
                'average_score': round(float(user_obj.average_score), 2) if user_obj.average_score else 0.0,
            })

        # Cache the full ranked list
        cache.set(cache_key, ranked_entries, LeaderboardService.CACHE_TTL)

        return LeaderboardService._build_batch_response(
            ranked_entries, page, page_size, requesting_user
        )

    @staticmethod
    def _build_batch_response(ranked_entries, page, page_size, requesting_user):
        """Build paginated response from cached ranked entries."""
        total_count = len(ranked_entries)

        # Paginate
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_entries = ranked_entries[start_idx:end_idx]

        # Find requesting user's entry
        current_user_entry = None
        for entry in ranked_entries:
            if entry['user_id'] == requesting_user.id:
                current_user_entry = {
                    'rank': entry['rank'],
                    'user_display_name': entry['user_display_name'],
                    'total_score': entry['total_score'],
                    'total_exams_completed': entry['total_exams_completed'],
                    'average_score': entry['average_score'],
                }
                break

        return {
            'entries': page_entries,
            'total_count': total_count,
            'current_user_entry': current_user_entry,
        }

    @staticmethod
    def get_exam_leaderboard(exam, page, page_size, requesting_user):
        """
        Get the exam leaderboard using each user's best (highest-scoring) attempt.

        Sorting: score DESC, duration ASC, earliest end_time ASC.
        Cached with 300-second TTL (Requirement 10.3).

        Args:
            exam: Exam instance
            page: int, 1-indexed page number
            page_size: int, number of entries per page
            requesting_user: User instance making the request

        Returns:
            dict with keys: entries, total_count, current_user_entry
        """
        cache_key = f"exam_leaderboard_{exam.id}"
        cached_result = cache.get(cache_key)

        if cached_result is not None:
            return LeaderboardService._build_exam_response(
                cached_result, page, page_size, requesting_user
            )

        # For each user, get their best (highest score) completed attempt.
        # Since SQLite doesn't support DISTINCT ON, we pick the best per user in Python.
        # Get all completed attempts sorted by score DESC for efficient best-pick.
        all_attempts = (
            ExamAttempt.objects.filter(
                exam=exam,
                is_completed=True,
            )
            .select_related('user')
            .order_by('-score', 'end_time')
        )

        # Pick best attempt per user (highest score, then shortest duration, then earliest end_time)
        best_per_user = {}
        for attempt in all_attempts:
            if attempt.user_id not in best_per_user:
                best_per_user[attempt.user_id] = attempt
            else:
                current_best = best_per_user[attempt.user_id]
                # Compare: higher score wins
                if float(attempt.score) > float(current_best.score):
                    best_per_user[attempt.user_id] = attempt
                elif float(attempt.score) == float(current_best.score):
                    # Tiebreaker: shorter duration wins
                    attempt_duration = (
                        (attempt.end_time - attempt.start_time).total_seconds()
                        if attempt.end_time and attempt.start_time else float('inf')
                    )
                    current_duration = (
                        (current_best.end_time - current_best.start_time).total_seconds()
                        if current_best.end_time and current_best.start_time else float('inf')
                    )
                    if attempt_duration < current_duration:
                        best_per_user[attempt.user_id] = attempt
                    elif attempt_duration == current_duration:
                        # Tiebreaker: earliest end_time wins
                        if attempt.end_time and current_best.end_time and attempt.end_time < current_best.end_time:
                            best_per_user[attempt.user_id] = attempt

        # Sort the best attempts by: score DESC, duration ASC, end_time ASC
        sorted_attempts = sorted(
            best_per_user.values(),
            key=lambda a: (
                -float(a.score),
                (a.end_time - a.start_time).total_seconds() if a.end_time and a.start_time else float('inf'),
                a.end_time,
            ),
        )

        # Build ranked entries
        ranked_entries = []
        for rank, attempt in enumerate(sorted_attempts, start=1):
            duration_seconds = None
            if attempt.end_time and attempt.start_time:
                duration_seconds = int((attempt.end_time - attempt.start_time).total_seconds())

            ranked_entries.append({
                'rank': rank,
                'user_id': attempt.user_id,
                'user_display_name': LeaderboardService._get_user_display_name(attempt.user),
                'score': float(attempt.score),
                'correct_count': attempt.correct_count,
                'incorrect_count': attempt.incorrect_count,
                'duration_seconds': duration_seconds,
            })

        # Cache the full ranked list
        cache.set(cache_key, ranked_entries, LeaderboardService.CACHE_TTL)

        return LeaderboardService._build_exam_response(
            ranked_entries, page, page_size, requesting_user
        )

    @staticmethod
    def _build_exam_response(ranked_entries, page, page_size, requesting_user):
        """Build paginated response from cached ranked entries."""
        total_count = len(ranked_entries)

        # Paginate
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_entries = ranked_entries[start_idx:end_idx]

        # Find requesting user's entry
        current_user_entry = None
        for entry in ranked_entries:
            if entry['user_id'] == requesting_user.id:
                current_user_entry = {
                    'rank': entry['rank'],
                    'user_display_name': entry['user_display_name'],
                    'score': entry['score'],
                    'correct_count': entry['correct_count'],
                    'incorrect_count': entry['incorrect_count'],
                    'duration_seconds': entry['duration_seconds'],
                }
                break

        return {
            'entries': page_entries,
            'total_count': total_count,
            'current_user_entry': current_user_entry,
        }

    @staticmethod
    def invalidate_cache(batch_id=None, exam_id=None):
        """
        Clear cached leaderboard results for the specified batch and/or exam.

        Args:
            batch_id: int or None — if provided, clears batch leaderboard cache
            exam_id: int or None — if provided, clears exam leaderboard cache
        """
        if batch_id is not None:
            cache.delete(f"batch_leaderboard_{batch_id}")
        if exam_id is not None:
            cache.delete(f"exam_leaderboard_{exam_id}")


class QuestionService:
    """Handles category tree building, descendant resolution, and quiz generation."""

    @staticmethod
    def get_category_tree():
        """
        Build the full hierarchical category tree.

        Returns a list of root categories (level=0) with nested children.
        Fetches all categories in a single query and builds the tree in memory.
        """
        from admissionlife.models import Category

        all_categories = Category.objects.all().order_by('level', 'order', 'name')

        # Build lookup by parent_id
        children_map = {}
        for cat in all_categories:
            parent_id = cat.parent_id
            if parent_id not in children_map:
                children_map[parent_id] = []
            children_map[parent_id].append(cat)

        def build_node(category):
            return {
                'id': category.id,
                'name': category.name,
                'level': category.level,
                'order': category.order,
                'children': [build_node(child) for child in children_map.get(category.id, [])]
            }

        roots = children_map.get(None, [])
        return [build_node(root) for root in roots]

    @staticmethod
    def get_descendant_category_ids(category_id):
        """
        Get all descendant category IDs (inclusive of the given category).

        Uses the Category model's get_descendants() method for recursive traversal.
        Returns a list of IDs including the root category_id.
        """
        from admissionlife.models import Category

        try:
            category = Category.objects.get(pk=category_id)
        except Category.DoesNotExist:
            return [category_id]

        ids = [category.id]
        descendants = category.get_descendants()
        ids.extend([d.id for d in descendants])
        return ids

    @staticmethod
    def generate_practice_quiz(user, categories_config):
        """
        Generate a practice quiz based on category selections.

        Args:
            user: Django User instance
            categories_config: list of dicts with keys:
                - category_id: int
                - question_count: int
                - include_subcategories: bool

        Returns:
            Quiz instance with questions attached, or None if no questions selected.

        Logic:
        1. For each category selection, resolve category IDs (with descendants if requested)
        2. Query questions from those categories
        3. Randomly select up to question_count from each pool
        4. Deduplicate across all selections
        5. If total selected is 0, return None
        6. Create a Quiz with quiz_type=PRACTICE and attach selected questions
        """
        import random
        from admissionlife.models import Quiz, Question

        all_selected_questions = []

        for config in categories_config:
            category_id = config['category_id']
            question_count = config['question_count']
            include_subcategories = config.get('include_subcategories', False)

            if include_subcategories:
                category_ids = QuestionService.get_descendant_category_ids(category_id)
            else:
                category_ids = [category_id]

            available_questions = list(
                Question.objects.filter(category_id__in=category_ids)
                .values_list('id', flat=True)
            )

            # Select up to question_count randomly
            selected = random.sample(
                available_questions,
                min(question_count, len(available_questions))
            )
            all_selected_questions.extend(selected)

        # Deduplicate while preserving randomness
        all_selected_questions = list(set(all_selected_questions))

        if not all_selected_questions:
            return None

        # Create the quiz
        username = user.username if hasattr(user, 'username') else 'guest'
        quiz = Quiz.objects.create(
            name=f"Practice Quiz - {username} - {timezone.now().strftime('%Y%m%d%H%M%S')}",
            quiz_type=Quiz.QuizType.PRACTICE,
            duration_minutes=len(all_selected_questions) * 1,  # 1 min per question
        )
        quiz.questions.set(all_selected_questions)

        return quiz

    @staticmethod
    def start_quiz_attempt(user, quiz):
        """
        Create a QuizAttempt for the user (regular or guest) and return it.

        For regular Django users, sets the user field.
        For guest users (GuestAuthenticatedUser), sets the guest_user field.
        """
        from admissionlife.models import QuizAttempt

        if hasattr(user, 'is_guest') and user.is_guest:
            # Guest user — store via guest_user FK
            attempt = QuizAttempt.objects.create(
                guest_user=user.guest_user, quiz=quiz
            )
        else:
            # Regular authenticated user
            attempt = QuizAttempt.objects.create(user=user, quiz=quiz)
        return attempt

    @staticmethod
    def submit_quiz_attempt(attempt, submissions_data):
        """
        Process quiz submissions, calculate score, and finalize the attempt.

        Args:
            attempt: QuizAttempt instance
            submissions_data: list of dicts with question_id and selected_answer_id

        Returns:
            Updated QuizAttempt instance with score calculated and is_completed=True
        """
        from admissionlife.models import UserSubmission, Answer

        for item in submissions_data:
            question_id = item['question_id']
            selected_answer_id = item.get('selected_answer_id')

            selected_answer = None
            is_correct = False

            if selected_answer_id:
                try:
                    selected_answer = Answer.objects.get(pk=selected_answer_id)
                    is_correct = selected_answer.is_correct
                except Answer.DoesNotExist:
                    pass

            UserSubmission.objects.update_or_create(
                attempt=attempt,
                question_id=question_id,
                defaults={
                    'selected_answer': selected_answer,
                    'is_correct': is_correct,
                }
            )

        # Calculate score
        total_submissions = UserSubmission.objects.filter(attempt=attempt)
        correct_count = total_submissions.filter(is_correct=True).count()

        attempt.score = correct_count
        attempt.is_completed = True
        attempt.end_time = timezone.now()
        attempt.save()

        return attempt


import csv
import io


class QuestionBankService:
    """Handles bulk import of questions from CSV files into the question bank."""

    EXPECTED_COLUMNS = {
        'question_text',
        'option_a',
        'option_b',
        'option_c',
        'option_d',
        'correct_option',
        'category_id',
        'explanation',
    }

    VALID_CORRECT_OPTIONS = {'a', 'b', 'c', 'd'}

    @staticmethod
    def import_questions_from_csv(file):
        """
        Parse a CSV file and bulk-import questions into the question bank.

        Each valid row creates one Question and four Answer records. Invalid rows
        are skipped and collected in the errors list. Each valid row is wrapped in
        its own savepoint so a failure on one row does not roll back others.

        Expected CSV columns (header row required):
            question_text, option_a, option_b, option_c, option_d,
            correct_option, category_id, explanation

        Validation rules per row:
        - question_text must be non-empty
        - option_a, option_b, option_c, option_d must all be non-empty
        - correct_option must be one of: a, b, c, d (case-insensitive)
        - category_id (if provided and non-empty) must reference an existing Category

        Args:
            file: A file-like object (e.g. Django InMemoryUploadedFile).
                  Must be UTF-8 encoded.

        Returns:
            dict with keys:
                total_rows (int): number of data rows processed
                created   (int): number of questions successfully created
                skipped   (int): number of rows that failed validation
                errors    (list[dict]): each entry has 'row' (int) and 'reason' (str)

        Raises:
            UnicodeDecodeError: if the file is not UTF-8 encoded (caller should handle)
        """
        from admissionlife.models import Answer, Category, Question

        decoded = file.read().decode('utf-8')
        reader = csv.DictReader(io.StringIO(decoded))

        # Validate that the CSV has the expected header columns
        if reader.fieldnames is None:
            return {
                'total_rows': 0,
                'created': 0,
                'skipped': 0,
                'errors': [],
            }

        actual_columns = {col.strip() for col in reader.fieldnames}
        missing_columns = QuestionBankService.EXPECTED_COLUMNS - actual_columns
        if missing_columns:
            return {
                'total_rows': 0,
                'created': 0,
                'skipped': 0,
                'errors': [
                    {
                        'row': 0,
                        'reason': f"Missing required columns: {', '.join(sorted(missing_columns))}",
                    }
                ],
            }

        rows = list(reader)
        total_rows = len(rows)
        created = 0
        errors = []

        for row_num, row in enumerate(rows, start=2):  # row 1 is the header
            # Strip whitespace from all values
            question_text = (row.get('question_text') or '').strip()
            option_a = (row.get('option_a') or '').strip()
            option_b = (row.get('option_b') or '').strip()
            option_c = (row.get('option_c') or '').strip()
            option_d = (row.get('option_d') or '').strip()
            correct_option = (row.get('correct_option') or '').strip().lower()
            category_id_raw = (row.get('category_id') or '').strip()
            explanation = (row.get('explanation') or '').strip()

            # --- Validation ---

            if not question_text:
                errors.append({'row': row_num, 'reason': 'Missing or empty question_text.'})
                continue

            if not option_a:
                errors.append({'row': row_num, 'reason': 'Missing or empty option_a.'})
                continue

            if not option_b:
                errors.append({'row': row_num, 'reason': 'Missing or empty option_b.'})
                continue

            if not option_c:
                errors.append({'row': row_num, 'reason': 'Missing or empty option_c.'})
                continue

            if not option_d:
                errors.append({'row': row_num, 'reason': 'Missing or empty option_d.'})
                continue

            if correct_option not in QuestionBankService.VALID_CORRECT_OPTIONS:
                errors.append({
                    'row': row_num,
                    'reason': f"Invalid correct_option value: '{correct_option}'. Must be one of: a, b, c, d.",
                })
                continue

            # Resolve category (optional)
            category = None
            if category_id_raw:
                try:
                    category_id = int(category_id_raw)
                except ValueError:
                    errors.append({
                        'row': row_num,
                        'reason': f"Invalid category_id '{category_id_raw}': must be an integer.",
                    })
                    continue

                try:
                    category = Category.objects.get(pk=category_id)
                except Category.DoesNotExist:
                    errors.append({
                        'row': row_num,
                        'reason': f"Category with id {category_id} does not exist.",
                    })
                    continue

            # --- Creation (each row in its own savepoint) ---
            try:
                with transaction.atomic():
                    question = Question.objects.create(
                        question_text=question_text,
                        explanation=explanation or None,
                        category=category,
                    )

                    options = {
                        'a': option_a,
                        'b': option_b,
                        'c': option_c,
                        'd': option_d,
                    }

                    for letter, text in options.items():
                        Answer.objects.create(
                            question=question,
                            text=text,
                            is_correct=(letter == correct_option),
                        )

                created += 1

            except Exception as exc:
                errors.append({
                    'row': row_num,
                    'reason': f"Unexpected error: {str(exc)}",
                })

        skipped = len(errors)

        return {
            'total_rows': total_rows,
            'created': created,
            'skipped': skipped,
            'errors': errors,
        }
