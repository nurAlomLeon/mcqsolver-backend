"""Daily Exam generation, scheduling and leaderboard helpers.

The exam is shared by every learner for a Dhaka calendar date: one category is
selected at random, 20 valid questions are sampled once, and the resulting quiz
is reused for every attempt. All time comparisons are done in Asia/Dhaka even
though the server clock runs in UTC.
"""

import random
from collections import Counter
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import (
    Category,
    DailyExam,
    DailyExamAttempt,
    Question,
    Quiz,
)
from .services import get_effective_category_ids

DHAKA_TZ = ZoneInfo('Asia/Dhaka')
DEADLINE_GRACE = timedelta(seconds=5)

# Keep these defaults here as well as in project settings. Some production
# deployments load a site-specific settings module that may not yet define the
# optional Daily Exam knobs; the feature should still use the documented
# schedule rather than crash during command/API startup.
DAILY_EXAM_OPEN_HOUR = getattr(settings, 'DAILY_EXAM_OPEN_HOUR', 21)
DAILY_EXAM_CLOSE_HOUR = getattr(settings, 'DAILY_EXAM_CLOSE_HOUR', 23)
DAILY_EXAM_DURATION_MINUTES = getattr(
    settings,
    'DAILY_EXAM_DURATION_MINUTES',
    20,
)
DAILY_EXAM_QUESTION_COUNT = getattr(settings, 'DAILY_EXAM_QUESTION_COUNT', 20)
DAILY_EXAM_TOTAL_MARKS = getattr(settings, 'DAILY_EXAM_TOTAL_MARKS', 20)


def local_date(now=None):
    """The current calendar date in Bangladesh."""
    return (now or timezone.now()).astimezone(DHAKA_TZ).date()


def exam_window_for_date(exam_date):
    """Return the aware UTC open/close datetimes for a Dhaka exam date."""
    opens_at = datetime.combine(
        exam_date,
        time(DAILY_EXAM_OPEN_HOUR, 0),
        tzinfo=DHAKA_TZ,
    )
    closes_at = datetime.combine(
        exam_date,
        time(DAILY_EXAM_CLOSE_HOUR, 0),
        tzinfo=DHAKA_TZ,
    )
    return opens_at, closes_at


def daily_exam_duration():
    return timedelta(minutes=DAILY_EXAM_DURATION_MINUTES)


def valid_questions_queryset(category_ids):
    """Questions that are safe for a single-answer competitive exam.

    A valid question has text, at least two options and exactly one correct
    answer. Multi-correct or incomplete questions would make automatic grading
    ambiguous, so they are excluded from the daily exam pool.
    """
    return (
        Question.objects
        .filter(category_id__in=list(category_ids))
        .exclude(question_text__exact='')
        .annotate(
            daily_answer_count=Count('answers'),
            daily_correct_count=Count(
                'answers',
                filter=Q(answers__is_correct=True),
            ),
        )
        .filter(daily_answer_count__gte=2, daily_correct_count=1)
    )


def category_effective_ids(category):
    return get_effective_category_ids(category, include_subcategories=True)


def category_valid_question_ids(category):
    return list(
        valid_questions_queryset(category_effective_ids(category))
        .values_list('id', flat=True)
    )


def select_daily_exam_category(required_count=None):
    """Pick a random leaf subcategory with enough valid questions.

    Leaf categories are the intended pool. If the question bank is organised
    with questions on parent categories, fall back to the deepest non-root
    categories whose combined subtree has enough questions. This keeps the
    feature usable without silently changing the "subcategory" rule.
    """
    required_count = required_count or DAILY_EXAM_QUESTION_COUNT

    leaf_ids = list(
        Category.objects.filter(
            parent__isnull=False,
            children__isnull=True,
        ).values_list('id', flat=True)
    )
    if leaf_ids:
        counts = Counter(
            valid_questions_queryset(leaf_ids).values_list(
                'category_id',
                flat=True,
            )
        )
        eligible_leaf_ids = [
            category_id for category_id, count in counts.items()
            if count >= required_count
        ]
        if eligible_leaf_ids:
            return Category.objects.get(pk=random.choice(eligible_leaf_ids))

    # Fallback for databases where questions hang off a non-leaf category.
    candidates = []
    for category in (
        Category.objects.filter(parent__isnull=False)
        .order_by('-level', 'name')
    ):
        if len(category_valid_question_ids(category)) >= required_count:
            candidates.append(category)
    if candidates:
        return random.choice(candidates)
    return None


