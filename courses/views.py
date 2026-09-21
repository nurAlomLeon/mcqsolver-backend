from django.db import models, transaction
from django.db.models import Count, Exists, OuterRef, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.cache_utils import NS_COURSE_HOME, TTL_SHORT, cached_payload
from api.pagination import StandardResultsSetPagination
from api.permissions import IsAuthenticatedUserOnly

from .models import (
    Course,
    CourseAnswer,
    CourseEnrollment,
    CourseQuestion,
    CourseQuiz,
    CourseQuizAttempt,
    CourseQuizSubmission,
)
from .serializers import (
    BulkCourseQuizSubmissionSerializer,
    CandidateCourseQuizDetailSerializer,
    CourseEnrollmentSerializer,
    CourseEnrollmentWriteSerializer,
    CourseHomeCardSerializer,
    CourseHomeExamSerializer,
    CourseQuizAttemptResultSerializer,
    CourseQuizAttemptStartSerializer,
    CourseQuizDetailSerializer,
    CourseQuizImportSerializer,
    CourseQuizSummarySerializer,
    CourseQuizWriteSerializer,
    CourseWriteSerializer,
    LearnerCourseSerializer,
    StaffCourseSerializer,
)
from .services import (
    append_questions_to_quiz,
    attempt_is_expired,
    create_or_reactivate_enrollment,
    create_or_resume_attempt,
    create_quiz_from_import,
    ensure_course_access,
    ensure_quiz_access,
    finalize_attempt_submission,
    get_course_performance,
    parse_csv_questions,
    replace_quiz_questions,
    submissions_match_attempt,
    validate_submission_payload,
)


def get_course_list_queryset(user):
    enrollment_exists = CourseEnrollment.objects.filter(
        course=OuterRef('pk'),
        user=user,
        is_active=True,
    )
    queryset = Course.objects.select_related('created_by').annotate(
        quiz_count=Count('quizzes', distinct=True),
        enrollment_count=Count('enrollments', filter=models.Q(enrollments__is_active=True), distinct=True),
        is_enrolled=Exists(enrollment_exists),
    )
    if not user.is_staff:
        queryset = queryset.filter(is_published=True)
    return queryset.order_by('name', 'id')


def annotate_quiz_attempts(queryset, user):
    active_attempt = CourseQuizAttempt.objects.filter(
        quiz_id=OuterRef('pk'),
        user=user,
        is_completed=False,
    )
    return queryset.annotate(
        completed_attempt_count=Count(
            'attempts',
            filter=models.Q(attempts__user=user, attempts__is_completed=True),
            distinct=True,
        ),
        has_in_progress_attempt=Exists(active_attempt),
    )


def get_quiz_prefetch(user, include_questions=False):
    quiz_queryset = CourseQuiz.objects.annotate(
        question_count=Count('questions', distinct=True),
    )
    quiz_queryset = annotate_quiz_attempts(quiz_queryset, user).order_by('position', 'id')
    if include_questions:
        quiz_queryset = quiz_queryset.prefetch_related(
            Prefetch(
                'questions',
                queryset=CourseQuestion.objects.order_by('position', 'id').prefetch_related(
                    Prefetch('answers', queryset=CourseAnswer.objects.order_by('position', 'id')),
                ),
            ),
        )
    return Prefetch('quizzes', queryset=quiz_queryset, to_attr='prefetched_quizzes')


def get_course_detail_queryset(user):
    queryset = get_course_list_queryset(user).prefetch_related(
        get_quiz_prefetch(user, include_questions=user.is_staff),
    )
    return queryset


HOME_SECTION_LIMIT = 6
HOME_SECTION_MAX_LIMIT = 20


def get_home_course_queryset(user):
    """Courses annotated with the counters the home screen cards need."""
    enrollment_exists = CourseEnrollment.objects.filter(
        course=OuterRef('pk'),
        user=user,
        is_active=True,
    )
    queryset = Course.objects.annotate(
        quiz_count=Count(
            'quizzes',
            filter=models.Q(quizzes__is_active=True),
            distinct=True,
        ),
        enrollment_count=Count(
            'enrollments',
            filter=models.Q(enrollments__is_active=True),
            distinct=True,
        ),
        completed_quiz_count=Count(
            'quizzes',
            filter=models.Q(
                quizzes__is_active=True,
                quizzes__attempts__user=user,
                quizzes__attempts__is_completed=True,
            ),
            distinct=True,
        ),
        is_enrolled=Exists(enrollment_exists),
    )
    if not user.is_staff:
        queryset = queryset.filter(is_published=True)
    return queryset


