"""Automation runners invoked by ``manage.py run_notification_automations``.

Each runner is independently gated by its :class:`NotificationAutomation` row
(``is_enabled`` + the shared Asia/Dhaka daytime window) and deduped through
:func:`~notifications.services.claim_delivery`, so cron can safely run this
every hour without risk of duplicate sends.

The server clock is UTC, but the audience is in Bangladesh, so every gate in
this module compares against local (Asia/Dhaka) time via :mod:`.timezones`
rather than the server's own timezone.
"""
import logging

from django.utils import timezone

from api.models import Question

from .models import (
    DailyMcqDelivery,
    DailyMcqSubscription,
    DeviceInstallation,
    NotificationAutomation,
    NotificationCampaign,
    NotificationKind,
)
from .services import (
    dispatch_campaign,
    find_inactive_owner_ids,
    find_recent_courses,
    claim_delivery,
    send_direct,
)
from .timezones import DHAKA_TZ

logger = logging.getLogger(__name__)

DEFAULT_TITLES = {
    NotificationKind.INACTIVITY_REMINDER: 'আপনি কি জানেন?',
    NotificationKind.NEW_COURSE_LAUNCH: 'New course available',
    NotificationKind.DAILY_MCQ: 'দৈনিক MCQ',
}
DEFAULT_MESSAGES = {
    NotificationKind.INACTIVITY_REMINDER:
        "You haven't practiced in a while. Keep your streak alive!",
    NotificationKind.NEW_COURSE_LAUNCH: '{course_name} is now open for enrollment.',
    NotificationKind.DAILY_MCQ: '',
}

# Notification bodies must stay well under the FCM 4096-byte payload cap.
EXPLANATION_BODY_LIMIT = 240
MCQ_BODY_LIMIT = 480

# Must match `Routes.DAILY_MCQ_QUESTION` in lib/app/routes/app_routes.dart
DAILY_MCQ_ROUTE = '/daily-mcq-question'


def pick_random_explanation():
    """A random question explanation, or None when none are available.

    Only questions with a non-empty explanation qualify, since the whole point
    of the "আপনি কি জানেন?" reminder is to teach something in the notification
    itself rather than just tease a question.
    """
    question = (
        Question.objects
        .exclude(explanation__isnull=True)
        .exclude(explanation__exact='')
        .order_by('?')
        .only('id', 'explanation')
        .first()
    )
    if question is None:
        return None
    return question, _truncate(question.explanation, EXPLANATION_BODY_LIMIT)


def pick_random_mcq(exclude_ids=()):
    """A random question with its answer options rendered as notification text."""
    question = (
        Question.objects
        .exclude(id__in=list(exclude_ids))
        .exclude(question_text__exact='')
        .order_by('?')
        .prefetch_related('answers')
        .first()
    )
    if question is None:
        return None

    lines = [' '.join(question.question_text.split())]
    labels = ['ক', 'খ', 'গ', 'ঘ', 'ঙ', 'চ']
    for index, answer in enumerate(question.answers.all()):
        label = labels[index] if index < len(labels) else str(index + 1)
        lines.append(f'{label}) {" ".join(answer.text.split())}')

    return question, _truncate('\n'.join(lines), MCQ_BODY_LIMIT)


def get_automation(kind):
    """Fetch (creating if absent) the automation config row for ``kind``."""
    automation, _created = NotificationAutomation.objects.get_or_create(kind=kind)
    return automation


def local_hour(automation, now):
    """The current hour in Asia/Dhaka, for comparing against the daytime window."""
    now = now or timezone.now()
    return now.astimezone(DHAKA_TZ).hour


def in_daytime_window(automation, now=None):
    """True while the local (Asia/Dhaka) clock sits inside the daytime window.

    The window is ``[daytime_start_hour, daytime_end_hour)``, e.g. 8 (8 AM)
    through 22 (10 PM) by default, so nothing sends overnight.
    """
    hour = local_hour(automation, now)
    return automation.daytime_start_hour <= hour < automation.daytime_end_hour


def should_run(automation, now=None, force=False):
    """Gate an automation on enabled state, the daytime window, and once-per-day."""
    if not automation.is_enabled:
        return False, 'disabled'
    if force:
        return True, 'forced'
    now = now or timezone.now()
    if not in_daytime_window(automation, now):
        return False, 'outside daytime window'
    if automation.already_ran_today(now):
        return False, 'already ran today'
    return True, 'ready'


def mark_ran(automation, now=None):
    automation.last_run_at = now or timezone.now()
    automation.save(update_fields=['last_run_at', 'updated_at'])


def _truncate(text, limit=160):
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else f'{text[:limit - 1]}…'


# ── 1. Daily inactivity reminder ─────────────────────────────────────────────