def ensure_daily_exam(exam_date=None, now=None, category=None, force=False):
    """Return ``(exam, created)`` for the date, generating it when needed.

    Returns ``(None, False)`` when no eligible category has enough questions or
    when the requested date has already closed.
    """
    now = now or timezone.now()
    exam_date = exam_date or local_date(now)
    opens_at, closes_at = exam_window_for_date(exam_date)

    existing = (
        DailyExam.objects
        .select_related('category', 'quiz')
        .filter(exam_date=exam_date)
        .first()
    )
    if existing is not None:
        return existing, False
    if not force and now >= closes_at:
        return None, False

    category = category or select_daily_exam_category()
    if category is None:
        return None, False

    question_ids = category_valid_question_ids(category)
    required_count = DAILY_EXAM_QUESTION_COUNT
    if len(question_ids) < required_count:
        return None, False

    selected_question_ids = random.sample(question_ids, required_count)
    quiz = Quiz.objects.create(
        name=f'Daily Exam {exam_date} - {category.name}',
        quiz_type=Quiz.QuizType.DAILY_EXAM,
        duration_minutes=DAILY_EXAM_DURATION_MINUTES,
        correct_mark=Decimal('1.00'),
        wrong_mark=Decimal('0.00'),
        unanswered_mark=Decimal('0.00'),
    )
    quiz.questions.set(selected_question_ids)

    try:
        with transaction.atomic():
            exam = DailyExam.objects.create(
                exam_date=exam_date,
                category=category,
                quiz=quiz,
                opens_at=opens_at,
                closes_at=closes_at,
                total_marks=DAILY_EXAM_TOTAL_MARKS,
            )
    except IntegrityError:
        # Another worker generated the same day's exam first. Drop our
        # duplicate quiz and return the winner.
        quiz.delete()
        existing = DailyExam.objects.select_related('category', 'quiz').get(
            exam_date=exam_date,
        )
        return existing, False

    return exam, True


def get_owner_attempt(exam, user=None, guest_user=None):
    if user is not None:
        return (
            DailyExamAttempt.objects
            .select_related('quiz_attempt', 'quiz_attempt__quiz')
            .filter(exam=exam, user=user)
            .first()
        )
    if guest_user is not None:
        return (
            DailyExamAttempt.objects
            .select_related('quiz_attempt', 'quiz_attempt__quiz')
            .filter(exam=exam, guest_user=guest_user)
            .first()
        )
    return None


def attempt_deadline(daily_attempt):
    quiz_attempt = daily_attempt.quiz_attempt
    start = quiz_attempt.start_time
    duration_deadline = start + daily_exam_duration()
    return min(duration_deadline, daily_attempt.exam.closes_at)


def attempt_is_expired(daily_attempt, now=None):
    return (now or timezone.now()) > attempt_deadline(daily_attempt) + DEADLINE_GRACE


def start_gets_full_duration(exam, started_at):
    """Whether this start leaves the learner the complete exam duration."""
    return started_at + daily_exam_duration() <= exam.closes_at


def attempt_status(daily_attempt, now=None):
    if daily_attempt.submitted_at is not None:
        return 'SUBMITTED'
    if attempt_is_expired(daily_attempt, now=now):
        return 'EXPIRED'
    return 'IN_PROGRESS'


def attempt_stats(daily_attempt, total_questions=None):
    """Correct/wrong/unanswered/accuracy counts for one attempt."""
    quiz_attempt = daily_attempt.quiz_attempt
    submissions = list(quiz_attempt.submissions.all())
    total_questions = (
        total_questions
        if total_questions is not None
        else quiz_attempt.quiz.questions.count()
    )

    answered = sum(
        1 for submission in submissions
        if submission.selected_answer_id is not None
    )
    correct = sum(1 for submission in submissions if submission.is_correct)
    wrong = sum(
        1 for submission in submissions
        if submission.selected_answer_id is not None and not submission.is_correct
    )
    unanswered = max(total_questions - answered, 0)
    accuracy = round(correct * 100 / answered, 2) if answered else 0.0

    return {
        'total_questions': total_questions,
        'answered': answered,
        'correct': correct,
        'wrong': wrong,
        'unanswered': unanswered,
        'accuracy': accuracy,
    }


def finalize_expired_attempt(daily_attempt, now=None):
    """Close an abandoned attempt with whatever was saved on the server.

    The Flutter app submits the whole answer sheet at the end; an attempt that
    expires without a submission is intentionally left out of the leaderboard
    (``submitted_at`` stays null). This helper only keeps the QuizAttempt row
    consistent for admin/history screens.
    """
    if daily_attempt.quiz_attempt.is_completed:
        return daily_attempt

    now = now or timezone.now()
    if not attempt_is_expired(daily_attempt, now=now):
        return daily_attempt

    quiz_attempt = daily_attempt.quiz_attempt
    stats = attempt_stats(daily_attempt)
    score = (
        stats['correct'] * quiz_attempt.quiz.correct_mark
        + stats['wrong'] * quiz_attempt.quiz.wrong_mark
        + stats['unanswered'] * quiz_attempt.quiz.unanswered_mark
    )
    quiz_attempt.score = score
    quiz_attempt.is_completed = True
    quiz_attempt.end_time = attempt_deadline(daily_attempt)
    quiz_attempt.save(update_fields=['score', 'is_completed', 'end_time'])

    daily_attempt.time_taken_seconds = max(
        int((quiz_attempt.end_time - quiz_attempt.start_time).total_seconds()),
        0,
    )
    daily_attempt.save(update_fields=['time_taken_seconds'])
    return daily_attempt