def get_enrolled_home_courses(user, limit):
    """The user's active enrollments, most recently enrolled first."""
    queryset = get_home_course_queryset(user).annotate(
        latest_enrolled_at=models.Max(
            'enrollments__enrolled_at',
            filter=models.Q(enrollments__user=user, enrollments__is_active=True),
        ),
    ).filter(is_enrolled=True)
    return list(queryset.order_by('-latest_enrolled_at', 'name', 'id')[:limit])


def get_popular_home_courses(user, limit):
    """Most enrolled courses, preferring ones the user has not joined yet."""
    queryset = get_home_course_queryset(user).order_by(
        '-enrollment_count',
        '-created_at',
        'id',
    )
    popular = list(queryset.exclude(is_enrolled=True)[:limit])
    if not popular:
        # The user is already enrolled everywhere — still show the top courses.
        popular = list(queryset[:limit])
    return popular


def get_home_course_exams(user, now, limit):
    """Active exams across the user's enrollments, most actionable first."""
    queryset = CourseQuiz.objects.filter(
        is_active=True,
        course__enrollments__user=user,
        course__enrollments__is_active=True,
    ).select_related('course').annotate(
        question_count=Count('questions', distinct=True),
    )
    queryset = annotate_quiz_attempts(queryset, user).annotate(
        is_unlocked_now=models.Case(
            models.When(
                course__course_type=Course.CourseType.ARCHIVE,
                then=models.Value(True),
            ),
            models.When(
                unlock_at__isnull=False,
                unlock_at__lte=now,
                then=models.Value(True),
            ),
            default=models.Value(False),
            output_field=models.BooleanField(),
        ),
    )
    ordered = queryset.order_by(
        '-has_in_progress_attempt',
        '-is_unlocked_now',
        'completed_attempt_count',
        'position',
        'id',
    )
    return list(ordered[:limit])


def get_course_quiz_queryset(user, include_questions=False):
    queryset = CourseQuiz.objects.select_related('course').annotate(
        question_count=Count('questions', distinct=True),
    )
    queryset = annotate_quiz_attempts(queryset, user).order_by('position', 'id')
    if include_questions:
        queryset = queryset.prefetch_related(
            Prefetch(
                'questions',
                queryset=CourseQuestion.objects.order_by('position', 'id').prefetch_related(
                    Prefetch('answers', queryset=CourseAnswer.objects.order_by('position', 'id')),
                ),
            ),
        )
    return queryset


def get_attempt_result_queryset(user):
    return CourseQuizAttempt.objects.select_related(
        'quiz',
        'quiz__course',
        'user',
    ).annotate(
        quiz_completed_attempt_count=Count(
            'quiz__attempts',
            filter=models.Q(
                quiz__attempts__user=user,
                quiz__attempts__is_completed=True,
            ),
            distinct=True,
        ),
        quiz_has_in_progress_attempt=Exists(
            CourseQuizAttempt.objects.filter(
                quiz_id=OuterRef('quiz_id'),
                user=user,
                is_completed=False,
            )
        ),
    ).prefetch_related(
        Prefetch(
            'quiz__questions',
            queryset=CourseQuestion.objects.order_by('position', 'id').prefetch_related(
                Prefetch('answers', queryset=CourseAnswer.objects.order_by('position', 'id')),
            ),
        ),
        Prefetch(
            'submissions',
            queryset=CourseQuizSubmission.objects.select_related(
                'selected_answer',
                'question',
            ).prefetch_related(
                Prefetch('question__answers', queryset=CourseAnswer.objects.order_by('position', 'id')),
            ).order_by('question__position', 'id'),
        ),
    )


class StaffWriteMixin:
    def check_staff(self, request):
        if not request.user.is_staff:
            self.permission_denied(request, message='Staff access is required.')


