"""Response builders for the Daily Exam endpoints."""

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from .daily_exam_services import (
    attempt_deadline,
    attempt_is_expired,
    attempt_stats,
    attempt_status,
    daily_exam_duration,
    get_owner_attempt,
    local_date,
    rank_for_attempt,
)
from .models import DailyExam
from .serializers import decimal_as_number


class DailyExamCategorySerializer(serializers.ModelSerializer):
    full_path = serializers.SerializerMethodField()

    class Meta:
        from .models import Category
        model = Category
        fields = ['id', 'name', 'level', 'full_path']

    def get_full_path(self, obj):
        return obj.get_full_path()


def _iso(value):
    return value.isoformat() if value else None


def build_exam_payload(exam, now=None):
    now = now or timezone.now()
    status = exam.status_at(now)
    opens_at = exam.opens_at
    closes_at = exam.closes_at
    question_count = exam.quiz.questions.count()

    return {
        'id': exam.pk,
        'exam_date': exam.exam_date.isoformat(),
        'category': DailyExamCategorySerializer(exam.category).data,
        'opens_at': _iso(opens_at),
        'closes_at': _iso(closes_at),
        'status': status,
        'question_count': question_count,
        'total_marks': exam.total_marks,
        'duration_minutes': exam.quiz.duration_minutes,
        'correct_mark': decimal_as_number(exam.quiz.correct_mark),
        'wrong_mark': decimal_as_number(exam.quiz.wrong_mark),
        'unanswered_mark': decimal_as_number(exam.quiz.unanswered_mark),
        'seconds_until_open': max(int((opens_at - now).total_seconds()), 0),
        'seconds_until_close': max(int((closes_at - now).total_seconds()), 0),
        'server_time': _iso(now),
    }


def build_attempt_payload(daily_attempt, now=None, rank=None):
    now = now or timezone.now()
    status = attempt_status(daily_attempt, now=now)
    stats = attempt_stats(daily_attempt)
    quiz_attempt = daily_attempt.quiz_attempt
    time_taken = daily_attempt.time_taken_seconds
    if not time_taken and quiz_attempt.end_time and quiz_attempt.start_time:
        time_taken = max(
            int((quiz_attempt.end_time - quiz_attempt.start_time).total_seconds()),
            0,
        )

    if rank is None and status == 'SUBMITTED':
        rank = rank_for_attempt(daily_attempt)

    return {
        'id': daily_attempt.pk,
        'quiz_attempt_id': quiz_attempt.pk,
        'status': status,
        'is_ranked': bool(daily_attempt.is_ranked),
        'score': decimal_as_number(quiz_attempt.score),
        'correct_answers': stats['correct'],
        'wrong_answers': stats['wrong'],
        'unanswered': stats['unanswered'],
        'accuracy': stats['accuracy'],
        'rank': rank,
        'started_at': _iso(quiz_attempt.start_time),
        'submitted_at': _iso(daily_attempt.submitted_at),
        'expires_at': _iso(attempt_deadline(daily_attempt)),
        'time_taken_seconds': time_taken,
        'can_resume': status == 'IN_PROGRESS' and not attempt_is_expired(
            daily_attempt,
            now=now,
        ),
    }


def build_status_payload(exam, user=None, guest_user=None, now=None):
    now = now or timezone.now()
    attempt = get_owner_attempt(exam, user=user, guest_user=guest_user)
    exam_payload = build_exam_payload(exam, now=now)
    attempt_payload = (
        build_attempt_payload(attempt, now=now) if attempt is not None else None
    )

    status = exam.status_at(now)
    can_start = (
        status == DailyExam.Status.LIVE
        and attempt is None
        and now < exam.closes_at
        and exam_payload['question_count'] == settings.DAILY_EXAM_QUESTION_COUNT
    )
    full_duration_available = now + daily_exam_duration() <= exam.closes_at
    ranking_available = user is not None
    late_start_unranked = (
        status == DailyExam.Status.LIVE
        and attempt is None
        and full_duration_available is False
        and user is not None
    )

    participant_count = 0
    if status != DailyExam.Status.UPCOMING:
        participant_count = exam.attempts.filter(
            user__isnull=False,
            is_ranked=True,
            submitted_at__isnull=False,
            quiz_attempt__is_completed=True,
        ).count()

    return {
        'available': True,
        'server_time': _iso(now),
        'exam': exam_payload,
        'attempt': attempt_payload,
        'can_start': can_start,
        'can_resume': bool(attempt_payload and attempt_payload['can_resume']),
        'has_attempted': attempt is not None,
        'is_ranked': ranking_available,
        'late_start_unranked': late_start_unranked,
        'participant_count': participant_count,
        'answer_key_available': now >= exam.closes_at,
    }


def build_history_item(exam, daily_attempt=None, now=None, rank=None,
                       participant_count=None):
    now = now or timezone.now()
    if daily_attempt is not None and rank is None and daily_attempt.submitted_at:
        rank = rank_for_attempt(daily_attempt)
    if participant_count is None:
        participant_count = exam.attempts.filter(
            user__isnull=False,
            is_ranked=True,
            submitted_at__isnull=False,
            quiz_attempt__is_completed=True,
        ).count()
    return {
        'exam': build_exam_payload(exam, now=now),
        'attempt': (
            build_attempt_payload(daily_attempt, now=now, rank=rank)
            if daily_attempt is not None else None
        ),
        'participant_count': participant_count,
        'answer_key_available': now >= exam.closes_at,
    }


def build_unavailable_payload(now=None, next_open_at=None):
    now = now or timezone.now()
    return {
        'available': False,
        'reason': 'no_eligible_category',
        'message': (
            'Today\'s daily exam could not be generated because no '
            'subcategory has enough valid questions.'
        ),
        'server_time': _iso(now),
        'exam_date': local_date(now).isoformat(),
        'next_opens_at': _iso(next_open_at),
    }
