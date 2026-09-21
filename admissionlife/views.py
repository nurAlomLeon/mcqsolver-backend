import csv
import io

from rest_framework import mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from django.db import transaction

from .models import (
    Answer, Category, Label, Question, QuestionReport, SavedQuestion, Quiz, QuizAttempt,
    Batch, Enrollment, Exam, ExamQuestion, Payment,
)
from .pagination import AdmissionLifePagination
from .permissions import IsAuthenticatedUser
from .serializers import (
    BatchCreateUpdateSerializer,
    BatchDetailSerializer,
    BatchListSerializer,
    CategoryAdminSerializer,
    CategorySerializer,
    CategoryTreeSerializer,
    ExamCreateUpdateSerializer,
    ExamDetailSerializer,
    ExamListSerializer,
    LabelSerializer,
    PaymentAdminSerializer,
    PaymentListSerializer,
    PaymentSubmitSerializer,
    PracticeQuizConfigSerializer,
    PracticeQuizResponseSerializer,
    PracticeQuizAttemptStartSerializer,
    PracticeQuizAttemptResultSerializer,
    PracticeQuizSubmissionSerializer,
    QuestionAdminCreateUpdateSerializer,
    QuestionDetailSerializer,
    QuestionReportCreateSerializer,
    SavedQuestionCreateSerializer,
    SavedQuestionListSerializer,
)
from .services import EnrollmentService, ExamAccessService, LeaderboardService, PaymentService, QuestionBankService, QuestionService


# =============================================================================
# Batch Views
# =============================================================================


class BatchViewSet(ModelViewSet):
    """
    ViewSet for Batch CRUD operations.

    - list/retrieve: accessible to any authenticated user
      (non-admin users see only active batches)
    - create/update/partial_update/destroy: admin only
    """

    pagination_class = AdmissionLifePagination

    def get_queryset(self):
        qs = Batch.objects.prefetch_related('exams', 'enrollments')
        if self.action == 'list' and not (
            self.request.user and self.request.user.is_staff
        ):
            qs = qs.filter(is_active=True)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return BatchListSerializer
        if self.action == 'retrieve':
            return BatchDetailSerializer
        return BatchCreateUpdateSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated()]
        return [IsAdminUser()]


# =============================================================================
# Category Views
# =============================================================================


class CategoryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    """
    Read-only viewset for browsing the category hierarchy.

    Filters:
    - level: integer (0, 1, 2)
    - parent: integer (category id)

    Custom actions:
    - tree/: returns full nested hierarchy
    - {id}/children/: returns direct children
    """

    permission_classes = [IsAuthenticatedUser]
    pagination_class = AdmissionLifePagination
    serializer_class = CategorySerializer

    def get_queryset(self):
        qs = Category.objects.all()
        level = self.request.query_params.get('level')
        parent = self.request.query_params.get('parent')
        if level is not None:
            qs = qs.filter(level=int(level))
        if parent is not None:
            qs = qs.filter(parent_id=int(parent))
        return qs

    @action(detail=False, methods=['get'])
    def tree(self, request):
        """Return full hierarchical category tree."""
        tree_data = QuestionService.get_category_tree()
        serializer = CategoryTreeSerializer(tree_data, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def children(self, request, pk=None):
        """Return direct children of a category."""
        category = self.get_object()
        children = category.children.all().order_by('order', 'name')
        serializer = CategorySerializer(children, many=True)
        return Response(serializer.data)


class QuestionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, GenericViewSet):
    """
    Read-only viewset for browsing questions.

    Filters:
    - category: integer (category id)
    - category_level: 'all' to include descendant categories
    """

    permission_classes = [IsAuthenticatedUser]
    pagination_class = AdmissionLifePagination
    serializer_class = QuestionDetailSerializer

    def get_queryset(self):
        qs = Question.objects.prefetch_related('answers', 'labels').select_related('category')
        category_id = self.request.query_params.get('category')
        category_level = self.request.query_params.get('category_level')

        if category_id:
            if category_level == 'all':
                category_ids = QuestionService.get_descendant_category_ids(int(category_id))
                qs = qs.filter(category_id__in=category_ids)
            else:
                qs = qs.filter(category_id=int(category_id))
        return qs


