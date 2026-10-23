"""Daily Exam API endpoints."""

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import FlexibleAuthentication
from .daily_exam_serializers import (
    build_attempt_payload,
    build_exam_payload,
    build_history_item,
    build_status_payload,
    build_unavailable_payload,
)
from .daily_exam_services import (
    attempt_deadline,
    attempt_is_expired,
    attempt_status,
    ensure_daily_exam,
    finalize_expired_attempt,
    leaderboard_payload,
    local_date,
    next_exam_opens_at,
    start_gets_full_duration,
)
from .models import DailyExam, DailyExamAttempt, QuizAttempt
from .pagination import StandardResultsSetPagination
from .permissions import IsAuthenticatedOrGuest
from .serializers import (
    BulkQuizSubmissionSerializer,
    CandidateQuizDetailSerializer,
    QuizAttemptResultSerializer,
    SummaryQuizAttemptResultSerializer,
)
from .views import (
    finalize_attempt_submission,
    get_model_test_serializer_context,
    get_prefetched_quiz_question_ids,
    get_quiz_detail_queryset,
    get_user_or_guest,
    get_user_queryset_filter,
    serialize_quiz_attempt_result,
    submissions_match_attempt,
    validate_bulk_submissions_for_attempt,
)


def _quiz_detail_payload(request, exam, include_questions=True):
    quiz = get_quiz_detail_queryset().get(pk=exam.quiz_id)
    question_ids = (
        get_prefetched_quiz_question_ids(quiz) if include_questions else []
    )
    serializer = CandidateQuizDetailSerializer(
        quiz,
        context=get_model_test_serializer_context(request, question_ids),
    )
    return serializer.data


def _get_owned_attempt(request, attempt_id):
    queryset = DailyExamAttempt.objects.select_related(
        'exam',
        'exam__category',
        'exam__quiz',
        'quiz_attempt',
        'quiz_attempt__quiz',
        'user',
    ).prefetch_related('quiz_attempt__submissions__question')
    return queryset.filter(
        Q(pk=attempt_id) & get_user_queryset_filter(request),
    ).first()


class DailyExamTodayView(APIView):
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def get(self, request):
        now = timezone.now()
        user, guest_user = get_user_or_guest(request)
        exam, _created = ensure_daily_exam(now=now)
        if exam is None:
            return Response(
                build_unavailable_payload(
                    now=now,
                    next_open_at=next_exam_opens_at(now=now),
                )
            )
        return Response(
            build_status_payload(
                exam,
                user=user,
                guest_user=guest_user,
                now=now,
            )
        )