def run_inactivity_reminder(now=None, force=False, dry_run=False):
    """Nudge learners with no recent activity, optionally with a real question."""
    automation = get_automation(NotificationKind.INACTIVITY_REMINDER)
    ok, reason = should_run(automation, now=now, force=force)
    if not ok:
        return {'kind': automation.kind, 'skipped': reason, 'sent': 0}

    now = now or timezone.now()
    today = now.astimezone(DHAKA_TZ).date().isoformat()
    user_ids, guest_ids = find_inactive_owner_ids(
        inactivity_days=automation.inactivity_days,
        now=now,
    )

    title = automation.title_template or DEFAULT_TITLES[automation.kind]
    base_body = automation.message_template or DEFAULT_MESSAGES[automation.kind]

    # Prefer teaching something concrete: a real explanation from the bank,
    # shown under the "আপনি কি জানেন?" title. Fall back to the plain nudge
    # when no question has an explanation yet.
    body = base_body
    payload = {}
    if automation.include_question:
        picked = pick_random_explanation()
        if picked is not None:
            question, explanation = picked
            body = explanation
            payload = {'question_id': question.id}

    sent = 0
    skipped = 0
    for owner_field, owner_ids in (('user_id', user_ids), ('guest_user_id', guest_ids)):
        for owner_id in owner_ids:
            prefix = 'user' if owner_field == 'user_id' else 'guest'
            dedupe_key = f'{prefix}:{owner_id}:{today}'
            if dry_run:
                sent += 1
                continue
            if not claim_delivery(automation.kind, dedupe_key):
                skipped += 1
                continue
            installations = DeviceInstallation.objects.filter(
                is_active=True, **{owner_field: owner_id},
            )
            success, _failures = send_direct(
                installations,
                title=title,
                body=body,
                kind=automation.kind,
                route='/home',
                payload=payload,
            )
            sent += 1 if success else 0

    if not dry_run:
        mark_ran(automation, now)
    return {
        'kind': automation.kind,
        'sent': sent,
        'deduped': skipped,
        'candidates': len(user_ids) + len(guest_ids),
    }


# ── 2. New course launch ─────────────────────────────────────────────────────

def run_new_course_launch(now=None, force=False, dry_run=False):
    """Announce newly published courses to everyone, once per course."""
    automation = get_automation(NotificationKind.NEW_COURSE_LAUNCH)
    ok, reason = should_run(automation, now=now, force=force)
    if not ok:
        return {'kind': automation.kind, 'skipped': reason, 'sent': 0}

    now = now or timezone.now()
    courses = find_recent_courses(
        lookback_hours=automation.new_course_lookback_hours,
        now=now,
    )

    title = automation.title_template or DEFAULT_TITLES[automation.kind]
    message_template = automation.message_template or DEFAULT_MESSAGES[automation.kind]

    announced = 0
    for course in courses:
        dedupe_key = f'course:{course.id}'
        if dry_run:
            announced += 1
            continue
        if not claim_delivery(automation.kind, dedupe_key):
            continue

        try:
            body = message_template.format(course_name=course.name)
        except (KeyError, IndexError):
            body = message_template

        campaign = NotificationCampaign.objects.create(
            title=title,
            message=body,
            kind=automation.kind,
            audience=NotificationCampaign.Audience.ALL,
            course=course,
            status=NotificationCampaign.Status.DRAFT,
        )
        dispatch_campaign(campaign)
        announced += 1

    if not dry_run:
        mark_ran(automation, now)
    return {'kind': automation.kind, 'sent': announced}


# ── 3. Daily MCQ subscription ────────────────────────────────────────────────

