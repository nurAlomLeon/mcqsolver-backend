# views.py - Complete File with Guest User Support

from decimal import Decimal
from rest_framework import viewsets, generics, permissions, status, mixins
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import action
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.db import models
from .models import *
from .serializers import *
from .pagination import StandardResultsSetPagination
from rest_framework import filters
from django_filters.rest_framework import DjangoFilterBackend
from django_filters import rest_framework as django_filters
from .permissions import IsAuthenticatedOrGuest, IsAuthenticatedUserOnly, GuestLimitedPermission
from .authentication import FlexibleAuthentication
from .services import generate_quiz, start_template
from .cache_utils import (
    NS_APP_UPDATE,
    NS_CATEGORY,
    NS_MODEL_TEST,
    NS_QUESTION,
    NS_QUIZ,
    NS_QUIZ_CATEGORY,
    TTL_LONG,
    TTL_MEDIUM,
    cached_payload,
    request_signature,
)

# Additional imports for progress tracking
from datetime import date, timedelta, datetime
from django.db.models import Sum, Q, Avg
from collections import defaultdict
from django.contrib.auth.models import User

class CachedListMixin:
    """Cache a viewset's serialized ``list`` payload verbatim.

    The cached value is exactly what ``super().list()`` produced, so the
    response body stays byte-for-byte identical to the uncached code path and
    existing app builds are unaffected.

    Only mix this into viewsets whose list payload has no per-user fields.
    """

    cache_namespace = None
    cache_timeout = TTL_MEDIUM

    def get_cache_parts(self, request):
        return ('list', request_signature(request))

    def list(self, request, *args, **kwargs):
        if self.cache_namespace is None:
            return super().list(request, *args, **kwargs)

        def build():
            return super(CachedListMixin, self).list(request, *args, **kwargs).data

        return Response(
            cached_payload(
                self.cache_namespace,
                self.get_cache_parts(request),
                self.cache_timeout,
                build,
            )
        )


CATEGORY_TREE_CACHE_KEY = 'api:categories:tree:v3'
QUIZ_CATEGORY_TREE_CACHE_KEY = 'api:quiz-categories:tree:v2'
CATEGORY_SERIALIZER_CONTEXT_CACHE_KEY = 'api:categories:serializer-context:v1'
CATEGORY_TREE_CACHE_TIMEOUT = 60 * 60 * 12
QUIZ_RESULT_CACHE_TIMEOUT = 60 * 5


def _build_category_tree_response():
    categories = list(
        Category.objects.all()
        .only('id', 'name', 'level', 'order', 'parent_id')
        .order_by('level', 'order', 'name')
    )
    direct_counts = dict(
        Question.objects.filter(category_id__isnull=False)
        .values('category_id')
        .annotate(total=models.Count('id'))
        .values_list('category_id', 'total')
    )

    nodes = {}
    for category in categories:
        nodes[category.id] = {
            'id': category.id,
            'name': category.name,
            'level': category.level,
            'order': category.order,
            'parent': category.parent_id,
            'question_count': direct_counts.get(category.id, 0),
            'children': [],
        }

    roots = []
    for category in categories:
        node = nodes[category.id]
        if category.parent_id and category.parent_id in nodes:
            nodes[category.parent_id]['children'].append(node)
        else:
            roots.append(node)

    for category in reversed(categories):
        if category.parent_id and category.parent_id in nodes:
            nodes[category.parent_id]['question_count'] += nodes[category.id][
                'question_count'
            ]

    return roots


def _build_quiz_category_tree_response():
    categories = list(
        QuizCategory.objects.all()
        .only('id', 'name', 'level', 'order', 'parent_id')
        .order_by('level', 'order', 'name')
    )
    nodes = {
        category.id: {
            'id': category.id,
            'name': category.name,
            'level': category.level,
            'parent': category.parent_id,
            'children': [],
        }
        for category in categories
    }

    roots = []
    for category in categories:
        node = nodes[category.id]
        if category.parent_id and category.parent_id in nodes:
            nodes[category.parent_id]['children'].append(node)
        else:
            roots.append(node)

    return roots


def get_cached_category_tree():
    data = cache.get(CATEGORY_TREE_CACHE_KEY)
    if data is None:
        data = _build_category_tree_response()
        cache.set(CATEGORY_TREE_CACHE_KEY, data, CATEGORY_TREE_CACHE_TIMEOUT)
    return data


def get_cached_quiz_category_tree():
    data = cache.get(QUIZ_CATEGORY_TREE_CACHE_KEY)
    if data is None:
        data = _build_quiz_category_tree_response()
        cache.set(QUIZ_CATEGORY_TREE_CACHE_KEY, data, CATEGORY_TREE_CACHE_TIMEOUT)
    return data


def clear_category_tree_caches():
    cache.delete_many([
        CATEGORY_TREE_CACHE_KEY,
        QUIZ_CATEGORY_TREE_CACHE_KEY,
        CATEGORY_SERIALIZER_CONTEXT_CACHE_KEY,
    ])


def get_model_test_question_queryset():
    return Question.objects.select_related(
        'category',
        'category__parent',
        'category__parent__parent',
        'category__parent__parent__parent',
    ).prefetch_related(
        models.Prefetch('answers', queryset=Answer.objects.order_by('id')),
        models.Prefetch('labels', queryset=Label.objects.order_by('id')),
    ).order_by('id')


def get_model_test_submission_queryset():
    return UserSubmission.objects.select_related(
        'selected_answer',
        'question',
        'question__category',
        'question__category__parent',
        'question__category__parent__parent',
        'question__category__parent__parent__parent',
    ).prefetch_related(
        models.Prefetch('question__answers', queryset=Answer.objects.order_by('id')),
        models.Prefetch('question__labels', queryset=Label.objects.order_by('id')),
    ).order_by('question_id')


def get_quiz_detail_queryset(queryset=None):
    if queryset is None:
        queryset = Quiz.objects.all()
    return queryset.select_related(
        'category',
        'source_template',
    ).annotate(
        question_count=models.Count('questions', distinct=True),
    ).prefetch_related(
        models.Prefetch('questions', queryset=get_model_test_question_queryset()),
    )


def get_quiz_attempt_result_queryset(queryset=None, include_quiz_questions=True):
    if queryset is None:
        queryset = QuizAttempt.objects.all()
    prefetches = [
        models.Prefetch('submissions', queryset=get_model_test_submission_queryset()),
    ]
    if include_quiz_questions:
        prefetches.insert(
            0,
            models.Prefetch('quiz__questions', queryset=get_model_test_question_queryset()),
        )

    return queryset.select_related(
        'user',
        'guest_user',
        'quiz',
        'quiz__category',
        'quiz__source_template',
    ).annotate(
        question_count=models.Count('quiz__questions', distinct=True),
    ).prefetch_related(*prefetches)


def get_prefetched_quiz_question_ids(quiz):
    prefetched_questions = getattr(
        quiz,
        '_prefetched_objects_cache',
        {},
    ).get('questions')
    if prefetched_questions is not None:
        return [question.id for question in prefetched_questions]
    return list(quiz.questions.values_list('id', flat=True))


def get_saved_question_ids_for_request(request, question_ids):
    question_ids = set(question_ids or [])
    if not question_ids:
        return set()

    user, guest_user = get_user_or_guest(request)
    if user:
        queryset = SavedQuestion.objects.filter(
            user=user,
            question_id__in=question_ids,
        )
    elif guest_user:
        queryset = SavedQuestion.objects.filter(
            guest_user=guest_user,
            question_id__in=question_ids,
        )
    else:
        return set()

    return set(queryset.values_list('question_id', flat=True))


def get_category_serializer_context():
    cached_context = cache.get(CATEGORY_SERIALIZER_CONTEXT_CACHE_KEY)
    if cached_context is not None:
        return cached_context

    categories = list(
        Category.objects.all()
        .select_related('parent')
        .order_by('level', 'order', 'name')
    )
    category_by_id = {category.id: category for category in categories}
    children_by_parent = defaultdict(list)
    for category in categories:
        children_by_parent[category.parent_id].append(category)

    question_counts = defaultdict(int)
    direct_counts = dict(
        Question.objects.filter(category_id__isnull=False)
        .values('category_id')
        .annotate(total=models.Count('id'))
        .values_list('category_id', 'total')
    )
    question_counts.update(direct_counts)
    for category in sorted(categories, key=lambda item: item.level, reverse=True):
        if category.parent_id:
            question_counts[category.parent_id] += question_counts[category.id]

    full_paths = {}
    for category in categories:
        path = []
        current = category
        while current is not None:
            path.insert(0, str(current.name))
            current = category_by_id.get(current.parent_id)
        full_paths[category.id] = ' → '.join(path)

    context = {
        'category_children_by_parent': dict(children_by_parent),
        'category_full_paths': full_paths,
        'category_question_counts': dict(question_counts),
    }
    cache.set(
        CATEGORY_SERIALIZER_CONTEXT_CACHE_KEY,
        context,
        CATEGORY_TREE_CACHE_TIMEOUT,
    )
    return context


def get_model_test_serializer_context(request, question_ids=None):
    context = {'request': request}
    context.update(get_category_serializer_context())
    if question_ids is not None:
        context['saved_question_ids'] = get_saved_question_ids_for_request(
            request,
            question_ids,
        )
    return context


def get_quiz_result_cache_key(request, attempt_id):
    user, guest_user = get_user_or_guest(request)
    if user:
        owner_key = f'user:{user.id}'
    elif guest_user:
        owner_key = f'guest:{guest_user.id}'
    else:
        owner_key = 'anonymous'
    return f'api:quiz-result:{owner_key}:{attempt_id}:v2'


def cache_quiz_result_response(request, attempt_id, data):
    cache.set(
        get_quiz_result_cache_key(request, attempt_id),
        data,
        QUIZ_RESULT_CACHE_TIMEOUT,
    )