def display_name(user):
    name = ' '.join(
        part for part in [user.first_name.strip(), user.last_name.strip()] if part
    )
    return name or user.username


def _ranked_attempts(exam):
    """Submitted, ranked attempts in leaderboard order."""
    return (
        DailyExamAttempt.objects
        .filter(
            exam=exam,
            user__isnull=False,
            is_ranked=True,
            submitted_at__isnull=False,
            quiz_attempt__is_completed=True,
        )
        .select_related('user', 'quiz_attempt')
        .prefetch_related('quiz_attempt__submissions')
        .order_by(
            '-quiz_attempt__score',
            'time_taken_seconds',
            'submitted_at',
            'user_id',
        )
    )


def _entry_payload(daily_attempt, rank, current_user_id, total_questions):
    quiz_attempt = daily_attempt.quiz_attempt
    stats = attempt_stats(daily_attempt, total_questions=total_questions)
    return {
        'rank': rank,
        'user_id': daily_attempt.user_id,
        'display_name': display_name(daily_attempt.user),
        'score': _decimal_as_number(quiz_attempt.score),
        'correct': stats['correct'],
        'wrong': stats['wrong'],
        'unanswered': stats['unanswered'],
        'accuracy': stats['accuracy'],
        'time_taken_seconds': daily_attempt.time_taken_seconds,
        'submitted_at': daily_attempt.submitted_at.isoformat()
        if daily_attempt.submitted_at else None,
        'is_current_user': daily_attempt.user_id == current_user_id,
    }


def leaderboard_payload(exam, current_user_id=None, limit=50):
    """Top ranked entries plus the caller's own rank, even outside the page."""
    queryset = _ranked_attempts(exam)
    total_questions = exam.quiz.questions.count()
    participant_count = queryset.count()

    top_attempts = list(queryset[:limit])
    entries = [
        _entry_payload(attempt, index, current_user_id, total_questions)
        for index, attempt in enumerate(top_attempts, start=1)
    ]

    current_entry = None
    if current_user_id is not None:
        current = queryset.filter(user_id=current_user_id).first()
        if current is not None:
            current_entry = next(
                (entry for entry in entries if entry['user_id'] == current_user_id),
                None,
            )
            if current_entry is None:
                current_score = current.quiz_attempt.score
                ahead = queryset.filter(
                    Q(quiz_attempt__score__gt=current_score)
                    | (
                        Q(quiz_attempt__score=current_score)
                        & Q(time_taken_seconds__lt=current.time_taken_seconds)
                    )
                    | (
                        Q(quiz_attempt__score=current_score)
                        & Q(time_taken_seconds=current.time_taken_seconds)
                        & Q(submitted_at__lt=current.submitted_at)
                    )
                    | (
                        Q(quiz_attempt__score=current_score)
                        & Q(time_taken_seconds=current.time_taken_seconds)
                        & Q(submitted_at=current.submitted_at)
                        & Q(user_id__lt=current.user_id)
                    )
                ).count()
                current_entry = _entry_payload(
                    current,
                    ahead + 1,
                    current_user_id,
                    total_questions,
                )

    return {
        'exam_id': exam.pk,
        'status': exam.status,
        'participant_count': participant_count,
        'leaderboard': entries,
        'current_user_entry': current_entry,
    }


def rank_for_attempt(daily_attempt):
    """The attempt's current rank, or None when it is not ranked/submitted."""
    if (
        daily_attempt.user_id is None
        or not daily_attempt.is_ranked
        or daily_attempt.submitted_at is None
        or not daily_attempt.quiz_attempt.is_completed
    ):
        return None

    exam = daily_attempt.exam
    queryset = _ranked_attempts(exam)
    current_score = daily_attempt.quiz_attempt.score
    ahead = queryset.filter(
        Q(quiz_attempt__score__gt=current_score)
        | (
            Q(quiz_attempt__score=current_score)
            & Q(time_taken_seconds__lt=daily_attempt.time_taken_seconds)
        )
        | (
            Q(quiz_attempt__score=current_score)
            & Q(time_taken_seconds=daily_attempt.time_taken_seconds)
            & Q(submitted_at__lt=daily_attempt.submitted_at)
        )
        | (
            Q(quiz_attempt__score=current_score)
            & Q(time_taken_seconds=daily_attempt.time_taken_seconds)
            & Q(submitted_at=daily_attempt.submitted_at)
            & Q(user_id__lt=daily_attempt.user_id)
        )
    ).count()
    return ahead + 1


def next_exam_opens_at(now=None):
    """The next 9 PM opening, used by the empty-state UI."""
    now = now or timezone.now()
    today = local_date(now)
    opens_at, closes_at = exam_window_for_date(today)
    if now < closes_at:
        return opens_at
    return opens_at + timedelta(days=1)


def _decimal_as_number(value):
    if value is None:
        return None
    value = Decimal(value)
    if value == value.to_integral_value():
        return int(value)
    return float(value)