class DailyExamDetailView(APIView):
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def get(self, request, exam_id):
        exam = (
            DailyExam.objects
            .select_related('category', 'quiz')
            .filter(pk=exam_id)
            .first()
        )
        if exam is None:
            return Response(
                {'error': 'Daily exam not found.', 'code': 'exam_not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        user, guest_user = get_user_or_guest(request)
        return Response(
            build_status_payload(
                exam,
                user=user,
                guest_user=guest_user,
                now=timezone.now(),
            )
        )


class DailyExamHistoryView(APIView):
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def get(self, request):
        now = timezone.now()
        user, guest_user = get_user_or_guest(request)
        owner_filter = (
            Q(user=user) if user is not None else Q(guest_user=guest_user)
        )
        attempts = (
            DailyExamAttempt.objects
            .filter(owner_filter)
            .select_related('quiz_attempt', 'user')
            .prefetch_related('quiz_attempt__submissions')
        )
        queryset = (
            DailyExam.objects
            .filter(
                Q(exam_date__lt=local_date(now))
                | Q(closes_at__lte=now)
            )
            .select_related('category', 'quiz')
            .annotate(
                ranked_participant_count=Count(
                    'attempts',
                    filter=Q(
                        attempts__user__isnull=False,
                        attempts__is_ranked=True,
                        attempts__submitted_at__isnull=False,
                        attempts__quiz_attempt__is_completed=True,
                    ),
                    distinct=True,
                ),
            )
            .order_by('-exam_date')
        )

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        exam_list = list(page if page is not None else queryset)
        exam_ids = [exam.pk for exam in exam_list]
        attempt_by_exam = {
            attempt.exam_id: attempt
            for attempt in attempts.filter(exam_id__in=exam_ids)
        }

        results = []
        for exam in exam_list:
            attempt = attempt_by_exam.get(exam.pk)
            results.append(
                build_history_item(
                    exam,
                    daily_attempt=attempt,
                    now=now,
                    participant_count=exam.ranked_participant_count,
                )
            )
        return paginator.get_paginated_response(results)


class DailyExamStartView(APIView):
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def post(self, request, exam_id):
        exam = (
            DailyExam.objects
            .select_related('category', 'quiz')
            .filter(pk=exam_id)
            .first()
        )
        if exam is None:
            return Response(
                {'error': 'Daily exam not found.', 'code': 'exam_not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if exam.quiz.questions.count() != settings.DAILY_EXAM_QUESTION_COUNT:
            return Response(
                {
                    'error': 'This daily exam is incomplete and cannot be started.',
                    'code': 'exam_unavailable',
                },
                status=status.HTTP_409_CONFLICT,
            )

        now = timezone.now()
        if now < exam.opens_at:
            return Response(
                {
                    'error': 'The daily exam has not started yet.',
                    'code': 'exam_not_open',
                    'opens_at': exam.opens_at,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if now >= exam.closes_at:
            return Response(
                {
                    'error': 'The daily exam has already closed.',
                    'code': 'exam_closed',
                    'closes_at': exam.closes_at,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, guest_user = get_user_or_guest(request)
        existing = DailyExamAttempt.objects.select_related(
            'quiz_attempt',
            'quiz_attempt__quiz',
        ).filter(
            Q(exam=exam)
            & (
                Q(user=user) if user is not None else Q(guest_user=guest_user)
            )
        ).first()

        resumed = False
        if existing is not None:
            existing_status = attempt_status(existing, now=now)
            if existing_status == 'SUBMITTED':
                return Response(
                    {
                        'error': 'You have already attempted this daily exam.',
                        'code': 'already_attempted',
                        'attempt': build_attempt_payload(existing, now=now),
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            if existing_status == 'EXPIRED':
                finalize_expired_attempt(existing, now=now)
                return Response(
                    {
                        'error': 'Your attempt time has expired.',
                        'code': 'attempt_expired',
                        'attempt': build_attempt_payload(existing, now=now),
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            daily_attempt = existing
            resumed = True
        else:
            is_ranked = bool(
                user is not None and start_gets_full_duration(exam, now)
            )
            with transaction.atomic():
                quiz_attempt = QuizAttempt.objects.create(
                    user=user,
                    guest_user=guest_user,
                    quiz=exam.quiz,
                )
                try:
                    daily_attempt = DailyExamAttempt.objects.create(
                        exam=exam,
                        user=user,
                        guest_user=guest_user,
                        quiz_attempt=quiz_attempt,
                        is_ranked=is_ranked,
                    )
                except IntegrityError:
                    # Another request raced us; reuse the winner.
                    quiz_attempt.delete()
                    daily_attempt = DailyExamAttempt.objects.select_related(
                        'quiz_attempt',
                        'quiz_attempt__quiz',
                    ).get(
                        Q(exam=exam)
                        & (
                            Q(user=user) if user is not None
                            else Q(guest_user=guest_user)
                        )
                    )
                    raced_status = attempt_status(daily_attempt, now=now)
                    if raced_status == 'SUBMITTED':
                        return Response(
                            {
                                'error': 'You have already attempted this daily exam.',
                                'code': 'already_attempted',
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    if raced_status == 'EXPIRED':
                        finalize_expired_attempt(daily_attempt, now=now)
                        return Response(
                            {
                                'error': 'Your attempt time has expired.',
                                'code': 'attempt_expired',
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    resumed = True

        quiz_attempt = daily_attempt.quiz_attempt
        deadline = attempt_deadline(daily_attempt)
        ranking_reason = None
        if user is None:
            ranking_reason = 'guest'
        elif not daily_attempt.is_ranked:
            ranking_reason = 'late_start'

        return Response(
            {
                'attempt_id': daily_attempt.pk,
                'quiz_attempt_id': quiz_attempt.pk,
                'started_at': quiz_attempt.start_time,
                'expires_at': deadline,
                'server_time': now,
                'is_ranked': bool(daily_attempt.is_ranked),
                'ranking_reason': ranking_reason,
                'resumed': resumed,
                'quiz_details': _quiz_detail_payload(request, exam),
            },
            status=status.HTTP_201_CREATED,
        )


class DailyExamSubmitView(APIView):
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def post(self, request, attempt_id):
        input_serializer = BulkQuizSubmissionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        submissions_data = input_serializer.validated_data['submissions']
        now = timezone.now()

        with transaction.atomic():
            daily_attempt = _get_owned_attempt(request, attempt_id)
            if daily_attempt is None:
                return Response(
                    {
                        'error': 'Daily exam attempt not found.',
                        'code': 'attempt_not_found',
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            quiz_attempt = QuizAttempt.objects.select_for_update().select_related(
                'quiz',
            ).get(pk=daily_attempt.quiz_attempt_id)
            submission_state, error_response = validate_bulk_submissions_for_attempt(
                quiz_attempt,
                submissions_data,
            )
            if error_response is not None:
                return error_response

            if quiz_attempt.is_completed:
                if not submissions_match_attempt(
                    quiz_attempt,
                    submission_state['submitted_answers_by_question_id'],
                ):
                    return Response(
                        {
                            'error': (
                                'This daily exam attempt has already been '
                                'submitted with different answers.'
                            ),
                            'code': 'already_submitted',
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
            else:
                if attempt_is_expired(daily_attempt, now=now):
                    finalize_expired_attempt(daily_attempt, now=now)
                    return Response(
                        {
                            'error': 'Your attempt time has expired.',
                            'code': 'attempt_expired',
                            'attempt': build_attempt_payload(
                                daily_attempt,
                                now=now,
                            ),
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                finalize_attempt_submission(
                    request,
                    quiz_attempt,
                    submission_state,
                )
                quiz_attempt.refresh_from_db()
                daily_attempt.submitted_at = now
                daily_attempt.time_taken_seconds = max(
                    int(
                        (
                            quiz_attempt.end_time
                            - quiz_attempt.start_time
                        ).total_seconds()
                    ),
                    0,
                )
                daily_attempt.save(
                    update_fields=['submitted_at', 'time_taken_seconds'],
                )

            # Keep the in-memory relation in sync with the rows we just wrote.
            daily_attempt.quiz_attempt = quiz_attempt

        data = serialize_quiz_attempt_result(
            request,
            quiz_attempt.pk,
            serializer_class=SummaryQuizAttemptResultSerializer,
            include_quiz_questions=False,
        )
        data = dict(data)
        data.update({
            'daily_exam_attempt_id': daily_attempt.pk,
            'is_ranked': bool(daily_attempt.is_ranked),
            'rank': build_attempt_payload(daily_attempt, now=now).get('rank'),
            'answer_key_available': now >= daily_attempt.exam.closes_at,
            'server_time': now,
        })
        return Response(data)


class DailyExamResultView(APIView):
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def get(self, request, attempt_id):
        daily_attempt = _get_owned_attempt(request, attempt_id)
        if daily_attempt is None:
            return Response(
                {
                    'error': 'Daily exam attempt not found.',
                    'code': 'attempt_not_found',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        now = timezone.now()
        answer_key_available = now >= daily_attempt.exam.closes_at
        serializer_class = (
            QuizAttemptResultSerializer
            if answer_key_available
            else SummaryQuizAttemptResultSerializer
        )
        data = serialize_quiz_attempt_result(
            request,
            daily_attempt.quiz_attempt_id,
            serializer_class=serializer_class,
            include_quiz_questions=answer_key_available,
        )
        data = dict(data)
        data.update({
            'daily_exam_attempt_id': daily_attempt.pk,
            'attempt': build_attempt_payload(daily_attempt, now=now),
            'exam': build_exam_payload(daily_attempt.exam, now=now),
            'is_ranked': bool(daily_attempt.is_ranked),
            'answer_key_available': answer_key_available,
            'server_time': now,
        })
        return Response(data)


class DailyExamLeaderboardView(APIView):
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def get(self, request, exam_id):
        exam = (
            DailyExam.objects
            .select_related('category', 'quiz')
            .filter(pk=exam_id)
            .first()
        )
        if exam is None:
            return Response(
                {'error': 'Daily exam not found.', 'code': 'exam_not_found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            limit = int(request.query_params.get('limit', 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 100))

        user, guest_user = get_user_or_guest(request)
        data = leaderboard_payload(
            exam,
            current_user_id=user.pk if user is not None else None,
            limit=limit,
        )
        data.update({
            'is_ranked': user is not None,
            'server_time': timezone.now(),
        })
        return Response(data)