def validate_bulk_submissions_for_attempt(attempt, submissions_data):
    quiz_question_ids = set(
        attempt.quiz.questions.values_list('id', flat=True)
    )
    submitted_question_ids = [
        submission['question_id'] for submission in submissions_data
    ]
    seen_question_ids = set()
    duplicate_question_ids = set()
    for question_id in submitted_question_ids:
        if question_id in seen_question_ids:
            duplicate_question_ids.add(question_id)
        seen_question_ids.add(question_id)

    submitted_question_id_set = set(submitted_question_ids)
    if (
        duplicate_question_ids
        or submitted_question_id_set != quiz_question_ids
        or len(submitted_question_ids) != len(quiz_question_ids)
    ):
        return None, Response(
            {
                'error': 'Submit exactly one row for every quiz question.',
                'missing_question_ids': sorted(quiz_question_ids - submitted_question_id_set),
                'unexpected_question_ids': sorted(submitted_question_id_set - quiz_question_ids),
                'duplicate_question_ids': sorted(duplicate_question_ids),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    selected_answer_ids = {
        submission['selected_answer_id']
        for submission in submissions_data
        if submission['selected_answer_id'] is not None
    }
    answers_by_id = {
        answer.id: answer
        for answer in Answer.objects.filter(pk__in=selected_answer_ids)
    }
    answer_errors = {}
    submissions_to_create = []
    submitted_answers_by_question_id = {}
    for index, submission in enumerate(submissions_data):
        question_id = submission['question_id']
        selected_answer_id = submission['selected_answer_id']
        answer = answers_by_id.get(selected_answer_id)
        if selected_answer_id is not None and (
            answer is None or answer.question_id != question_id
        ):
            answer_errors[str(index)] = [
                'Selected answer does not belong to the submitted question.'
            ]
            continue
        submissions_to_create.append(UserSubmission(
            attempt=attempt,
            question_id=question_id,
            selected_answer_id=selected_answer_id,
            is_correct=bool(answer and answer.is_correct),
        ))
        submitted_answers_by_question_id[question_id] = selected_answer_id

    if answer_errors:
        return None, Response(
            {'submissions': answer_errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    correct_count = sum(1 for submission in submissions_to_create if submission.is_correct)
    wrong_count = sum(
        1 for submission in submissions_to_create
        if submission.selected_answer_id is not None and not submission.is_correct
    )
    unanswered_count = sum(
        1 for submission in submissions_to_create
        if submission.selected_answer_id is None
    )

    return {
        'submissions_to_create': submissions_to_create,
        'submitted_answers_by_question_id': submitted_answers_by_question_id,
        'correct_count': correct_count,
        'wrong_count': wrong_count,
        'unanswered_count': unanswered_count,
    }, None


def submissions_match_attempt(attempt, submitted_answers_by_question_id):
    existing_answers_by_question_id = dict(
        UserSubmission.objects.filter(attempt=attempt)
        .values_list('question_id', 'selected_answer_id')
    )
    return existing_answers_by_question_id == submitted_answers_by_question_id


def finalize_attempt_submission(request, attempt, submission_state):
    submissions_to_create = submission_state['submissions_to_create']
    correct_count = submission_state['correct_count']
    wrong_count = submission_state['wrong_count']
    unanswered_count = submission_state['unanswered_count']

    UserSubmission.objects.filter(attempt=attempt).delete()
    UserSubmission.objects.bulk_create(submissions_to_create)

    attempt.score = (
        correct_count * attempt.quiz.correct_mark
        + wrong_count * attempt.quiz.wrong_mark
        + unanswered_count * attempt.quiz.unanswered_mark
    )
    attempt.is_completed = True
    attempt.end_time = timezone.now()
    attempt.save(update_fields=['score', 'is_completed', 'end_time'])

    user, guest_user = get_user_or_guest(request)
    duration_minutes = None
    if attempt.start_time and attempt.end_time:
        duration = attempt.end_time - attempt.start_time
        duration_minutes = int(duration.total_seconds() / 60)

    track_quiz_completed(
        user,
        guest_user,
        attempt,
        duration_minutes,
        correct_count=correct_count,
    )
    track_question_answers_bulk(user, guest_user, submissions_to_create)

    if guest_user:
        GuestUser.objects.filter(pk=guest_user.pk).update(
            total_quizzes_completed=models.F('total_quizzes_completed') + 1,
            total_questions_answered=(
                models.F('total_questions_answered') + correct_count + wrong_count
            ),
        )


def serialize_quiz_attempt_result(
    request,
    attempt_id,
    serializer_class=QuizAttemptResultSerializer,
    include_quiz_questions=True,
):
    attempt = get_quiz_attempt_result_queryset(
        QuizAttempt.objects.filter(pk=attempt_id),
        include_quiz_questions=include_quiz_questions,
    ).get()
    if include_quiz_questions:
        question_ids = get_prefetched_quiz_question_ids(attempt.quiz)
    else:
        question_ids = [submission.question_id for submission in attempt.submissions.all()]

    serializer = serializer_class(
        attempt,
        context=get_model_test_serializer_context(request, question_ids),
    )
    return serializer.data


def process_model_test_submission(
    request,
    attempt_id,
    serializer_class,
    include_quiz_questions,
):
    input_serializer = BulkQuizSubmissionSerializer(data=request.data)
    input_serializer.is_valid(raise_exception=True)
    submissions_data = input_serializer.validated_data['submissions']
    user_filter = get_user_queryset_filter(request)
    serialized_attempt_id = None

    with transaction.atomic():
        try:
            attempt = QuizAttempt.objects.select_for_update().select_related('quiz').get(
                Q(pk=attempt_id) & user_filter
            )
        except QuizAttempt.DoesNotExist:
            return Response(
                {'error': 'Quiz attempt not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        submission_state, error_response = validate_bulk_submissions_for_attempt(
            attempt,
            submissions_data,
        )
        if error_response is not None:
            return error_response

        if attempt.is_completed:
            if not submissions_match_attempt(
                attempt,
                submission_state['submitted_answers_by_question_id'],
            ):
                return Response(
                    {
                        'error': 'This quiz attempt has already been submitted with different answers.',
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            serialized_attempt_id = attempt.id
        else:
            if attempt_is_expired(attempt):
                return expired_attempt_response(attempt)

            finalize_attempt_submission(request, attempt, submission_state)
            serialized_attempt_id = attempt.id

    data = serialize_quiz_attempt_result(
        request,
        serialized_attempt_id,
        serializer_class=serializer_class,
        include_quiz_questions=include_quiz_questions,
    )
    return Response(data, status=status.HTTP_200_OK)

# For Google Login
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView

class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    callback_url = "https://qb.mcqsolver.com"
    client_class = OAuth2Client


class AppUpdateConfigView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, platform):
        # Every app launch hits this endpoint and the answer is the same for
        # all of them, so it is the cheapest possible thing to cache.
        return Response(
            cached_payload(
                NS_APP_UPDATE,
                ('config', platform),
                TTL_MEDIUM,
                lambda: self._build_config_payload(platform),
            )
        )

    @staticmethod
    def _build_config_payload(platform):
        config = (
            AppUpdateConfig.objects.filter(
                platform=platform,
                is_active=True,
            )
            .order_by('-latest_build_number')
            .first()
        )
        if config is None:
            return {'update_available': False}

        return AppUpdateConfigSerializer(config).data


def _generate_unique_username(email):
    base_username = email.split('@')[0]
    username = base_username
    counter = 1

    while User.objects.filter(username=username).exists():
        username = f"{base_username}_{counter}"
        counter += 1

    return username


def _transfer_guest_data_to_user(guest_user, user):
    with transaction.atomic():
        Quiz.objects.filter(generated_for_guest=guest_user).update(
            generated_for_user=user,
            generated_for_guest=None,
        )
        QuizAttempt.objects.filter(guest_user=guest_user).update(
            user=user,
            guest_user=None,
        )

        for saved_question in SavedQuestion.objects.filter(guest_user=guest_user).select_related('question'):
            SavedQuestion.objects.get_or_create(
                user=user,
                question=saved_question.question,
            )
        SavedQuestion.objects.filter(guest_user=guest_user).delete()

        for daily_target in DailyTarget.objects.filter(guest_user=guest_user):
            existing_target = DailyTarget.objects.filter(
                user=user,
                target_type=daily_target.target_type,
            ).first()
            if existing_target:
                existing_target.target_value = max(existing_target.target_value, daily_target.target_value)
                existing_target.is_active = existing_target.is_active or daily_target.is_active
                existing_target.save()
                daily_target.delete()
            else:
                daily_target.user = user
                daily_target.guest_user = None
                daily_target.save()

        for daily_progress in DailyProgress.objects.filter(guest_user=guest_user):
            existing_progress = DailyProgress.objects.filter(
                user=user,
                target_type=daily_progress.target_type,
                date=daily_progress.date,
            ).first()
            if existing_progress:
                existing_progress.current_value = max(existing_progress.current_value, daily_progress.current_value)
                existing_progress.target_value = max(existing_progress.target_value, daily_progress.target_value)
                existing_progress.save()
                daily_progress.delete()
            else:
                daily_progress.user = user
                daily_progress.guest_user = None
                daily_progress.save()

        for weekly_progress in WeeklyProgress.objects.filter(guest_user=guest_user):
            existing_weekly_progress = WeeklyProgress.objects.filter(
                user=user,
                week_start_date=weekly_progress.week_start_date,
                target_type=weekly_progress.target_type,
            ).first()
            if existing_weekly_progress:
                existing_weekly_progress.total_target = max(
                    existing_weekly_progress.total_target,
                    weekly_progress.total_target,
                )
                existing_weekly_progress.total_achieved = max(
                    existing_weekly_progress.total_achieved,
                    weekly_progress.total_achieved,
                )
                existing_weekly_progress.days_completed = max(
                    existing_weekly_progress.days_completed,
                    weekly_progress.days_completed,
                )
                existing_weekly_progress.completion_percentage = max(
                    existing_weekly_progress.completion_percentage,
                    weekly_progress.completion_percentage,
                )
                existing_weekly_progress.save()
                weekly_progress.delete()
            else:
                weekly_progress.user = user
                weekly_progress.guest_user = None
                weekly_progress.save()

        UserActivity.objects.filter(guest_user=guest_user).update(
            user=user,
            guest_user=None,
        )

        # DeviceInstallation.token is globally unique, so each installation
        # can simply be reassigned to the new owner (no merge needed).
        from notifications.models import DeviceInstallation
        DeviceInstallation.objects.filter(guest_user=guest_user).update(
            user=user,
            guest_user=None,
        )

        for streak in Streak.objects.filter(guest_user=guest_user):
            existing_streak = Streak.objects.filter(
                user=user,
                target_type=streak.target_type,
            ).first()
            if existing_streak:
                existing_streak.current_streak = max(existing_streak.current_streak, streak.current_streak)
                existing_streak.longest_streak = max(existing_streak.longest_streak, streak.longest_streak)
                existing_streak.last_activity_date = max(
                    filter(None, [existing_streak.last_activity_date, streak.last_activity_date]),
                    default=None,
                )
                existing_streak.save()
                streak.delete()
            else:
                streak.user = user
                streak.guest_user = None
                streak.save()

        guest_user.delete()

# ===================================================================
# GUEST USER AUTHENTICATION VIEWS
# ===================================================================

class GuestLoginView(APIView):
    """
    Create or retrieve a guest user session
    """
    authentication_classes = []
    permission_classes = []
    
    def post(self, request):
        serializer = GuestLoginSerializer(data=request.data)
        if serializer.is_valid():
            device_id = serializer.validated_data['device_id']
            
            # Get or create guest user
            guest_user, created = GuestUser.objects.get_or_create(
                device_id=device_id
            )
            
            response_data = {
                'guest_token': str(guest_user.guest_id),
                'device_id': device_id,
                'is_new_guest': created,
                'guest_data': GuestUserSerializer(guest_user).data
            }
            
            return Response(response_data, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ConvertGuestToUserView(APIView):
    """
    Convert a guest user to a registered user
    """
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    
    def post(self, request):
        serializer = ConvertGuestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        guest_id = serializer.validated_data['guest_id']
        device_id = serializer.validated_data.get('device_id')

        try:
            guest_user = GuestUser.objects.get(guest_id=guest_id)
        except GuestUser.DoesNotExist:
            return Response(
                {"error": "Guest user not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if device_id and guest_user.device_id != device_id:
            return Response(
                {"error": "Guest session validation failed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if getattr(request.user, 'is_guest', False):
                email = serializer.validated_data.get('email')
                name = serializer.validated_data.get('name', '').strip()

                if not email or not name:
                    return Response(
                        {"error": "Email and name are required for guest conversion"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                user = User.objects.filter(email__iexact=email).first()
                if user is None:
                    name_parts = name.split()
                    user = User.objects.create_user(
                        username=_generate_unique_username(email),
                        email=email,
                        first_name=name_parts[0] if name_parts else name,
                        last_name=' '.join(name_parts[1:]) if len(name_parts) > 1 else '',
                    )

                _transfer_guest_data_to_user(guest_user, user)
                return Response(
                    {
                        "message": "Guest account successfully converted to registered user",
                        "user_id": user.id,
                        "email": user.email,
                    },
                    status=status.HTTP_200_OK,
                )

            if request.user.is_authenticated:
                _transfer_guest_data_to_user(guest_user, request.user)
                return Response(
                    {
                        "message": "Guest data transferred successfully",
                        "user_id": request.user.id,
                        "email": request.user.email,
                    },
                    status=status.HTTP_200_OK,
                )

            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except Exception as e:
            return Response(
                {"error": f"Conversion failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

# ===================================================================
# UTILITY FUNCTIONS FOR GUEST/USER COMPATIBILITY
# ===================================================================

def get_user_or_guest(request):
    """Helper function to get either regular user or guest user"""
    if hasattr(request.user, 'is_guest') and request.user.is_guest:
        return None, request.user.guest_user
    elif request.user.is_authenticated and not getattr(request.user, 'is_guest', False):
        return request.user, None
    return None, None

def get_user_queryset_filter(request):
    """Helper function to get appropriate filter for user or guest"""
    user, guest_user = get_user_or_guest(request)
    if user:
        return Q(user=user)
    elif guest_user:
        return Q(guest_user=guest_user)
    return Q(pk=None)  # Return empty queryset


EXAM_DEADLINE_GRACE = timedelta(seconds=5)


def get_attempt_expiration(attempt):
    return attempt.start_time + timedelta(minutes=attempt.quiz.duration_minutes)


def attempt_is_expired(attempt):
    return timezone.now() > get_attempt_expiration(attempt) + EXAM_DEADLINE_GRACE


def expired_attempt_response(attempt):
    return Response(
        {
            'error': 'The quiz duration has expired; answers can no longer be changed or submitted.',
            'expires_at': get_attempt_expiration(attempt),
        },
        status=status.HTTP_409_CONFLICT,
    )

# ===================================================================
# MAIN VIEWSETS WITH GUEST SUPPORT
# ===================================================================

class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """ Provides hierarchical category structure """
    queryset = Category.objects.all().select_related('parent').prefetch_related('children')
    serializer_class = CategorySerializer
    authentication_classes = []
    permission_classes = []

    def list(self, request, *args, **kwargs):
        if not request.query_params:
            return Response(get_cached_category_tree())

        def build():
            return super(CategoryViewSet, self).list(request, *args, **kwargs).data

        return Response(
            cached_payload(
                NS_CATEGORY,
                ('list', request_signature(request)),
                TTL_LONG,
                build,
            )
        )
    
    def get_queryset(self):
        queryset = super().get_queryset()
        level = self.request.query_params.get('level')
        parent = self.request.query_params.get('parent')
        
        if level is not None:
            queryset = queryset.filter(level=level)
        if parent is not None:
            if parent == 'null' or parent == '':
                queryset = queryset.filter(parent__isnull=True)
            else:
                queryset = queryset.filter(parent_id=parent)
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def tree(self, request):
        """Get the complete category tree structure"""
        return Response(get_cached_category_tree())
    
    @action(detail=False, methods=['get'])
    def subjects(self, request):
        """Get only root level categories (subjects)"""
        def build():
            subjects = Category.objects.filter(level=0, parent__isnull=True)
            return CategorySerializer(
                subjects, many=True, context={'request': request}
            ).data

        return Response(cached_payload(NS_CATEGORY, ('subjects',), TTL_LONG, build))
    
    @action(detail=True, methods=['get'])
    def parts(self, request, pk=None):
        """Get parts (level 1) for a specific subject"""
        # The existence check stays uncached so a missing subject still 404s
        # instead of the 404 body being cached as a success payload. It is a
        # single primary-key lookup.
        try:
            subject = Category.objects.get(pk=pk, level=0)
        except Category.DoesNotExist:
            return Response({"error": "Subject not found"}, status=status.HTTP_404_NOT_FOUND)

        def build():
            parts = subject.children.filter(level=1)
            return CategorySerializer(parts, many=True, context={'request': request}).data

        return Response(cached_payload(NS_CATEGORY, ('parts', pk), TTL_LONG, build))
    
    @action(detail=True, methods=['get'])
    def topics(self, request, pk=None):
        """Get topics (level 2) for a specific part"""
        try:
            part = Category.objects.get(pk=pk, level=1)
        except Category.DoesNotExist:
            return Response({"error": "Part not found"}, status=status.HTTP_404_NOT_FOUND)

        def build():
            topics = part.children.filter(level=2)
            return CategorySerializer(topics, many=True, context={'request': request}).data

        return Response(cached_payload(NS_CATEGORY, ('topics', pk), TTL_LONG, build))

class QuizCategoryViewSet(CachedListMixin, viewsets.ReadOnlyModelViewSet):
    """
    Provides a hierarchical structure for Quiz Categories.
    Use ?parent=null to get root categories.
    """
    queryset = QuizCategory.objects.filter(parent__isnull=True).prefetch_related('children')
    serializer_class = QuizCategorySerializer
    authentication_classes = []
    permission_classes = []
    cache_namespace = NS_QUIZ_CATEGORY
    cache_timeout = TTL_LONG

    @action(detail=False, methods=['get'])
    def tree(self, request):
        """Get the complete quiz category tree structure."""
        return Response(get_cached_quiz_category_tree())


class ModelTestTemplateViewSet(CachedListMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = ModelTestTemplateSerializer
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    # Same catalogue for every user; only staff edits change it.
    cache_namespace = NS_MODEL_TEST
    cache_timeout = TTL_MEDIUM

    def get_queryset(self):
        return ModelTestTemplate.objects.filter(is_active=True).prefetch_related(
            'category_configurations__category',
        ).order_by('display_order', 'name')

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        template = self.get_object()
        user, guest_user = get_user_or_guest(request)
        quiz, attempt = start_template(
            template,
            user=user,
            guest_user=guest_user,
        )
        quiz = get_quiz_detail_queryset().get(pk=quiz.pk)
        question_ids = get_prefetched_quiz_question_ids(quiz)
        serializer = CandidateQuizDetailSerializer(
            quiz,
            context=get_model_test_serializer_context(request, question_ids),
        )
        return Response(
            {
                'attempt_id': attempt.id,
                'started_at': attempt.start_time,
                'expires_at': get_attempt_expiration(attempt),
                'quiz_details': serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

class QuizViewSet(CachedListMixin, viewsets.ReadOnlyModelViewSet):
    """ Provides read-only access to pre-defined quizzes with category and quiz type filtering """
    queryset = Quiz.objects.all()
    serializer_class = QuizListSerializer
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    # The list excludes per-user generated quizzes, so one cached copy serves
    # everybody. See invalidate_quiz_caches() in models.py.
    cache_namespace = NS_QUIZ
    cache_timeout = TTL_MEDIUM

    def get_serializer_class(self):
        if getattr(self, 'action', None) == 'retrieve':
            return CandidateQuizDetailSerializer
        return QuizListSerializer

    def get_queryset(self):
        queryset = super().get_queryset().filter(
            generated_for_user__isnull=True,
            generated_for_guest__isnull=True,
        ).select_related('category').annotate(
            question_count=models.Count('questions', distinct=True),
        )

        if getattr(self, 'action', None) == 'retrieve':
            queryset = queryset.prefetch_related('questions__answers')

        # Category filtering - support both 'category' and 'category_id' parameters
        category_id = self.request.query_params.get('category') or self.request.query_params.get('category_id')
        if category_id:
            try:
                queryset = queryset.filter(category__id=int(category_id))
            except (ValueError, TypeError):
                pass  # Invalid category ID, ignore filter
        
        # Quiz type filtering - for question banks specifically
        quiz_type = self.request.query_params.get('quiz_type')
        if quiz_type:
            queryset = queryset.filter(quiz_type=quiz_type)

        return queryset

    @action(detail=True, methods=['get'])
    def questions(self, request, pk=None):
        """Return quiz questions one page at a time for fast question bank viewing."""
        quiz = self.get_object()

        def build():
            queryset = quiz.questions.all().select_related('category').prefetch_related(
                'answers',
                'labels',
            ).order_by('id')

            paginator = StandardResultsSetPagination()
            page = paginator.paginate_queryset(queryset, request, view=self)
            if page is not None:
                serializer = QuestionBankQuestionSerializer(
                    page, many=True, context={'request': request}
                )
                return paginator.get_paginated_response(serializer.data).data

            serializer = QuestionBankQuestionSerializer(
                queryset, many=True, context={'request': request}
            )
            return serializer.data

        # QuestionBankQuestionSerializer has no per-user fields, so each page of
        # a question bank is cacheable as-is.
        return Response(
            cached_payload(
                NS_QUESTION,
                ('quiz-questions', quiz.pk, request_signature(request)),
                TTL_MEDIUM,
                build,
            )
        )

class QuestionViewSet(viewsets.ReadOnlyModelViewSet):
    """ Provides read-only access to questions with hierarchical filtering """
    # order_by('id') makes pagination deterministic. Without it the queryset had
    # no ORDER BY, so page 2 could repeat or skip rows the database happened to
    # return in a different order - and caching those pages would freeze the
    # inconsistency in place. 'id' matches the order the database was already
    # returning in practice, and the question_cat_id_idx index serves it.
    queryset = Question.objects.all().select_related('category').prefetch_related(
        'answers', 'labels',
    ).order_by('id')
    serializer_class = QuestionSerializer
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    pagination_class = StandardResultsSetPagination

    def list(self, request, *args, **kwargs):
        """Cached question list with the per-user ``is_saved`` flag layered on.

        Everything in this payload except ``is_saved`` is identical for every
        caller, so the page is cached once with the flag cleared and then
        re-stamped from a single bulk lookup. That also replaces the
        one-query-per-question check the serializer used to run.
        """
        payload = cached_payload(
            NS_QUESTION,
            ('list', request_signature(request)),
            TTL_MEDIUM,
            lambda: self._build_list_payload(request),
        )
        return Response(self._stamp_saved_flags(request, payload))

    def _build_list_payload(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        # An empty `saved_question_ids` makes get_is_saved() return False
        # without touching the database; real values are stamped in per request.
        context = {'request': request, 'saved_question_ids': set()}
        serializer_class = self.get_serializer_class()

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = serializer_class(page, many=True, context=context)
            return self.paginator.get_paginated_response(serializer.data).data

        return serializer_class(queryset, many=True, context=context).data

    @staticmethod
    def _payload_rows(payload):
        if isinstance(payload, dict):
            return payload.get('results') or []
        return payload or []

    def _stamp_saved_flags(self, request, payload):
        rows = self._payload_rows(payload)
        if not rows:
            return payload

        saved_ids = get_saved_question_ids_for_request(
            request,
            [row['id'] for row in rows],
        )
        for row in rows:
            # Assigning an existing key keeps the JSON field order unchanged.
            row['is_saved'] = row['id'] in saved_ids
        return payload

    def get_queryset(self):
        queryset = super().get_queryset()
        
        category = self.request.query_params.get('category')
        category_level = self.request.query_params.get('category_level')
        
        if category:
            if category_level == 'all':
                try:
                    cat = Category.objects.get(id=category)
                    if cat.is_leaf():
                        queryset = queryset.filter(category_id=category)
                    else:
                        descendant_ids = [desc.id for desc in cat.get_descendants() if desc.is_leaf()]
                        if cat.is_leaf():
                            descendant_ids.append(cat.id)
                        queryset = queryset.filter(category_id__in=descendant_ids)
                except Category.DoesNotExist:
                    queryset = queryset.none()
            else:
                queryset = queryset.filter(category__id=category)
        
        return queryset


class BcsTargetTopicImportView(APIView):
    """Import one completed BCS Target topic into the main question bank."""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        serializer = BcsTargetTopicImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            parent_category = Category.objects.get(pk=data['parent_category_id'])
        except Category.DoesNotExist:
            return Response(
                {'parent_category_id': 'Parent category not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        topic_category, topic_created = self._get_or_create_topic_category(
            parent_category=parent_category,
            topic_name=data['topic_name'],
        )

        created = 0
        skipped = 0
        errors = []

        for index, raw_question in enumerate(data['questions'], start=1):
            question_serializer = BcsTargetImportQuestionSerializer(data=raw_question)
            if not question_serializer.is_valid():
                skipped += 1
                errors.append({
                    'index': index,
                    'source_question_id': raw_question.get('source_question_id', ''),
                    'errors': question_serializer.errors,
                })
                continue

            question_data = question_serializer.validated_data
            question_text = question_data['question_text']

            if Question.objects.filter(
                category=topic_category,
                question_text=question_text,
            ).exists():
                skipped += 1
                continue

            with transaction.atomic():
                question = Question.objects.create(
                    category=topic_category,
                    question_text=question_text,
                    explanation=question_data.get('explanation') or None,
                )

                correct_option = question_data['correct_option'].upper()
                for letter, field_name in (
                    ('A', 'option_a'),
                    ('B', 'option_b'),
                    ('C', 'option_c'),
                    ('D', 'option_d'),
                ):
                    Answer.objects.create(
                        question=question,
                        text=question_data[field_name],
                        is_correct=(letter == correct_option),
                    )
                created += 1

        return Response(
            {
                'topic_category': CategorySerializer(topic_category, context={'request': request}).data,
                'topic_created': topic_created,
                'total': len(data['questions']),
                'created': created,
                'skipped': skipped,
                'errors': errors,
            },
            status=status.HTTP_200_OK,
        )

    def _get_or_create_topic_category(self, parent_category, topic_name):
        topic_category = Category.objects.filter(
            parent=parent_category,
            name=topic_name,
        ).first()
        if topic_category:
            return topic_category, False

        next_order = (
            Category.objects.filter(parent=parent_category)
            .aggregate(max_order=models.Max('order'))['max_order']
            or 0
        ) + 1
        return Category.objects.create(
            parent=parent_category,
            name=topic_name,
            order=next_order,
        ), True


class BcsTargetAnswersheetImportView(APIView):
    """Import one completed BCS Target answersheet as a question-bank quiz."""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        serializer = BcsTargetAnswersheetImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            quiz_category = QuizCategory.objects.get(pk=data['quiz_category_id'])
        except QuizCategory.DoesNotExist:
            return Response(
                {'quiz_category_id': 'Quiz category not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        quiz, quiz_created = Quiz.objects.get_or_create(
            category=quiz_category,
            name=data['quiz_name'],
            quiz_type=Quiz.QuizType.QUESTION_BANK,
            defaults={
                'duration_minutes': data.get('duration_minutes') or len(data['questions']),
            },
        )

        duration_minutes = data.get('duration_minutes') or len(data['questions'])
        if quiz.duration_minutes != duration_minutes:
            quiz.duration_minutes = duration_minutes
            quiz.save(update_fields=['duration_minutes'])

        existing_question_texts = set(
            quiz.questions.values_list('question_text', flat=True)
        )
        created = 0
        skipped = 0
        errors = []

        for index, raw_question in enumerate(data['questions'], start=1):
            question_serializer = BcsTargetImportQuestionSerializer(data=raw_question)
            if not question_serializer.is_valid():
                skipped += 1
                errors.append({
                    'index': index,
                    'source_question_id': raw_question.get('source_question_id', ''),
                    'errors': question_serializer.errors,
                })
                continue

            question_data = question_serializer.validated_data
            question_text = question_data['question_text']

            if question_text in existing_question_texts:
                skipped += 1
                continue

            with transaction.atomic():
                question = Question.objects.create(
                    category=None,
                    question_text=question_text,
                    explanation=question_data.get('explanation') or None,
                )

                correct_option = question_data['correct_option'].upper()
                for letter, field_name in (
                    ('A', 'option_a'),
                    ('B', 'option_b'),
                    ('C', 'option_c'),
                    ('D', 'option_d'),
                ):
                    Answer.objects.create(
                        question=question,
                        text=question_data[field_name],
                        is_correct=(letter == correct_option),
                    )
                quiz.questions.add(question)
                existing_question_texts.add(question_text)
                created += 1

        return Response(
            {
                'quiz': QuizSerializer(quiz, context={'request': request}).data,
                'quiz_created': quiz_created,
                'total': len(data['questions']),
                'created': created,
                'skipped': skipped,
                'errors': errors,
            },
            status=status.HTTP_200_OK,
        )

class CreateModelTestView(APIView):
    """ Creates a custom model test based on hierarchical category selection """
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def post(self, request, *args, **kwargs):
        input_serializer = CustomModelTestCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data
        user, guest_user = get_user_or_guest(request)
        quiz = generate_quiz(
            configurations=data['config'],
            name=data['name'] or 'Custom Model Test',
            duration_minutes=data['duration_minutes'],
            correct_mark=Decimal('1.00'),
            wrong_mark=Decimal('0'),
            unanswered_mark=Decimal('0'),
            user=user,
            guest_user=guest_user,
            strict=False,
        )
        quiz = get_quiz_detail_queryset().get(pk=quiz.pk)
        question_ids = get_prefetched_quiz_question_ids(quiz)

        serializer = CandidateQuizDetailSerializer(
            quiz,
            context=get_model_test_serializer_context(request, question_ids),
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class QuizResultView(generics.RetrieveAPIView):
    """
    Provides the detailed result for a single, completed quiz attempt.
    Works for both regular users and guests.
    """
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    serializer_class = QuizAttemptResultSerializer
    lookup_url_kwarg = 'attempt_id'

    def get_queryset(self):
        """
        Filter quiz attempts based on user type
        """
        user_filter = get_user_queryset_filter(self.request)
        return get_quiz_attempt_result_queryset(
            QuizAttempt.objects.filter(user_filter, is_completed=True)
        )

    def retrieve(self, request, *args, **kwargs):
        attempt_id = kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        cache_key = get_quiz_result_cache_key(request, attempt_id)
        cached_data = cache.get(cache_key)
        if cached_data is not None and self.get_queryset().filter(pk=attempt_id).exists():
            return Response(cached_data)

        attempt = self.get_object()
        question_ids = get_prefetched_quiz_question_ids(attempt.quiz)
        serializer = self.get_serializer(
            attempt,
            context=get_model_test_serializer_context(request, question_ids),
        )
        data = serializer.data
        cache_quiz_result_response(request, attempt.id, data)
        return Response(data)


class ModelTestSubmitV2View(APIView):
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def post(self, request, attempt_id):
        return process_model_test_submission(
            request,
            attempt_id,
            serializer_class=CompactQuizAttemptResultSerializer,
            include_quiz_questions=False,
        )


class ModelTestSubmitSummaryV3View(APIView):
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def post(self, request, attempt_id):
        return process_model_test_submission(
            request,
            attempt_id,
            serializer_class=SummaryQuizAttemptResultSerializer,
            include_quiz_questions=False,
        )

class SavedQuestionFilter(django_filters.FilterSet):
    """
    Custom filter class to handle both category and label filtering
    """
    category = django_filters.CharFilter(method='filter_by_category')
    category_id = django_filters.NumberFilter(field_name='question__category__id')
    category_name = django_filters.CharFilter(field_name='question__category__name', lookup_expr='icontains')
    label = django_filters.CharFilter(field_name='question__labels__name', lookup_expr='icontains')
    label_name = django_filters.CharFilter(field_name='question__labels__name', lookup_expr='icontains')
    question__labels__name = django_filters.CharFilter(field_name='question__labels__name', lookup_expr='icontains')
    
    class Meta:
        model = SavedQuestion
        fields = [
            'category', 'category_id', 'category_name',
            'label', 'label_name', 'question__labels__name'
        ]
    
    def filter_by_category(self, queryset, name, value):
        return queryset.filter(
            models.Q(question__category__name__icontains=value) |
            models.Q(question__labels__name__icontains=value)
        ).distinct()

class SavedQuestionViewSet(mixins.CreateModelMixin, 
                           mixins.DestroyModelMixin, 
                           mixins.ListModelMixin, 
                           viewsets.GenericViewSet):
    """ 
    Allows users to list, save (create), and unsave (delete) questions 
    with proper category and label filtering support.
    Works for both regular users and guests.
    """
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    serializer_class = UniversalSavedQuestionSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = SavedQuestionFilter
    search_fields = ['question__question_text', 'question__explanation']
    ordering_fields = ['saved_at', 'question__category__name', 'question__labels__name']
    ordering = ['-saved_at']

    def get_queryset(self):
        user_filter = get_user_queryset_filter(self.request)
        return SavedQuestion.objects.filter(user_filter).select_related(
            'question__category'
        ).prefetch_related(
            'question__labels',
            'question__answers'
        ).distinct()

    def create(self, request, *args, **kwargs):
        question_id = request.data.get('question_id')
        if not question_id:
            return Response(
                {"error": "question_id is required."}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if the question exists
        if not Question.objects.filter(id=question_id).exists():
            return Response(
                {"error": f"Question with id {question_id} not found."}, 
                status=status.HTTP_404_NOT_FOUND
            )

        user, guest_user = get_user_or_guest(request)
        
        # Check for existing saved question
        if user:
            already_exists = SavedQuestion.objects.filter(user=user, question_id=question_id).exists()
            existing_instance = SavedQuestion.objects.filter(user=user, question_id=question_id).first()
        else:
            already_exists = SavedQuestion.objects.filter(guest_user=guest_user, question_id=question_id).exists()
            existing_instance = SavedQuestion.objects.filter(guest_user=guest_user, question_id=question_id).first()

        if already_exists:
            serializer = self.get_serializer(existing_instance)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # Create new saved question
        if user:
            saved_question = SavedQuestion.objects.create(user=user, question_id=question_id)
        else:
            saved_question = SavedQuestion.objects.create(guest_user=guest_user, question_id=question_id)
        
        serializer = self.get_serializer(saved_question)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='bulk-save')
    def bulk_save(self, request):
        """
        Saves multiple questions in a single API call.
        Works for both regular users and guests.
        """
        question_ids = request.data.get('question_ids')

        if not isinstance(question_ids, list):
            return Response(
                {"error": "The 'question_ids' field must be a list of integers."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user, guest_user = get_user_or_guest(request)
        
        # Filter out IDs that do not correspond to existing questions
        valid_question_ids = set(Question.objects.filter(id__in=question_ids).values_list('id', flat=True))
        
        # Find which questions are already saved
        if user:
            already_saved_ids = set(
                SavedQuestion.objects.filter(user=user, question_id__in=valid_question_ids)
                .values_list('question_id', flat=True)
            )
        else:
            already_saved_ids = set(
                SavedQuestion.objects.filter(guest_user=guest_user, question_id__in=valid_question_ids)
                .values_list('question_id', flat=True)
            )
        
        # Determine new questions to save
        ids_to_save = valid_question_ids - already_saved_ids
        
        if not ids_to_save:
            return Response({
                "status": "success",
                "message": "No new questions to save. All provided questions were either invalid or already saved.",
                "saved_count": 0,
                "already_saved_count": len(already_saved_ids)
            }, status=status.HTTP_200_OK)
            
        # Create new SavedQuestion objects
        if user:
            new_saves = [SavedQuestion(user=user, question_id=qid) for qid in ids_to_save]
        else:
            new_saves = [SavedQuestion(guest_user=guest_user, question_id=qid) for qid in ids_to_save]
        
        SavedQuestion.objects.bulk_create(new_saves)
        
        return Response({
            "status": "success",
            "message": f"Successfully processed the request.",
            "saved_count": len(new_saves),
            "already_saved_count": len(already_saved_ids)
        }, status=status.HTTP_201_CREATED)

class QuestionReportViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    """ Allows users to report a question - only for registered users """
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedUserOnly]  # Only registered users can report
    serializer_class = QuestionReportSerializer
    queryset = QuestionReport.objects.none()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

class ExamAttemptViewSet(viewsets.GenericViewSet):
    """ Handles the entire exam-taking process for both users and guests """
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    queryset = QuizAttempt.objects.all()

    def get_queryset(self):
        user_filter = get_user_queryset_filter(self.request)
        return QuizAttempt.objects.filter(user_filter)

    @action(detail=True, methods=['post'], url_path='start')
    def start_exam(self, request, pk=None):
        try:
            quiz = Quiz.objects.get(pk=pk)
        except Quiz.DoesNotExist:
            return Response({"error": "Quiz not found."}, status=status.HTTP_404_NOT_FOUND)
        
        user, guest_user = get_user_or_guest(request)
        if quiz.generated_for_user_id and quiz.generated_for_user_id != getattr(user, 'id', None):
            return Response(
                {'error': 'This generated quiz belongs to another user.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if quiz.generated_for_guest_id and quiz.generated_for_guest_id != getattr(guest_user, 'id', None):
            return Response(
                {'error': 'This generated quiz belongs to another guest.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        # Create or get quiz attempt
        if user:
            attempt, created = QuizAttempt.objects.get_or_create(
                user=user, 
                quiz=quiz, 
                is_completed=False,
                defaults={'start_time': timezone.now()}
            )
        else:
            attempt, created = QuizAttempt.objects.get_or_create(
                guest_user=guest_user, 
                quiz=quiz, 
                is_completed=False,
                defaults={'start_time': timezone.now()}
            )

        quiz = get_quiz_detail_queryset().get(pk=quiz.pk)
        question_ids = get_prefetched_quiz_question_ids(quiz)
        serializer = CandidateQuizDetailSerializer(
            quiz,
            context=get_model_test_serializer_context(request, question_ids),
        )
        return Response({
            "attempt_id": attempt.id,
            "started_at": attempt.start_time,
            "expires_at": get_attempt_expiration(attempt),
            "quiz_details": serializer.data
        })

    @action(detail=True, methods=['post'], url_path='submit-answer')
    def submit_answer(self, request, pk=None):
        user_filter = get_user_queryset_filter(request)
        
        try:
            attempt = QuizAttempt.objects.get(
                Q(pk=pk) & user_filter & Q(is_completed=False)
            )
        except QuizAttempt.DoesNotExist:
            return Response({"error": "Active quiz attempt not found."}, status=status.HTTP_404_NOT_FOUND)

        if attempt_is_expired(attempt):
            return expired_attempt_response(attempt)
        
        question_id = request.data.get('question_id')
        answer_id = request.data.get('answer_id')

        try:
            question = attempt.quiz.questions.get(pk=question_id)
        except (Question.DoesNotExist, TypeError, ValueError):
            return Response(
                {"error": "Question does not belong to this quiz."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        answer = None
        if answer_id is not None:
            try:
                answer = Answer.objects.get(pk=answer_id, question=question)
            except (Answer.DoesNotExist, TypeError, ValueError):
                return Response(
                    {"error": "Selected answer does not belong to the question."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        is_correct = bool(answer and answer.is_correct)
        UserSubmission.objects.update_or_create(
            attempt=attempt,
            question=question,
            defaults={'selected_answer': answer, 'is_correct': is_correct}
        )
        return Response({"status": "Answer submitted successfully."}, status=status.HTTP_200_OK)
        
    @action(detail=True, methods=['post'], url_path='submit-bulk')
    @transaction.atomic
    def submit_bulk_answers(self, request, pk=None):
        """
        Submits a list of answers, calculates score, and ends the exam in a single call.
        Works for both regular users and guests.
        """
        user_filter = get_user_queryset_filter(request)
        
        try:
            attempt = QuizAttempt.objects.select_for_update().select_related('quiz').get(
                Q(pk=pk) & user_filter & Q(is_completed=False)
            )
        except QuizAttempt.DoesNotExist:
            return Response({"error": "Active quiz attempt not found."}, status=status.HTTP_404_NOT_FOUND)

        if attempt_is_expired(attempt):
            return expired_attempt_response(attempt)

        input_serializer = BulkQuizSubmissionSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        submissions_data = input_serializer.validated_data['submissions']

        quiz_question_ids = set(
            attempt.quiz.questions.values_list('id', flat=True)
        )
        submitted_question_ids = [
            submission['question_id'] for submission in submissions_data
        ]
        seen_question_ids = set()
        duplicate_question_ids = set()
        for question_id in submitted_question_ids:
            if question_id in seen_question_ids:
                duplicate_question_ids.add(question_id)
            seen_question_ids.add(question_id)

        submitted_question_id_set = set(submitted_question_ids)
        if (
            duplicate_question_ids
            or submitted_question_id_set != quiz_question_ids
            or len(submitted_question_ids) != len(quiz_question_ids)
        ):
            return Response(
                {
                    'error': 'Submit exactly one row for every quiz question.',
                    'missing_question_ids': sorted(quiz_question_ids - submitted_question_id_set),
                    'unexpected_question_ids': sorted(submitted_question_id_set - quiz_question_ids),
                    'duplicate_question_ids': sorted(duplicate_question_ids),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        selected_answer_ids = {
            submission['selected_answer_id']
            for submission in submissions_data
            if submission['selected_answer_id'] is not None
        }
        answers_by_id = {
            answer.id: answer
            for answer in Answer.objects.filter(pk__in=selected_answer_ids)
        }
        answer_errors = {}
        submissions_to_create = []
        for index, submission in enumerate(submissions_data):
            question_id = submission['question_id']
            selected_answer_id = submission['selected_answer_id']
            answer = answers_by_id.get(selected_answer_id)
            if selected_answer_id is not None and (
                answer is None or answer.question_id != question_id
            ):
                answer_errors[str(index)] = [
                    'Selected answer does not belong to the submitted question.'
                ]
                continue
            submissions_to_create.append(UserSubmission(
                attempt=attempt,
                question_id=question_id,
                selected_answer_id=selected_answer_id,
                is_correct=bool(answer and answer.is_correct),
            ))

        if answer_errors:
            return Response(
                {'submissions': answer_errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Clear any previous submissions for this attempt to prevent duplicates
        UserSubmission.objects.filter(attempt=attempt).delete()
        
        # Create all new submissions in one database query
        UserSubmission.objects.bulk_create(submissions_to_create)

        correct_count = sum(1 for submission in submissions_to_create if submission.is_correct)
        wrong_count = sum(
            1 for submission in submissions_to_create
            if submission.selected_answer_id is not None and not submission.is_correct
        )
        unanswered_count = sum(
            1 for submission in submissions_to_create
            if submission.selected_answer_id is None
        )
        score = (
            correct_count * attempt.quiz.correct_mark
            + wrong_count * attempt.quiz.wrong_mark
            + unanswered_count * attempt.quiz.unanswered_mark
        )
        
        attempt.score = score
        attempt.is_completed = True
        attempt.end_time = timezone.now()
        attempt.save(update_fields=['score', 'is_completed', 'end_time'])

        # Track activities and progress
        user, guest_user = get_user_or_guest(request)
        duration_minutes = None
        if attempt.start_time and attempt.end_time:
            duration = attempt.end_time - attempt.start_time
            duration_minutes = int(duration.total_seconds() / 60)
        
        # Track quiz completion
        track_quiz_completed(
            user,
            guest_user,
            attempt,
            duration_minutes,
            correct_count=correct_count,
        )

        # Track individual question answers without per-question database work.
        track_question_answers_bulk(user, guest_user, submissions_to_create)

        # Update guest user stats if applicable
        if guest_user:
            GuestUser.objects.filter(pk=guest_user.pk).update(
                total_quizzes_completed=models.F('total_quizzes_completed') + 1,
                total_questions_answered=(
                    models.F('total_questions_answered') + correct_count + wrong_count
                ),
            )

        attempt = get_quiz_attempt_result_queryset(
            QuizAttempt.objects.filter(pk=attempt.pk)
        ).get()
        question_ids = get_prefetched_quiz_question_ids(attempt.quiz)
        serializer = QuizAttemptResultSerializer(
            attempt,
            context=get_model_test_serializer_context(request, question_ids),
        )
        data = serializer.data
        cache_quiz_result_response(request, attempt.id, data)
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='end')
    @transaction.atomic
    def end_exam(self, request, pk=None):
        user_filter = get_user_queryset_filter(request)
        
        try:
            attempt = QuizAttempt.objects.select_for_update().select_related('quiz').get(
                Q(pk=pk) & user_filter & Q(is_completed=False)
            )
        except QuizAttempt.DoesNotExist:
            return Response({"error": "Active quiz attempt not found."}, status=status.HTTP_404_NOT_FOUND)

        if attempt_is_expired(attempt):
            return expired_attempt_response(attempt)

        quiz_question_ids = set(attempt.quiz.questions.values_list('id', flat=True))
        existing_question_ids = set(
            UserSubmission.objects.filter(
                attempt=attempt,
                question_id__in=quiz_question_ids,
            ).values_list('question_id', flat=True)
        )
        UserSubmission.objects.bulk_create([
            UserSubmission(
                attempt=attempt,
                question_id=question_id,
                selected_answer=None,
                is_correct=False,
            )
            for question_id in quiz_question_ids - existing_question_ids
        ])

        submissions = UserSubmission.objects.filter(
            attempt=attempt,
            question_id__in=quiz_question_ids,
        )
        correct_count = submissions.filter(is_correct=True).count()
        wrong_count = submissions.filter(
            selected_answer__isnull=False,
            is_correct=False,
        ).count()
        unanswered_count = submissions.filter(selected_answer__isnull=True).count()
        attempt.score = (
            correct_count * attempt.quiz.correct_mark
            + wrong_count * attempt.quiz.wrong_mark
            + unanswered_count * attempt.quiz.unanswered_mark
        )
        attempt.is_completed = True
        attempt.end_time = timezone.now()
        attempt.save(update_fields=['score', 'is_completed', 'end_time'])

        # Track quiz completion
        user, guest_user = get_user_or_guest(request)
        duration_minutes = None
        if attempt.start_time and attempt.end_time:
            duration = attempt.end_time - attempt.start_time
            duration_minutes = int(duration.total_seconds() / 60)
        
        track_quiz_completed(
            user,
            guest_user,
            attempt,
            duration_minutes,
            correct_count=correct_count,
        )

        # Update guest user stats if applicable
        if guest_user:
            GuestUser.objects.filter(pk=guest_user.pk).update(
                total_quizzes_completed=models.F('total_quizzes_completed') + 1,
            )

        attempt = get_quiz_attempt_result_queryset(
            QuizAttempt.objects.filter(pk=attempt.pk)
        ).get()
        question_ids = get_prefetched_quiz_question_ids(attempt.quiz)
        serializer = QuizAttemptResultSerializer(
            attempt,
            context=get_model_test_serializer_context(request, question_ids),
        )
        data = serializer.data
        cache_quiz_result_response(request, attempt.id, data)
        return Response(data, status=status.HTTP_200_OK)

# ===================================================================
# VIEWSETS FOR DAILY TARGETS AND PROGRESS TRACKING
# ===================================================================

class DailyTargetViewSet(viewsets.ModelViewSet):
    """ViewSet for managing user daily targets - works for both users and guests"""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    serializer_class = UniversalDailyTargetSerializer
    
    def get_queryset(self):
        user_filter = get_user_queryset_filter(self.request)
        return DailyTarget.objects.filter(user_filter)
    
    def perform_create(self, serializer):
        user, guest_user = get_user_or_guest(self.request)
        if user:
            serializer.save(user=user)
        else:
            serializer.save(guest_user=guest_user)
    
    @action(detail=False, methods=['post'])
    def set_targets(self, request):
        """Bulk set multiple targets at once"""
        targets_data = request.data.get('targets', [])
        
        if not isinstance(targets_data, list):
            return Response(
                {"error": "Targets must be a list"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user, guest_user = get_user_or_guest(request)
        created_targets = []
        updated_targets = []
        
        for target_data in targets_data:
            target_type = target_data.get('target_type')
            target_value = target_data.get('target_value')
            
            if not target_type or not target_value:
                continue
            
            if user:
                target, created = DailyTarget.objects.update_or_create(
                    user=user,
                    target_type=target_type,
                    defaults={
                        'target_value': target_value,
                        'is_active': target_data.get('is_active', True)
                    }
                )
            else:
                target, created = DailyTarget.objects.update_or_create(
                    guest_user=guest_user,
                    target_type=target_type,
                    defaults={
                        'target_value': target_value,
                        'is_active': target_data.get('is_active', True)
                    }
                )
            
            if created:
                created_targets.append(target)
            else:
                updated_targets.append(target)
        
        return Response({
            "created": len(created_targets),
            "updated": len(updated_targets),
            "targets": UniversalDailyTargetSerializer(created_targets + updated_targets, many=True).data
        })

    @action(detail=False, methods=['get'])
    def active_targets(self, request):
        """Get only active targets"""
        targets = self.get_queryset().filter(is_active=True)
        serializer = self.get_serializer(targets, many=True)
        return Response(serializer.data)

class DailyProgressViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing daily progress - works for both users and guests"""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    serializer_class = UniversalDailyProgressSerializer
    
    def get_queryset(self):
        user_filter = get_user_queryset_filter(self.request)
        queryset = DailyProgress.objects.filter(user_filter)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
        if end_date:
            queryset = queryset.filter(date__lte=end_date)
        
        # Filter by target type
        target_type = self.request.query_params.get('target_type')
        if target_type:
            queryset = queryset.filter(target_type=target_type)
        
        return queryset.order_by('-date')
    
    @action(detail=False, methods=['get'])
    def today(self, request):
        """Get today's progress for all target types"""
        today = date.today()
        user_filter = get_user_queryset_filter(request)
        progress = DailyProgress.objects.filter(user_filter, date=today)
        serializer = self.get_serializer(progress, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def week(self, request):
        """Get this week's progress"""
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        
        user_filter = get_user_queryset_filter(request)
        progress = DailyProgress.objects.filter(
            user_filter,
            date__range=[week_start, week_end]
        ).order_by('date')
        
        serializer = self.get_serializer(progress, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def month(self, request):
        """Get this month's progress"""
        today = date.today()
        month_start = today.replace(day=1)
        
        user_filter = get_user_queryset_filter(request)
        progress = DailyProgress.objects.filter(
            user_filter,
            date__gte=month_start,
            date__lte=today
        ).order_by('date')
        
        serializer = self.get_serializer(progress, many=True)
        return Response(serializer.data)

class WeeklyProgressViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing weekly progress summaries"""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    serializer_class = WeeklyProgressSerializer
    
    def get_queryset(self):
        user_filter = get_user_queryset_filter(self.request)
        return WeeklyProgress.objects.filter(user_filter).order_by('-week_start_date')

    @action(detail=False, methods=['get'])
    def current_week(self, request):
        """Get current week's progress"""
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        
        user_filter = get_user_queryset_filter(request)
        progress = WeeklyProgress.objects.filter(
            user_filter,
            week_start_date=week_start
        )
        
        serializer = self.get_serializer(progress, many=True)
        return Response(serializer.data)

class UserActivityViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing user activities"""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    serializer_class = UniversalUserActivitySerializer
    pagination_class = StandardResultsSetPagination
    
    def get_queryset(self):
        user_filter = get_user_queryset_filter(self.request)
        queryset = UserActivity.objects.filter(user_filter).select_related(
            'question', 'quiz_attempt__quiz'
        )
        
        # Filter by activity type
        activity_type = self.request.query_params.get('activity_type')
        if activity_type:
            queryset = queryset.filter(activity_type=activity_type)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            queryset = queryset.filter(activity_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(activity_date__lte=end_date)
        
        return queryset.order_by('-activity_time')
    
    @action(detail=False, methods=['get'])
    def today(self, request):
        """Get today's activities"""
        today = date.today()
        activities = self.get_queryset().filter(activity_date=today)
        page = self.paginate_queryset(activities)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(activities, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get activity summary statistics"""
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        
        user_filter = get_user_queryset_filter(request)
        user_activities = UserActivity.objects.filter(user_filter)
        
        summary = {
            'today': {
                'questions_answered': user_activities.filter(
                    activity_date=today,
                    activity_type=UserActivity.ActivityType.QUESTION_ANSWERED
                ).count(),
                'quizzes_completed': user_activities.filter(
                    activity_date=today,
                    activity_type__in=[
                        UserActivity.ActivityType.QUIZ_COMPLETED,
                        UserActivity.ActivityType.MODEL_TEST_COMPLETED
                    ]
                ).count(),
            },
            'this_week': {
                'questions_answered': user_activities.filter(
                    activity_date__gte=week_start,
                    activity_type=UserActivity.ActivityType.QUESTION_ANSWERED
                ).count(),
                'quizzes_completed': user_activities.filter(
                    activity_date__gte=week_start,
                    activity_type__in=[
                        UserActivity.ActivityType.QUIZ_COMPLETED,
                        UserActivity.ActivityType.MODEL_TEST_COMPLETED
                    ]
                ).count(),
            },
            'this_month': {
                'questions_answered': user_activities.filter(
                    activity_date__gte=month_start,
                    activity_type=UserActivity.ActivityType.QUESTION_ANSWERED
                ).count(),
                'quizzes_completed': user_activities.filter(
                    activity_date__gte=month_start,
                    activity_type__in=[
                        UserActivity.ActivityType.QUIZ_COMPLETED,
                        UserActivity.ActivityType.MODEL_TEST_COMPLETED
                    ]
                ).count(),
            }
        }
        
        return Response(summary)

class StreakViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing user streaks"""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    serializer_class = UniversalStreakSerializer
    
    def get_queryset(self):
        user_filter = get_user_queryset_filter(self.request)
        return Streak.objects.filter(user_filter)

    @action(detail=False, methods=['get'])
    def detailed(self, request):
        """Get detailed streak information with motivational messages"""
        streaks = self.get_queryset()
        serializer = StreakDetailSerializer(streaks, many=True)
        return Response(serializer.data)

class ProgressDashboardView(APIView):
    """Comprehensive dashboard view for user progress - works for both users and guests"""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    
    def get(self, request):
        user, guest_user = get_user_or_guest(request)
        user_filter = get_user_queryset_filter(request)
        
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        
        # Get today's progress
        today_progress = DailyProgress.objects.filter(user_filter, date=today)
        
        # Get this week's progress summary
        weekly_progress = WeeklyProgress.objects.filter(
            user_filter, 
            week_start_date=week_start
        )
        
        # Get user streaks
        streaks = Streak.objects.filter(user_filter)
        
        # Get recent activities (last 10)
        recent_activities = UserActivity.objects.filter(user_filter).select_related(
            'question', 'quiz_attempt__quiz'
        ).order_by('-activity_time')[:10]
        
        # Get user targets
        targets = DailyTarget.objects.filter(user_filter, is_active=True)
        
        # Calculate summary statistics
        today_activities = UserActivity.objects.filter(user_filter, activity_date=today)
        
        total_questions_today = today_activities.filter(
            activity_type=UserActivity.ActivityType.QUESTION_ANSWERED
        ).count()
        
        total_quizzes_today = today_activities.filter(
            activity_type__in=[
                UserActivity.ActivityType.QUIZ_COMPLETED,
                UserActivity.ActivityType.MODEL_TEST_COMPLETED
            ]
        ).count()
        
        total_study_time_today = today_activities.aggregate(
            total_time=Sum('duration_minutes')
        )['total_time'] or 0
        
        # Weekly statistics
        week_activities = UserActivity.objects.filter(
            user_filter, 
            activity_date__range=[week_start, week_end]
        )
        
        weekly_questions = week_activities.filter(
            activity_type=UserActivity.ActivityType.QUESTION_ANSWERED
        ).count()
        
        weekly_quizzes = week_activities.filter(
            activity_type__in=[
                UserActivity.ActivityType.QUIZ_COMPLETED,
                UserActivity.ActivityType.MODEL_TEST_COMPLETED
            ]
        ).count()
        
        weekly_study_time = week_activities.aggregate(
            total_time=Sum('duration_minutes')
        )['total_time'] or 0
        
        # Determine user type and identifier
        user_type = 'guest' if guest_user else 'registered'
        user_identifier = str(guest_user.guest_id) if guest_user else user.username
        
        dashboard_data = {
            'today_progress': UniversalDailyProgressSerializer(today_progress, many=True).data,
            'weekly_progress': WeeklyProgressSerializer(weekly_progress, many=True).data,
            'streaks': UniversalStreakSerializer(streaks, many=True).data,
            'recent_activities': UniversalUserActivitySerializer(recent_activities, many=True).data,
            'targets': UniversalDailyTargetSerializer(targets, many=True).data,
            'total_questions_solved_today': total_questions_today,
            'total_quizzes_completed_today': total_quizzes_today,
            'total_study_time_today': total_study_time_today,
            'weekly_questions_solved': weekly_questions,
            'weekly_quizzes_completed': weekly_quizzes,
            'weekly_study_time': weekly_study_time,
            'user_type': user_type,
            'user_identifier': user_identifier,
        }
        
        return Response(dashboard_data)

class ProgressAnalyticsView(APIView):
    """Advanced analytics view for progress tracking"""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    
    def get(self, request):
        user_filter = get_user_queryset_filter(request)
        days = int(request.query_params.get('days', 30))  # Default 30 days
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # Get daily progress for the period
        daily_progress = DailyProgress.objects.filter(
            user_filter,
            date__range=[start_date, end_date]
        ).order_by('date')
        
        # Group by target type
        progress_by_type = defaultdict(list)
        for progress in daily_progress:
            progress_by_type[progress.target_type].append({
                'date': progress.date,
                'current_value': progress.current_value,
                'target_value': progress.target_value,
                'completion_percentage': progress.completion_percentage,
                'is_completed': progress.is_completed
            })
        
        # Calculate streaks and completion rates
        analytics = {}
        for target_type, progress_list in progress_by_type.items():
            completed_days = len([p for p in progress_list if p['is_completed']])
            total_days = len(progress_list)
            completion_rate = (completed_days / total_days * 100) if total_days > 0 else 0
            
            # Calculate average completion percentage
            avg_completion = sum(p['completion_percentage'] for p in progress_list) / len(progress_list) if progress_list else 0
            
            analytics[target_type] = {
                'progress_data': progress_list,
                'completion_rate': round(completion_rate, 2),
                'completed_days': completed_days,
                'total_days': total_days,
                'average_completion': round(avg_completion, 2),
                'target_type_display': dict(DailyTarget.TargetType.choices)[target_type]
            }
        
        return Response({
            'period': f'{days} days',
            'start_date': start_date,
            'end_date': end_date,
            'analytics': analytics
        })

# ===================================================================
# GUEST USER SPECIFIC VIEWS
# ===================================================================

class GuestUserStatsView(APIView):
    """Get guest user statistics"""
    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    
    def get(self, request):
        if not getattr(request.user, 'is_guest', False):
            return Response(
                {"error": "This endpoint is only for guest users"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        guest_user = request.user.guest_user
        
        # Get basic stats
        stats = {
            'guest_id': guest_user.guest_id,
            'total_questions_answered': guest_user.total_questions_answered,
            'total_quizzes_completed': guest_user.total_quizzes_completed,
            'account_age_days': (timezone.now().date() - guest_user.created_at.date()).days,
            'last_active': guest_user.last_active,
        }
        
        # Get recent activity counts
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        
        recent_stats = {
            'questions_today': UserActivity.objects.filter(
                guest_user=guest_user,
                activity_date=today,
                activity_type=UserActivity.ActivityType.QUESTION_ANSWERED
            ).count(),
            'quizzes_this_week': UserActivity.objects.filter(
                guest_user=guest_user,
                activity_date__gte=week_start,
                activity_type__in=[
                    UserActivity.ActivityType.QUIZ_COMPLETED,
                    UserActivity.ActivityType.MODEL_TEST_COMPLETED
                ]
            ).count(),
        }
        
        return Response({
            'guest_stats': stats,
            'recent_activity': recent_stats
        })

# ===================================================================
# UTILITY FUNCTIONS FOR PROGRESS TRACKING (UPDATED FOR GUEST SUPPORT)
# ===================================================================

def track_question_answered(user, guest_user, question, is_correct=False, duration_minutes=None):
    """Track when a user or guest answers a question"""
    if user:
        activity = UserActivity.objects.create(
            user=user,
            activity_type=UserActivity.ActivityType.QUESTION_ANSWERED,
            question=question,
            duration_minutes=duration_minutes,
            points_earned=1 if is_correct else 0
        )
    else:
        activity = UserActivity.objects.create(
            guest_user=guest_user,
            activity_type=UserActivity.ActivityType.QUESTION_ANSWERED,
            question=question,
            duration_minutes=duration_minutes,
            points_earned=1 if is_correct else 0
        )
    
    # Update daily progress for questions solved
    update_daily_progress(user, guest_user, DailyTarget.TargetType.QUESTIONS_SOLVED, 1)
    
    # Update streak
    update_streak(user, guest_user, DailyTarget.TargetType.QUESTIONS_SOLVED)
    
    return activity


def track_question_answers_bulk(user, guest_user, submissions, duration_minutes=None):
    """Track answered questions in bulk for model-test submission."""
    answered_submissions = [
        submission for submission in submissions
        if submission.selected_answer_id is not None
    ]
    if not answered_submissions:
        return []

    if user:
        activities = [
            UserActivity(
                user=user,
                activity_type=UserActivity.ActivityType.QUESTION_ANSWERED,
                question_id=submission.question_id,
                duration_minutes=duration_minutes,
                points_earned=1 if submission.is_correct else 0,
            )
            for submission in answered_submissions
        ]
    else:
        activities = [
            UserActivity(
                guest_user=guest_user,
                activity_type=UserActivity.ActivityType.QUESTION_ANSWERED,
                question_id=submission.question_id,
                duration_minutes=duration_minutes,
                points_earned=1 if submission.is_correct else 0,
            )
            for submission in answered_submissions
        ]

    created_activities = UserActivity.objects.bulk_create(activities)
    update_daily_progress(
        user,
        guest_user,
        DailyTarget.TargetType.QUESTIONS_SOLVED,
        len(answered_submissions),
    )
    update_streak(user, guest_user, DailyTarget.TargetType.QUESTIONS_SOLVED)
    return created_activities


def track_quiz_completed(user, guest_user, quiz_attempt, duration_minutes=None, correct_count=None):
    """Track when a user or guest completes a quiz"""
    activity_type = (
        UserActivity.ActivityType.MODEL_TEST_COMPLETED 
        if quiz_attempt.quiz.quiz_type == Quiz.QuizType.MODEL_TEST 
        else UserActivity.ActivityType.QUIZ_COMPLETED
    )
    # Activity points predate decimal/negative scoring and remain an integer count.
    activity_points = (
        correct_count
        if correct_count is not None
        else quiz_attempt.submissions.filter(is_correct=True).count()
    )
    
    if user:
        activity = UserActivity.objects.create(
            user=user,
            activity_type=activity_type,
            quiz_attempt=quiz_attempt,
            duration_minutes=duration_minutes,
            points_earned=activity_points
        )
    else:
        activity = UserActivity.objects.create(
            guest_user=guest_user,
            activity_type=activity_type,
            quiz_attempt=quiz_attempt,
            duration_minutes=duration_minutes,
            points_earned=activity_points
        )
    
    # Update daily progress
    if quiz_attempt.quiz.quiz_type == Quiz.QuizType.MODEL_TEST:
        update_daily_progress(user, guest_user, DailyTarget.TargetType.MODEL_TESTS_TAKEN, 1)
        update_streak(user, guest_user, DailyTarget.TargetType.MODEL_TESTS_TAKEN)
    else:
        update_daily_progress(user, guest_user, DailyTarget.TargetType.QUIZ_ATTEMPTS, 1)
        update_streak(user, guest_user, DailyTarget.TargetType.QUIZ_ATTEMPTS)
    
    return activity

def update_daily_progress(user, guest_user, target_type, increment_value=1, activity_date=None):
    """Update daily progress for a specific target type"""
    if activity_date is None:
        activity_date = date.today()
    
    # Get user's target for this type
    try:
        if user:
            target = DailyTarget.objects.get(user=user, target_type=target_type, is_active=True)
        else:
            target = DailyTarget.objects.get(guest_user=guest_user, target_type=target_type, is_active=True)
        target_value = target.target_value
    except DailyTarget.DoesNotExist:
        target_value = 0  # No target set
    
    # Get or create daily progress
    if user:
        progress, created = DailyProgress.objects.get_or_create(
            user=user,
            target_type=target_type,
            date=activity_date,
            defaults={
                'target_value': target_value,
                'current_value': 0
            }
        )
    else:
        progress, created = DailyProgress.objects.get_or_create(
            guest_user=guest_user,
            target_type=target_type,
            date=activity_date,
            defaults={
                'target_value': target_value,
                'current_value': 0
            }
        )
    
    # Update current value
    progress.current_value += increment_value
    progress.target_value = target_value  # Update in case target changed
    progress.save()  # This will auto-calculate completion percentage
    
    # Update weekly progress
    update_weekly_progress(user, guest_user, target_type, activity_date)
    
    return progress

def update_weekly_progress(user, guest_user, target_type, activity_date=None):
    """Update weekly progress summary"""
    if activity_date is None:
        activity_date = date.today()
    
    # Calculate week start (Monday)
    week_start = activity_date - timedelta(days=activity_date.weekday())
    week_end = week_start + timedelta(days=6)
    
    # Get daily progress for this week
    if user:
        daily_progress = DailyProgress.objects.filter(
            user=user,
            target_type=target_type,
            date__range=[week_start, week_end]
        )
    else:
        daily_progress = DailyProgress.objects.filter(
            guest_user=guest_user,
            target_type=target_type,
            date__range=[week_start, week_end]
        )
    
    weekly_totals = daily_progress.aggregate(
        total_target=Sum('target_value'),
        total_achieved=Sum('current_value'),
        days_completed=models.Count('id', filter=Q(is_completed=True)),
    )
    total_target = weekly_totals['total_target'] or 0
    total_achieved = weekly_totals['total_achieved'] or 0
    days_completed = weekly_totals['days_completed'] or 0
    
    completion_percentage = (total_achieved / total_target * 100) if total_target > 0 else 0
    
    # Update or create weekly progress
    if user:
        weekly_progress, created = WeeklyProgress.objects.update_or_create(
            user=user,
            target_type=target_type,
            week_start_date=week_start,
            defaults={
                'total_target': total_target,
                'total_achieved': total_achieved,
                'days_completed': days_completed,
                'completion_percentage': completion_percentage
            }
        )
    else:
        weekly_progress, created = WeeklyProgress.objects.update_or_create(
            guest_user=guest_user,
            target_type=target_type,
            week_start_date=week_start,
            defaults={
                'total_target': total_target,
                'total_achieved': total_achieved,
                'days_completed': days_completed,
                'completion_percentage': completion_percentage
            }
        )
    
    return weekly_progress

def update_streak(user, guest_user, target_type, activity_date=None):
    """Update user or guest streak for a target type"""
    if activity_date is None:
        activity_date = date.today()
    
    if user:
        streak, created = Streak.objects.get_or_create(
            user=user,
            target_type=target_type
        )
    else:
        streak, created = Streak.objects.get_or_create(
            guest_user=guest_user,
            target_type=target_type
        )
    
    streak.update_streak(activity_date)
    return streak
