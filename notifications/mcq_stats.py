"""Daily MCQ performance stats and monthly leaderboard.

Kept separate from ``services.py`` (which is about dispatch) because these are
read-only aggregations over :class:`DailyMcqDelivery`.
"""
from datetime import date

from django.db.models import Count, Q

from .models import DailyMcqDelivery

LEADERBOARD_SIZE = 20


def month_bounds(reference=None):
    """First and last day of the month containing ``reference``."""
    reference = reference or date.today()
    first = reference.replace(day=1)
    if first.month == 12:
        next_first = first.replace(year=first.year + 1, month=1)
    else:
        next_first = first.replace(month=first.month + 1)
    return first, next_first


def personal_stats(owner_filter, reference=None):
    """Correct/incorrect/unanswered counts for one owner, month and all-time."""
    first, next_first = month_bounds(reference)
    base = DailyMcqDelivery.objects.filter(**owner_filter)

    def summarize(queryset):
        aggregate = queryset.aggregate(
            total=Count('id'),
            correct=Count('id', filter=Q(is_correct=True)),
            incorrect=Count('id', filter=Q(is_correct=False)),
            unanswered=Count('id', filter=Q(answered_at__isnull=True)),
        )
        answered = aggregate['correct'] + aggregate['incorrect']
        aggregate['answered'] = answered
        aggregate['accuracy'] = (
            round(aggregate['correct'] * 100 / answered, 2) if answered else 0.0
        )
        return aggregate

    return {
        'month': summarize(
            base.filter(delivered_date__gte=first, delivered_date__lt=next_first)
        ),
        'all_time': summarize(base),
    }


def monthly_leaderboard(reference=None, current_user_id=None, limit=LEADERBOARD_SIZE):
    """Top registered users by correct answers this month, plus the caller's rank.

    Guests are excluded from the ranking because they have no stable display
    identity; they still see their own stats via :func:`personal_stats`.
    """
    first, next_first = month_bounds(reference)

    rows = (
        DailyMcqDelivery.objects
        .filter(
            user__isnull=False,
            delivered_date__gte=first,
            delivered_date__lt=next_first,
        )
        .values('user_id', 'user__first_name', 'user__last_name', 'user__username')
        .annotate(
            correct=Count('id', filter=Q(is_correct=True)),
            incorrect=Count('id', filter=Q(is_correct=False)),
        )
        .filter(correct__gt=0)
        .order_by('-correct', 'incorrect', 'user_id')
    )

    entries = []
    for rank, row in enumerate(rows, start=1):
        display_name = ' '.join(
            part for part in [
                (row['user__first_name'] or '').strip(),
                (row['user__last_name'] or '').strip(),
            ] if part
        ) or row['user__username']

        entries.append({
            'rank': rank,
            'user_id': row['user_id'],
            'display_name': display_name,
            'correct': row['correct'],
            'incorrect': row['incorrect'],
            'is_current_user': row['user_id'] == current_user_id,
        })

    current_entry = next(
        (entry for entry in entries if entry['is_current_user']),
        None,
    )

    return {
        'month': first.isoformat(),
        'participant_count': len(entries),
        'leaderboard': entries[:limit],
        'current_user_entry': current_entry,
    }