class CourseListCreateView(StaffWriteMixin, generics.ListCreateAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return get_course_detail_queryset(self.request.user)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return CourseWriteSerializer
        if self.request.user.is_staff:
            return StaffCourseSerializer
        return LearnerCourseSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['now'] = timezone.now()
        return context

    def perform_create(self, serializer):
        self.check_staff(self.request)
        serializer.save(created_by=self.request.user)


class CourseHomeView(APIView):
    """Everything the home screen sliders need in one round trip.

    Returns ``enrolled_courses``, ``course_exams`` (active exams across every
    active enrollment) and ``popular_courses``. Each section is capped by the
    optional ``limit`` query parameter.
    """

    permission_classes = [IsAuthenticatedUserOnly]

    def get(self, request):
        limit = self._parse_limit(request.query_params.get('limit'))
        # This endpoint fans out into several annotated aggregate queries, so it
        # is cached per user for a short window. Enrolling or finishing an exam
        # invalidates that user's entry immediately (see courses/models.py), so
        # the TTL only ever delays `unlock_at` transitions and other users'
        # enrolment counts.
        return Response(
            cached_payload(
                NS_COURSE_HOME,
                ('home', limit),
                TTL_SHORT,
                lambda: self._build_payload(request.user, limit),
                scope=request.user.pk,
            )
        )

    def _build_payload(self, user, limit):
        now = timezone.now()

        enrolled_courses = get_enrolled_home_courses(user, limit)
        course_exams = (
            get_home_course_exams(user, now, limit) if enrolled_courses else []
        )
        popular_courses = get_popular_home_courses(user, limit)

        course_context = {'now': now}
        exam_context = {'now': now, 'is_staff_view': user.is_staff}

        return {
            'enrolled_courses': CourseHomeCardSerializer(
                enrolled_courses,
                many=True,
                context=course_context,
            ).data,
            'course_exams': CourseHomeExamSerializer(
                course_exams,
                many=True,
                context=exam_context,
            ).data,
            'popular_courses': CourseHomeCardSerializer(
                popular_courses,
                many=True,
                context=course_context,
            ).data,
        }

    @staticmethod
    def _parse_limit(raw_limit):
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return HOME_SECTION_LIMIT
        return max(1, min(limit, HOME_SECTION_MAX_LIMIT))


class CourseDetailView(StaffWriteMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    lookup_url_kwarg = 'course_id'

    def get_queryset(self):
        return get_course_detail_queryset(self.request.user)

    def get_serializer_class(self):
        if self.request.method in permissions.SAFE_METHODS:
            if self.request.user.is_staff:
                return StaffCourseSerializer
            return LearnerCourseSerializer
        return CourseWriteSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['now'] = timezone.now()
        return context

    def update(self, request, *args, **kwargs):
        self.check_staff(request)
        return super().update(request, *args, **kwargs)


class CourseEnrollView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, course_id):
        course = get_object_or_404(Course, pk=course_id, is_published=True)
        if not course.allow_self_enrollment and not request.user.is_staff:
            return Response(
                {'detail': 'Self enrollment is disabled for this course.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        enrollment, created = create_or_reactivate_enrollment(
            course=course,
            user=request.user,
            source=CourseEnrollment.EnrollmentSource.SELF,
            enrolled_by=request.user,
        )
        serializer = CourseEnrollmentSerializer(enrollment)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class CourseEnrollmentListCreateView(StaffWriteMixin, generics.ListCreateAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    serializer_class = CourseEnrollmentSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.check_staff(request)

    def get_course(self):
        if not hasattr(self, '_course'):
            self._course = get_object_or_404(Course, pk=self.kwargs['course_id'])
        return self._course

    def get_queryset(self):
        return CourseEnrollment.objects.filter(course=self.get_course()).select_related(
            'user',
            'enrolled_by',
        )

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return CourseEnrollmentWriteSerializer
        return CourseEnrollmentSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = self.get_course()
        enrollment, created = create_or_reactivate_enrollment(
            course=course,
            user=serializer.validated_data['user'],
            source=CourseEnrollment.EnrollmentSource.STAFF,
            enrolled_by=request.user,
        )
        enrollment.is_active = serializer.validated_data.get('is_active', True)
        enrollment.save(update_fields=['is_active', 'updated_at'])
        response_serializer = CourseEnrollmentSerializer(enrollment)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class CourseQuizListCreateView(StaffWriteMixin, generics.ListCreateAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    pagination_class = StandardResultsSetPagination

    def get_course(self):
        if not hasattr(self, '_course'):
            course_queryset = get_course_detail_queryset(self.request.user)
            self._course = get_object_or_404(course_queryset, pk=self.kwargs['course_id'])
        return self._course

    def get_queryset(self):
        course = self.get_course()
        if not self.request.user.is_staff:
            ensure_course_access(course, self.request.user)
        queryset = get_course_quiz_queryset(
            self.request.user,
            include_questions=self.request.user.is_staff,
        ).filter(course=course)
        if not self.request.user.is_staff:
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return CourseQuizWriteSerializer
        return CourseQuizSummarySerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['course'] = self.get_course()
        context['now'] = timezone.now()
        context['is_staff_view'] = self.request.user.is_staff
        return context

    def create(self, request, *args, **kwargs):
        self.check_staff(request)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = self.get_course()
        questions = serializer.validated_data.pop('questions', None)
        with transaction.atomic():
            quiz = CourseQuiz.objects.create(course=course, **serializer.validated_data)
            if questions is not None:
                replace_quiz_questions(quiz, questions)
        response_serializer = CourseQuizDetailSerializer(
            get_course_quiz_queryset(request.user, include_questions=True).get(pk=quiz.pk),
            context={'now': timezone.now(), 'is_staff_view': True},
        )
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class CourseQuizDetailView(StaffWriteMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    lookup_url_kwarg = 'quiz_id'

    def get_course(self):
        if not hasattr(self, '_course'):
            course_queryset = get_course_detail_queryset(self.request.user)
            self._course = get_object_or_404(course_queryset, pk=self.kwargs['course_id'])
        return self._course

    def get_queryset(self):
        return get_course_quiz_queryset(
            self.request.user,
            include_questions=True,
        ).filter(course=self.get_course())

    def get_serializer_class(self):
        if self.request.method in permissions.SAFE_METHODS:
            if self.request.user.is_staff:
                return CourseQuizDetailSerializer
            return CandidateCourseQuizDetailSerializer
        return CourseQuizWriteSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['course'] = self.get_course()
        context['now'] = timezone.now()
        context['is_staff_view'] = self.request.user.is_staff
        return context

    def retrieve(self, request, *args, **kwargs):
        quiz = self.get_object()
        if not request.user.is_staff:
            ensure_quiz_access(quiz, request.user, require_unlocked=True)
        serializer = self.get_serializer(quiz)
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        self.check_staff(request)
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        questions = serializer.validated_data.pop('questions', None)
        with transaction.atomic():
            for attr, value in serializer.validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            if questions is not None:
                replace_quiz_questions(instance, questions)
        response_serializer = CourseQuizDetailSerializer(
            self.get_queryset().get(pk=instance.pk),
            context={'now': timezone.now(), 'is_staff_view': True},
        )
        return Response(response_serializer.data)


class CourseQuizImportView(StaffWriteMixin, APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, course_id):
        self.check_staff(request)
        course = get_object_or_404(Course, pk=course_id)
        serializer = CourseQuizImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        question_payloads = []
        for question in serializer.validated_data['questions']:
            correct_option = question['correct_option']
            question_payloads.append({
                'source_question_id': question.get('source_question_id', ''),
                'source_question_url': question.get('source_question_url', ''),
                'question_text': question['question_text'],
                'explanation': question.get('explanation', ''),
                'answers': [
                    {'text': question['option_a'], 'is_correct': correct_option == 'A'},
                    {'text': question['option_b'], 'is_correct': correct_option == 'B'},
                    {'text': question['option_c'], 'is_correct': correct_option == 'C'},
                    {'text': question['option_d'], 'is_correct': correct_option == 'D'},
                ],
            })

        result = create_quiz_from_import(
            course,
            {
                'name': serializer.validated_data['quiz_name'],
                'topic': serializer.validated_data.get('topic', ''),
                'quiz_type': serializer.validated_data['quiz_type'],
                'duration_minutes': serializer.validated_data['duration_minutes'],
                'correct_mark': serializer.validated_data['correct_mark'],
                'wrong_mark': serializer.validated_data['wrong_mark'],
                'unanswered_mark': serializer.validated_data['unanswered_mark'],
                'unlock_at': serializer.validated_data.get('unlock_at'),
                'source_name': serializer.validated_data.get('source_name', ''),
                'external_quiz_id': serializer.validated_data.get('external_quiz_id', ''),
                'source_url': serializer.validated_data.get('source_url', ''),
                'is_active': serializer.validated_data.get('is_active', True),
            },
            question_payloads,
        )
        response_serializer = CourseQuizDetailSerializer(
            get_course_quiz_queryset(request.user, include_questions=True).get(pk=result['quiz'].pk),
            context={'now': timezone.now(), 'is_staff_view': True},
        )
        return Response({
            'quiz': response_serializer.data,
            'quiz_created': result['quiz_created'],
            'total': result['total'],
            'created': result['created'],
            'skipped': result['skipped'],
        })


class CourseQuizQuestionImportView(StaffWriteMixin, APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, course_id, quiz_id):
        self.check_staff(request)
        quiz = get_object_or_404(
            get_course_quiz_queryset(request.user, include_questions=True),
            course_id=course_id,
            pk=quiz_id,
        )
        uploaded_file = request.FILES.get('file')
        if uploaded_file is None:
            return Response({'file': ['This field is required.']}, status=status.HTTP_400_BAD_REQUEST)
        question_payloads = parse_csv_questions(uploaded_file)
        result = append_questions_to_quiz(quiz, question_payloads)
        response_serializer = CourseQuizDetailSerializer(
            get_course_quiz_queryset(request.user, include_questions=True).get(pk=quiz.pk),
            context={'now': timezone.now(), 'is_staff_view': True},
        )
        return Response({
            'quiz': response_serializer.data,
            **result,
        })


class CourseQuizAttemptStartView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, course_id, quiz_id):
        quiz = get_object_or_404(
            get_course_quiz_queryset(request.user, include_questions=True),
            course_id=course_id,
            pk=quiz_id,
        )
        ensure_quiz_access(quiz, request.user, require_unlocked=True)
        attempt, _created = create_or_resume_attempt(quiz, request.user)
        attempt.quiz = get_course_quiz_queryset(
            request.user,
            include_questions=True,
        ).get(pk=quiz.pk)
        serializer = CourseQuizAttemptStartSerializer(
            attempt,
            context={'now': timezone.now(), 'is_staff_view': False},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class CourseQuizAttemptSubmitView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def post(self, request, attempt_id):
        input_serializer = BulkCourseQuizSubmissionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            attempt = get_object_or_404(
                CourseQuizAttempt.objects.select_for_update().select_related('quiz', 'quiz__course'),
                pk=attempt_id,
                user=request.user,
            )
            ensure_quiz_access(attempt.quiz, request.user, require_unlocked=True)
            submission_state = validate_submission_payload(
                attempt,
                input_serializer.validated_data['submissions'],
            )

            if attempt.is_completed:
                if not submissions_match_attempt(
                    attempt,
                    submission_state['submitted_answers_by_question_id'],
                ):
                    return Response(
                        {'error': 'This quiz attempt has already been submitted with different answers.'},
                        status=status.HTTP_409_CONFLICT,
                    )
            else:
                if attempt_is_expired(attempt):
                    return Response(
                        {'error': 'This quiz attempt has expired.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                finalize_attempt_submission(attempt, submission_state)

        result_attempt = get_attempt_result_queryset(request.user).get(pk=attempt_id)
        serializer = CourseQuizAttemptResultSerializer(
            result_attempt,
            context={'now': timezone.now(), 'is_staff_view': False},
        )
        return Response(serializer.data)


class CourseQuizAttemptResultView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticatedUserOnly]
    serializer_class = CourseQuizAttemptResultSerializer
    lookup_url_kwarg = 'attempt_id'

    def get_queryset(self):
        return get_attempt_result_queryset(self.request.user).filter(
            user=self.request.user,
            is_completed=True,
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['now'] = timezone.now()
        context['is_staff_view'] = False
        return context


class CoursePerformanceView(APIView):
    permission_classes = [IsAuthenticatedUserOnly]

    def get(self, request, course_id):
        course = get_object_or_404(Course, pk=course_id)
        ensure_course_access(course, request.user)
        return Response(get_course_performance(course, request.user))