def daily_mcq_slot(automation, subscription, now):
    """Which delivery slot (0-indexed) the current local hour maps to for one
    subscriber, or None if there is nothing new to send this hour.

    Spacing is proportional to how many questions the subscriber asked for,
    spread evenly across the whole daytime window rather than fixed at one
    per hour. A 14-hour window (8 AM - 10 PM) with 7 questions/day delivers
    one every 2 hours; the same window with 3 questions/day delivers one
    roughly every 4-5 hours.
    """
    window_hours = automation.daytime_end_hour - automation.daytime_start_hour
    if window_hours <= 0:
        return None

    questions_per_day = subscription.questions_per_day
    if questions_per_day <= 0:
        return None

    hour = local_hour(automation, now)
    if not (automation.daytime_start_hour <= hour < automation.daytime_end_hour):
        return None

    elapsed_hours = hour - automation.daytime_start_hour
    spacing_hours = window_hours / questions_per_day
    slot = int(elapsed_hours // spacing_hours)
    if slot >= questions_per_day:
        return None
    return slot


def run_daily_mcq(now=None, force=False, dry_run=False):
    """Deliver each active subscriber's MCQs, spread evenly across the local
    (Asia/Dhaka) daytime window.

    Unlike the other automations this intentionally runs many times per day
    (hourly cron), so it does not use the once-per-day gate. Each subscriber's
    slot spacing is personal to their ``questions_per_day`` (see
    :func:`daily_mcq_slot`); the dedupe ledger keyed on (owner, local date,
    slot) prevents duplicates when cron overlaps a slot's duration.
    """
    automation = get_automation(NotificationKind.DAILY_MCQ)
    if not automation.is_enabled:
        return {'kind': automation.kind, 'skipped': 'disabled', 'sent': 0}

    now = now or timezone.now()
    window_hours = automation.daytime_end_hour - automation.daytime_start_hour
    if window_hours <= 0:
        return {
            'kind': automation.kind,
            'skipped': 'invalid daytime window',
            'sent': 0,
        }

    if not force and not in_daytime_window(automation, now):
        return {'kind': automation.kind, 'skipped': 'outside daytime window', 'sent': 0}

    today = now.astimezone(DHAKA_TZ).date().isoformat()
    title = automation.title_template or DEFAULT_TITLES[automation.kind]

    subscriptions = DailyMcqSubscription.objects.filter(
        is_active=True,
    ).select_related('user', 'guest_user')

    sent = 0
    deduped = 0
    considered = 0
    for subscription in subscriptions:
        slot = daily_mcq_slot(automation, subscription, now)
        if slot is None:
            if not force:
                continue
            # Forced runs (admin testing) always exercise slot 0.
            slot = 0
        considered += 1

        if subscription.user_id:
            owner_field, owner_id, prefix = 'user_id', subscription.user_id, 'user'
        else:
            owner_field, owner_id, prefix = (
                'guest_user_id', subscription.guest_user_id, 'guest',
            )

        dedupe_key = f'{prefix}:{owner_id}:{today}:{slot}'
        if dry_run:
            sent += 1
            continue
        if not claim_delivery(automation.kind, dedupe_key):
            deduped += 1
            continue

        # Avoid repeating a question this subscriber already received.
        already_seen = DailyMcqDelivery.objects.filter(
            **{owner_field: owner_id},
        ).values_list('question_id', flat=True)
        picked = pick_random_mcq(exclude_ids=already_seen)
        if picked is None:
            # Every question has been seen; fall back to allowing repeats.
            picked = pick_random_mcq()
        if picked is None:
            continue
        question, body = picked

        delivery = DailyMcqDelivery.objects.create(
            question=question,
            delivered_date=now.astimezone(DHAKA_TZ).date(),
            slot=slot,
            **{owner_field: owner_id},
        )

        installations = DeviceInstallation.objects.filter(
            is_active=True, **{owner_field: owner_id},
        )
        success, _failures = send_direct(
            installations,
            title=f'{title} ({slot + 1}/{subscription.questions_per_day})',
            body=body,
            kind=automation.kind,
            route=DAILY_MCQ_ROUTE,
            payload={
                'question_id': question.id,
                'mcq_delivery_id': delivery.id,
            },
        )
        sent += 1 if success else 0

    return {
        'kind': automation.kind,
        'sent': sent,
        'deduped': deduped,
        'considered': considered,
        'subscribers': subscriptions.count(),
    }


# ── 4. Scheduled campaigns (admin direct + course announcements) ─────────────

def run_due_scheduled_campaigns(now=None, dry_run=False):
    """Send any campaign whose scheduled time has arrived.

    Covers both admin-authored scheduled broadcasts and course announcements,
    since both are just campaigns with a ``scheduled_at`` in the past. Gated by
    the SCHEDULED_BROADCAST automation toggle.
    """
    automation = get_automation(NotificationKind.SCHEDULED_BROADCAST)
    if not automation.is_enabled:
        return {'kind': automation.kind, 'skipped': 'disabled', 'sent': 0}

    now = now or timezone.now()
    due = NotificationCampaign.objects.filter(
        status=NotificationCampaign.Status.SCHEDULED,
        scheduled_at__lte=now,
    )

    sent = 0
    failed = 0
    for campaign in due:
        if dry_run:
            sent += 1
            continue
        result = dispatch_campaign(campaign)
        if result.status == NotificationCampaign.Status.SENT:
            sent += 1
        else:
            failed += 1

    if not dry_run:
        mark_ran(automation, now)
    return {'kind': automation.kind, 'sent': sent, 'failed': failed}


# ── Entrypoint ───────────────────────────────────────────────────────────────

RUNNERS = {
    NotificationKind.INACTIVITY_REMINDER: run_inactivity_reminder,
    NotificationKind.DAILY_MCQ: run_daily_mcq,
    NotificationKind.NEW_COURSE_LAUNCH: run_new_course_launch,
    NotificationKind.SCHEDULED_BROADCAST: run_due_scheduled_campaigns,
}


def run_all(now=None, force=False, dry_run=False, only=None):
    """Run every enabled automation, returning a per-automation result list."""
    results = []
    for kind, runner in RUNNERS.items():
        if only and kind != only:
            continue
        try:
            if runner is run_due_scheduled_campaigns:
                results.append(runner(now=now, dry_run=dry_run))
            else:
                results.append(runner(now=now, force=force, dry_run=dry_run))
        except Exception as error:  # noqa: BLE001 - one automation must not kill the rest
            logger.exception('Automation %s crashed', kind)
            results.append({'kind': kind, 'error': str(error), 'sent': 0})
    return results