# =============================================================================
# Payment Views
# =============================================================================


class PaymentViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, GenericViewSet):
    """
    ViewSet for payment operations.

    - create: Authenticated users submit a payment (calls PaymentService)
    - list: Returns requesting user's payments (admin sees all with PaymentAdminSerializer)
    - approve: Admin-only action to approve a pending payment
    - reject: Admin-only action to reject a pending payment (requires admin_notes)
    """

    pagination_class = AdmissionLifePagination

    def get_queryset(self):
        if hasattr(self.request.user, 'is_guest') and self.request.user.is_guest:
            return Payment.objects.none()
        if self.request.user.is_staff:
            return Payment.objects.select_related('user', 'batch').all()
        return Payment.objects.select_related('batch').filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == 'create':
            return PaymentSubmitSerializer
        if self.request.user.is_staff:
            return PaymentAdminSerializer
        return PaymentListSerializer

    def get_permissions(self):
        if self.action in ('approve', 'reject'):
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        try:
            payment = PaymentService.submit_payment(request.user, serializer.validated_data)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        response_serializer = PaymentListSerializer(payment)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        payment = self.get_object()

        try:
            PaymentService.approve_payment(payment, request.user)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'detail': 'Payment approved successfully.'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        payment = self.get_object()

        admin_notes = request.data.get('admin_notes')
        if not admin_notes:
            return Response(
                {'admin_notes': 'This field is required when rejecting a payment.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            PaymentService.reject_payment(payment, request.user, admin_notes)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'detail': 'Payment rejected successfully.'}, status=status.HTTP_200_OK)


# =============================================================================
# Enrollment Views
# =============================================================================


class EnrollmentCheckView(APIView):
    """
    Check whether the authenticated user is enrolled in a specific batch.

    GET /enrollments/check/{batch_id}/ → {"is_enrolled": true/false}
    Guest users always get {"is_enrolled": false}.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, batch_id):
        # Guest users are never enrolled
        if hasattr(request.user, 'is_guest') and request.user.is_guest:
            return Response({'is_enrolled': False}, status=status.HTTP_200_OK)

        try:
            batch = Batch.objects.get(pk=batch_id)
        except Batch.DoesNotExist:
            return Response(
                {'detail': 'Batch not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_enrolled = EnrollmentService.check_enrollment(request.user, batch)
        return Response({'is_enrolled': is_enrolled}, status=status.HTTP_200_OK)


# =============================================================================
# Exam Views
# =============================================================================


class ExamViewSet(ModelViewSet):
    """
    ViewSet for Exam CRUD operations and custom actions.

    - list/retrieve: accessible to any authenticated user
      (for enrolled users, list includes is_unlocked flag via ExamAccessService)
    - create/update/partial_update/destroy: admin only
    - import_csv: admin only — imports questions from CSV file
    - set_unlock_datetime: admin only — sets/clears unlock_datetime for live batch exams
    """

    pagination_class = AdmissionLifePagination

    def get_queryset(self):
        qs = Exam.objects.select_related('batch').prefetch_related('questions')
        batch_id = self.request.query_params.get('batch_id')
        if batch_id:
            qs = qs.filter(batch_id=batch_id)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return ExamListSerializer
        if self.action == 'retrieve':
            return ExamDetailSerializer
        return ExamCreateUpdateSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated()]
        return [IsAdminUser()]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        batch_id = request.query_params.get('batch_id')

        # For non-admin users with a batch_id, include is_unlocked via ExamAccessService
        is_unlocked_map = {}
        if batch_id and not request.user.is_staff:
            # Guest users can't be enrolled, so no exams are unlocked for them
            if not (hasattr(request.user, 'is_guest') and request.user.is_guest):
                try:
                    batch = Batch.objects.get(pk=batch_id)
                    accessible_exams = ExamAccessService.get_accessible_exams(request.user, batch)
                    is_unlocked_map = {
                        item['exam'].id: item['is_unlocked'] for item in accessible_exams
                    }
                except Batch.DoesNotExist:
                    pass

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(
                page, many=True, context={'request': request, 'is_unlocked': is_unlocked_map}
            )
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(
            queryset, many=True, context={'request': request, 'is_unlocked': is_unlocked_map}
        )
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='import-csv')
    def import_csv(self, request, pk=None):
        """
        Import questions from a CSV file into the specified exam.

        CSV columns: Question Title, Answer 1, Answer 2, Answer 3, Answer 4,
                     Correct Answer (1-4), Explanation

        Returns summary: {"total_rows": N, "created": M, "skipped": K, "errors": [...]}
        """
        exam = self.get_object()

        # Check file is present
        if 'file' not in request.FILES:
            return Response(
                {'file': 'No file was submitted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        csv_file = request.FILES['file']

        # Validate file size (max 5MB)
        if csv_file.size > 5 * 1024 * 1024:
            return Response(
                {'file': 'CSV file must be under 5 MB.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Read and decode the file
        try:
            decoded_file = csv_file.read().decode('utf-8')
        except UnicodeDecodeError:
            return Response(
                {'file': 'File must be UTF-8 encoded.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reader = csv.reader(io.StringIO(decoded_file))

        # Skip header row
        try:
            next(reader)
        except StopIteration:
            return Response(
                {'file': 'CSV file contains no data rows.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rows = list(reader)
        if not rows:
            return Response(
                {'file': 'CSV file contains no data rows.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        valid_questions = []
        errors = []
        total_rows = len(rows)

        for row_num, row in enumerate(rows, start=2):  # start=2 because row 1 is header
            # Validate row has enough columns
            if len(row) < 6:
                errors.append({
                    'row': row_num,
                    'reason': 'Row has fewer than 6 columns.',
                })
                continue

            question_text = row[0].strip() if row[0] else ''
            answer_1 = row[1].strip() if len(row) > 1 and row[1] else ''
            answer_2 = row[2].strip() if len(row) > 2 and row[2] else ''
            answer_3 = row[3].strip() if len(row) > 3 and row[3] else ''
            answer_4 = row[4].strip() if len(row) > 4 and row[4] else ''
            correct_answer_str = row[5].strip() if len(row) > 5 and row[5] else ''
            explanation = row[6].strip() if len(row) > 6 and row[6] else ''

            # Validate question_text is non-empty
            if not question_text:
                errors.append({
                    'row': row_num,
                    'reason': 'Missing or empty Question Title.',
                })
                continue

            # Validate all 4 answers are provided
            if not answer_1 or not answer_2 or not answer_3 or not answer_4:
                errors.append({
                    'row': row_num,
                    'reason': 'All four answer fields must be provided.',
                })
                continue

            # Validate answer lengths (max 255 chars)
            if len(answer_1) > 255 or len(answer_2) > 255 or len(answer_3) > 255 or len(answer_4) > 255:
                errors.append({
                    'row': row_num,
                    'reason': 'Answer field exceeds 255 characters.',
                })
                continue

            # Validate correct_answer is integer 1-4
            try:
                correct_answer = int(correct_answer_str)
            except (ValueError, TypeError):
                errors.append({
                    'row': row_num,
                    'reason': 'Correct Answer must be an integer between 1 and 4.',
                })
                continue

            if correct_answer not in (1, 2, 3, 4):
                errors.append({
                    'row': row_num,
                    'reason': 'Correct Answer must be an integer between 1 and 4.',
                })
                continue

            valid_questions.append(
                ExamQuestion(
                    exam=exam,
                    question_text=question_text,
                    answer_1=answer_1,
                    answer_2=answer_2,
                    answer_3=answer_3,
                    answer_4=answer_4,
                    correct_answer=correct_answer,
                    explanation=explanation,
                )
            )

        # Bulk create valid questions
        created_count = 0
        if valid_questions:
            ExamQuestion.objects.bulk_create(valid_questions)
            created_count = len(valid_questions)

        return Response({
            'total_rows': total_rows,
            'created': created_count,
            'skipped': len(errors),
            'errors': errors,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='set-unlock-datetime')
    def set_unlock_datetime(self, request, pk=None):
        """
        Set or clear the unlock_datetime for an exam (used for live batch exams).

        Accepts: {"unlock_datetime": "ISO8601" or null}
        Returns: Updated exam data.
        """
        exam = self.get_object()

        if 'unlock_datetime' not in request.data:
            return Response(
                {'unlock_datetime': 'This field is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        unlock_datetime = request.data.get('unlock_datetime')

        # Allow null to clear the datetime
        if unlock_datetime is not None:
            from django.utils.dateparse import parse_datetime
            parsed = parse_datetime(unlock_datetime)
            if parsed is None:
                return Response(
                    {'unlock_datetime': 'Invalid datetime format. Use ISO 8601.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            exam.unlock_datetime = parsed
        else:
            exam.unlock_datetime = None

        exam.save()

        serializer = ExamDetailSerializer(exam)
        return Response(serializer.data, status=status.HTTP_200_OK)


# =============================================================================
# Exam Attempt Views
# =============================================================================


class ExamAttemptStartView(APIView):
    """
    Start an exam attempt for the authenticated user.

    POST /exam-attempts/{exam_id}/start/
    - Checks user is enrolled in the exam's batch
    - Checks exam is accessible (sequential/time-based via ExamAccessService)
    - Checks no prior completed attempt exists
    - Creates ExamAttempt, returns questions without correct answers
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, exam_id):
        from .models import Enrollment, ExamAttempt, ExamQuestion
        from .serializers import ExamAttemptStartSerializer, ExamQuestionSerializer
        from .services import ExamAccessService

        # Guest users cannot start exam attempts
        if hasattr(request.user, 'is_guest') and request.user.is_guest:
            return Response(
                {'detail': 'Guests cannot take exams. Please sign in.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get the exam
        try:
            exam = Exam.objects.select_related('batch').get(pk=exam_id)
        except Exam.DoesNotExist:
            return Response(
                {'detail': 'Exam not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check enrollment
        if not Enrollment.objects.filter(user=request.user, batch=exam.batch).exists():
            return Response(
                {'detail': 'You are not enrolled in this batch.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check if user already completed this exam
        existing_attempt = ExamAttempt.objects.filter(
            user=request.user, exam=exam, is_completed=True
        ).first()
        if existing_attempt:
            return Response(
                {'detail': 'You have already completed this exam.', 'attempt_id': existing_attempt.id},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check exam access (sequential for pre-recorded, time-based for live)
        if not ExamAccessService.can_access_exam(request.user, exam):
            batch = exam.batch
            if batch.batch_type == Batch.BatchType.LIVE:
                if exam.unlock_datetime is None:
                    return Response(
                        {'detail': 'This exam has not been scheduled yet.'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                else:
                    return Response(
                        {
                            'detail': f'This exam unlocks at {exam.unlock_datetime.isoformat()}.',
                            'unlock_datetime': exam.unlock_datetime.isoformat(),
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )
            else:
                # Pre-recorded: find the prerequisite exam
                return Response(
                    {'detail': 'This exam is locked. Complete the prerequisite exam first.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Create the attempt
        attempt = ExamAttempt.objects.create(user=request.user, exam=exam)

        # Get questions without correct answers
        questions = ExamQuestion.objects.filter(exam=exam)

        # Build response
        serializer = ExamAttemptStartSerializer({
            'attempt_id': attempt.id,
            'questions': questions,
        })

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ExamAttemptSubmitView(APIView):
    """
    Submit answers for an exam attempt.

    POST /exam-attempts/{attempt_id}/submit/
    - Checks attempt belongs to user and is not already completed
    - Accepts {"submissions": [{question_id, selected_answer}, ...]}
    - Creates ExamSubmission records, determines is_correct for each
    - Calls ScoringService.finalize_attempt()
    - Calls LeaderboardService.invalidate_cache(batch_id, exam_id)
    - Returns ExamAttemptResultSerializer response
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, attempt_id):
        from .models import ExamAttempt, ExamQuestion, ExamSubmission
        from .serializers import ExamAttemptResultSerializer, ExamSubmissionSerializer
        from .services import LeaderboardService, ScoringService

        # Guest users cannot submit exam attempts
        if hasattr(request.user, 'is_guest') and request.user.is_guest:
            return Response(
                {'detail': 'Guests cannot take exams. Please sign in.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get the attempt
        try:
            attempt = ExamAttempt.objects.select_related('exam__batch').get(pk=attempt_id)
        except ExamAttempt.DoesNotExist:
            return Response(
                {'detail': 'Attempt not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check attempt belongs to user
        if attempt.user_id != request.user.id:
            return Response(
                {'detail': 'Attempt not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check attempt is not already completed
        if attempt.is_completed:
            return Response(
                {'detail': 'You have already completed this exam.', 'attempt_id': attempt.id},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate submissions
        submissions_data = request.data.get('submissions', [])
        if not isinstance(submissions_data, list):
            return Response(
                {'detail': 'submissions must be a list.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate each submission entry
        for item in submissions_data:
            serializer = ExamSubmissionSerializer(data=item)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Get all questions for this exam (for determining is_correct)
        exam_questions = {
            q.id: q for q in ExamQuestion.objects.filter(exam=attempt.exam)
        }

        # Create ExamSubmission records
        submission_objects = []
        for item in submissions_data:
            question_id = item.get('question_id')
            selected_answer = item.get('selected_answer')

            # Skip if question doesn't belong to this exam
            if question_id not in exam_questions:
                continue

            question = exam_questions[question_id]

            # Determine is_correct
            is_correct = (
                selected_answer is not None
                and selected_answer == question.correct_answer
            )

            submission_objects.append(
                ExamSubmission(
                    attempt=attempt,
                    question=question,
                    selected_answer=selected_answer,
                    is_correct=is_correct,
                )
            )

        # Bulk create submissions
        if submission_objects:
            ExamSubmission.objects.bulk_create(submission_objects, ignore_conflicts=True)

        # Finalize the attempt (calculates score, marks completed)
        attempt = ScoringService.finalize_attempt(attempt)

        # Invalidate leaderboard cache
        LeaderboardService.invalidate_cache(
            batch_id=attempt.exam.batch_id,
            exam_id=attempt.exam_id,
        )

        # Return result
        serializer = ExamAttemptResultSerializer(attempt)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ExamAttemptResultView(APIView):
    """
    Get the result of a completed exam attempt.

    GET /exam-attempts/{attempt_id}/result/
    - Checks attempt belongs to user
    - Returns ExamAttemptResultSerializer response
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, attempt_id):
        from .models import ExamAttempt
        from .serializers import ExamAttemptResultSerializer

        # Guest users cannot access exam results
        if hasattr(request.user, 'is_guest') and request.user.is_guest:
            return Response(
                {'detail': 'Guests cannot access exam results. Please sign in.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get the attempt
        try:
            attempt = ExamAttempt.objects.select_related('exam').get(pk=attempt_id)
        except ExamAttempt.DoesNotExist:
            return Response(
                {'detail': 'Attempt not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check attempt belongs to user
        if attempt.user_id != request.user.id:
            return Response(
                {'detail': 'Attempt not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ExamAttemptResultSerializer(attempt)
        return Response(serializer.data, status=status.HTTP_200_OK)


# =============================================================================
# Leaderboard Views
# =============================================================================


class BatchLeaderboardView(APIView):
    """
    Get the batch leaderboard with aggregated scores across all exams.

    GET /batches/{batch_id}/leaderboard/
    - Checks batch exists (404 if not)
    - Checks user is enrolled in the batch (403 if not)
    - Supports pagination via page and page_size query params
    - Returns entries, total_count, and current_user_entry (requesting user's rank)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, batch_id):
        # Guest users cannot access leaderboards
        if hasattr(request.user, 'is_guest') and request.user.is_guest:
            return Response(
                {'detail': 'Guests cannot access leaderboards. Please sign in.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check batch exists
        try:
            batch = Batch.objects.get(pk=batch_id)
        except Batch.DoesNotExist:
            return Response(
                {'detail': 'Batch not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check user is enrolled
        if not Enrollment.objects.filter(user=request.user, batch=batch).exists():
            return Response(
                {'detail': 'You are not enrolled in this batch.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Parse pagination params
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        page_size = min(page_size, 100)

        # Get leaderboard data
        result = LeaderboardService.get_batch_leaderboard(batch, page, page_size, request.user)

        return Response(result, status=status.HTTP_200_OK)


class ExamLeaderboardView(APIView):
    """
    Get the exam leaderboard using each user's best attempt.

    GET /exams/{exam_id}/leaderboard/
    - Checks exam exists (404 if not)
    - Checks user is enrolled in the exam's batch (403 if not)
    - Supports pagination via page and page_size query params
    - Returns entries, total_count, and current_user_entry (requesting user's rank)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id):
        # Guest users cannot access leaderboards
        if hasattr(request.user, 'is_guest') and request.user.is_guest:
            return Response(
                {'detail': 'Guests cannot access leaderboards. Please sign in.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check exam exists
        try:
            exam = Exam.objects.select_related('batch').get(pk=exam_id)
        except Exam.DoesNotExist:
            return Response(
                {'detail': 'Exam not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check user is enrolled in the exam's batch
        if not Enrollment.objects.filter(user=request.user, batch=exam.batch).exists():
            return Response(
                {'detail': 'You are not enrolled in this batch.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Parse pagination params
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        page_size = min(page_size, 100)

        # Get leaderboard data
        result = LeaderboardService.get_exam_leaderboard(exam, page, page_size, request.user)

        return Response(result, status=status.HTTP_200_OK)


# =============================================================================
# Question Report Views
# =============================================================================


class QuestionReportView(APIView):
    """
    POST-only endpoint for reporting a problematic question.
    """

    permission_classes = [IsAuthenticatedUser]

    def post(self, request):
        serializer = QuestionReportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if hasattr(user, 'is_guest') and user.is_guest:
            # Guest users can report but we store without user FK
            serializer.save()
        else:
            serializer.save(user=user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# =============================================================================
# Saved Question Views
# =============================================================================


class SavedQuestionViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """
    ViewSet for managing saved/bookmarked questions.

    Filters:
    - category: filter by category name (case-insensitive contains)
    - label: filter by label name (case-insensitive contains)
    - search: search by question_text content
    """

    permission_classes = [IsAuthenticatedUser]
    pagination_class = AdmissionLifePagination

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'is_guest') and user.is_guest:
            # Guest user — filter by guest_user FK
            qs = (
                SavedQuestion.objects.filter(guest_user=user.guest_user)
                .select_related('question__category')
                .prefetch_related('question__answers', 'question__labels')
                .order_by('-saved_at')
            )
        else:
            # Regular user — filter by user FK
            qs = (
                SavedQuestion.objects.filter(user=user, guest_user__isnull=True)
                .select_related('question__category')
                .prefetch_related('question__answers', 'question__labels')
                .order_by('-saved_at')
            )

        # Apply filters
        category = self.request.query_params.get('category')
        label = self.request.query_params.get('label')
        search = self.request.query_params.get('search')

        if category:
            qs = qs.filter(question__category__name__icontains=category)
        if label:
            qs = qs.filter(question__labels__name__icontains=label)
        if search:
            qs = qs.filter(question__question_text__icontains=search)
        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return SavedQuestionCreateSerializer
        return SavedQuestionListSerializer

    def perform_create(self, serializer):
        user = self.request.user
        if hasattr(user, 'is_guest') and user.is_guest:
            serializer.save(guest_user=user.guest_user)
        else:
            serializer.save(user=user)


# =============================================================================
# Practice Quiz Views
# =============================================================================


class PracticeQuizView(APIView):
    """
    POST endpoint to generate a practice quiz from category selections.

    Request body:
    {
        "categories": [
            {"category_id": 5, "question_count": 10, "include_subcategories": true},
            {"category_id": 12, "question_count": 5, "include_subcategories": false}
        ]
    }
    """

    permission_classes = [IsAuthenticatedUser]

    def post(self, request):
        serializer = PracticeQuizConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        quiz = QuestionService.generate_practice_quiz(
            user=request.user,
            categories_config=serializer.validated_data['categories']
        )

        if quiz is None:
            return Response(
                {'detail': 'No questions available for the selected categories.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        response_serializer = PracticeQuizResponseSerializer(quiz)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class PracticeQuizAttemptStartView(APIView):
    """
    Start a practice quiz attempt for the authenticated user.

    POST /practice-quizzes/{quiz_id}/start/
    - Creates a QuizAttempt via QuestionService.start_quiz_attempt()
    - Returns questions without correct answers using PracticeQuizAttemptStartSerializer
    """

    permission_classes = [IsAuthenticatedUser]

    def post(self, request, quiz_id):
        # Get the quiz
        try:
            quiz = Quiz.objects.prefetch_related('questions__answers').get(pk=quiz_id)
        except Quiz.DoesNotExist:
            return Response(
                {'detail': 'Quiz not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Create the attempt
        attempt = QuestionService.start_quiz_attempt(user=request.user, quiz=quiz)

        # Get questions for the quiz (without correct answers)
        questions = quiz.questions.prefetch_related('answers').all()

        serializer = PracticeQuizAttemptStartSerializer({
            'attempt_id': attempt.id,
            'questions': questions,
        })

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class PracticeQuizAttemptSubmitView(APIView):
    """
    Submit answers for a practice quiz attempt.

    POST /practice-quizzes/{attempt_id}/submit/
    - Validates attempt ownership (attempt.user == request.user, else 404)
    - Validates attempt is not already completed (else 400)
    - Calls QuestionService.submit_quiz_attempt()
    - Returns result using PracticeQuizAttemptResultSerializer
    """

    permission_classes = [IsAuthenticatedUser]

    def post(self, request, attempt_id):
        # Get the attempt
        try:
            attempt = QuizAttempt.objects.select_related('quiz').get(pk=attempt_id)
        except QuizAttempt.DoesNotExist:
            return Response(
                {'detail': 'Not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Validate attempt ownership (regular user or guest)
        if not self._is_owner(request.user, attempt):
            return Response(
                {'detail': 'Not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Validate attempt is not already completed
        if attempt.is_completed:
            return Response(
                {'detail': 'This attempt has already been completed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate submissions input
        submissions_data = request.data.get('submissions', [])
        if not isinstance(submissions_data, list):
            return Response(
                {'detail': 'submissions must be a list.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate each submission entry
        for item in submissions_data:
            serializer = PracticeQuizSubmissionSerializer(data=item)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Submit the attempt
        attempt = QuestionService.submit_quiz_attempt(attempt, submissions_data)

        result_serializer = PracticeQuizAttemptResultSerializer(attempt)
        return Response(result_serializer.data, status=status.HTTP_200_OK)

    @staticmethod
    def _is_owner(user, attempt):
        """Check if the request user owns this attempt (regular or guest)."""
        if hasattr(user, 'is_guest') and user.is_guest:
            return attempt.guest_user_id == user.guest_user.id
        return attempt.user_id == user.id


class PracticeQuizAttemptResultView(APIView):
    """
    Get the result of a completed practice quiz attempt.

    GET /practice-quizzes/{attempt_id}/result/
    - Returns completed attempt with per-question details using PracticeQuizAttemptResultSerializer
    - 404 if attempt doesn't belong to user
    """

    permission_classes = [IsAuthenticatedUser]

    def get(self, request, attempt_id):
        # Get the attempt
        try:
            attempt = QuizAttempt.objects.select_related('quiz').get(pk=attempt_id)
        except QuizAttempt.DoesNotExist:
            return Response(
                {'detail': 'Not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Validate attempt ownership (regular user or guest)
        if not self._is_owner(request.user, attempt):
            return Response(
                {'detail': 'Not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = PracticeQuizAttemptResultSerializer(attempt)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @staticmethod
    def _is_owner(user, attempt):
        """Check if the request user owns this attempt (regular or guest)."""
        if hasattr(user, 'is_guest') and user.is_guest:
            return attempt.guest_user_id == user.guest_user.id
        return attempt.user_id == user.id

# =============================================================================
# Admin Question Bank Management Views
# =============================================================================


class AdminQuestionViewSet(ModelViewSet):
    """
    Admin-only ViewSet for full CRUD on questions with nested answers.

    - list/retrieve: returns questions with nested answers and labels
    - create: creates question with nested answers and labels atomically
    - update/partial_update: replaces answers atomically
    - destroy: deletes question (cascades to answers)

    Filters:
    - category: integer (category id)
    - include_descendants: 'true' to include descendant categories
    """

    permission_classes = [IsAdminUser]
    pagination_class = AdmissionLifePagination

    def get_queryset(self):
        qs = Question.objects.prefetch_related('answers', 'labels').select_related('category')
        category_id = self.request.query_params.get('category')
        include_descendants = self.request.query_params.get('include_descendants')

        if category_id:
            if include_descendants == 'true':
                category_ids = QuestionService.get_descendant_category_ids(int(category_id))
                qs = qs.filter(category_id__in=category_ids)
            else:
                qs = qs.filter(category_id=int(category_id))
        return qs

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return QuestionAdminCreateUpdateSerializer
        return QuestionDetailSerializer

    def perform_create(self, serializer):
        with transaction.atomic():
            answers_data = serializer.validated_data.pop('answers')
            labels_data = serializer.validated_data.pop('labels', [])

            question = Question.objects.create(**serializer.validated_data)

            # Create nested answers
            for answer_data in answers_data:
                Answer.objects.create(question=question, **answer_data)

            # Set labels (M2M)
            if labels_data:
                question.labels.set(labels_data)

    def perform_update(self, serializer):
        with transaction.atomic():
            answers_data = serializer.validated_data.pop('answers', None)
            labels_data = serializer.validated_data.pop('labels', None)

            question = serializer.instance
            for attr, value in serializer.validated_data.items():
                setattr(question, attr, value)
            question.save()

            # Replace answers if provided
            if answers_data is not None:
                question.answers.all().delete()
                for answer_data in answers_data:
                    Answer.objects.create(question=question, **answer_data)

            # Update labels if provided
            if labels_data is not None:
                question.labels.set(labels_data)

    @action(detail=False, methods=['post'], url_path='import-csv')
    def import_csv(self, request):
        """
        Bulk-import questions from a CSV file into the question bank.

        CSV columns: question_text, option_a, option_b, option_c, option_d,
                     correct_option, category_id, explanation

        Returns summary: {"total_rows": N, "created": M, "skipped": K, "errors": [...]}
        """
        # Check file is present
        if 'file' not in request.FILES:
            return Response(
                {'file': 'No file was submitted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        csv_file = request.FILES['file']

        # Validate file size (max 5 MB)
        if csv_file.size > 5 * 1024 * 1024:
            return Response(
                {'file': 'CSV file must be under 5 MB.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate UTF-8 encoding
        try:
            csv_file.read().decode('utf-8')
            csv_file.seek(0)
        except UnicodeDecodeError:
            return Response(
                {'file': 'File must be UTF-8 encoded.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            summary = QuestionBankService.import_questions_from_csv(csv_file)
        except UnicodeDecodeError:
            return Response(
                {'file': 'File must be UTF-8 encoded.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(summary, status=status.HTTP_200_OK)

# Admin Label Views (Question Bank Management)
# =============================================================================


class AdminLabelViewSet(ModelViewSet):
    """
    Admin-only ViewSet for full CRUD on Labels.

    - list: GET /admin/labels/
    - create: POST /admin/labels/
    - retrieve: GET /admin/labels/{id}/
    - update: PUT /admin/labels/{id}/
    - destroy: DELETE /admin/labels/{id}/
    """

    queryset = Label.objects.all()
    serializer_class = LabelSerializer
    permission_classes = [IsAdminUser]

# =============================================================================
# Admin Category Views
# =============================================================================


class AdminCategoryViewSet(ModelViewSet):
    """
    Admin-only ViewSet for full CRUD on categories.

    - list/retrieve: returns categories using CategorySerializer
    - create/update: uses CategoryAdminSerializer, auto-calculates level
    - destroy: rejects if category has children (400), otherwise nulls
      associated questions and deletes
    """

    permission_classes = [IsAdminUser]
    queryset = Category.objects.all()

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return CategoryAdminSerializer
        return CategorySerializer

    def perform_create(self, serializer):
        # Level is auto-calculated by the Category model's save() method
        serializer.save()

    def perform_update(self, serializer):
        # Level is recalculated by the Category model's save() method if parent changes
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        # Reject deletion if category has children
        if Category.objects.filter(parent=instance).exists():
            return Response(
                {'detail': 'Cannot delete category with child categories.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Set category=null on all questions associated with this leaf category
        Question.objects.filter(category=instance).update(category=None)
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
