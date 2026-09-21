"""Notification dispatch and audience-resolution services.

Every send funnels through :func:`dispatch_campaign` so that push delivery,
in-app :class:`Notification` records, and dead-token cleanup stay consistent
regardless of whether the send came from the admin UI or from cron.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from api.models import GuestUser, Streak, UserActivity
from courses.models import Course, CourseEnrollment

from .firebase import (
    FirebaseNotConfigured,
    send_token_notifications,
    send_topic_notification,
)
from .models import (
    DeviceInstallation,
    Notification,
    NotificationAutomation,
    NotificationCampaign,
    NotificationDelivery,
    NotificationKind,
)

logger = logging.getLogger(__name__)

# Must match `notificationTopic` in lib/app/services/notification_service.dart
BROADCAST_TOPIC = 'all_devices'


# ── Audience resolution ──────────────────────────────────────────────────────

def resolve_campaign_targets(campaign):
    """Return the active DeviceInstallation queryset for a campaign's audience."""
    installations = DeviceInstallation.objects.filter(is_active=True)

    if campaign.audience == NotificationCampaign.Audience.COURSE:
        if campaign.course_id is None:
            return installations.none()
        enrolled_user_ids = CourseEnrollment.objects.filter(
            course_id=campaign.course_id,
            is_active=True,
        ).values_list('user_id', flat=True)
        return installations.filter(user_id__in=enrolled_user_ids)

    if campaign.audience == NotificationCampaign.Audience.INACTIVE:
        user_ids, guest_ids = find_inactive_owner_ids()
        return installations.filter(user_id__in=user_ids) | \
            installations.filter(guest_user_id__in=guest_ids)

    return installations


def find_inactive_owner_ids(inactivity_days=2, now=None):
    """Return ``(user_ids, guest_user_ids)`` with no recorded activity recently.

    Registered users are judged by their most recent :class:`UserActivity` or
    :class:`Streak` entry. Guests fall back to ``GuestUser.last_active``, which
    is coarser but the only signal available for them.
    """
    now = now or timezone.now()
    cutoff_date = (now - timedelta(days=inactivity_days)).date()

    active_user_ids = set(
        UserActivity.objects.filter(
            activity_date__gte=cutoff_date,
            user__isnull=False,
        ).values_list('user_id', flat=True)
    )
    active_user_ids.update(
        Streak.objects.filter(
            last_activity_date__gte=cutoff_date,
            user__isnull=False,
        ).values_list('user_id', flat=True)
    )

    active_guest_ids = set(
        UserActivity.objects.filter(
            activity_date__gte=cutoff_date,
            guest_user__isnull=False,
        ).values_list('guest_user_id', flat=True)
    )
    active_guest_ids.update(
        GuestUser.objects.filter(
            last_active__gte=now - timedelta(days=inactivity_days),
        ).values_list('id', flat=True)
    )

    # Only consider owners that actually have a device to notify.
    candidate_users = set(
        DeviceInstallation.objects.filter(
            is_active=True,
            user__isnull=False,
        ).values_list('user_id', flat=True)
    )
    candidate_guests = set(
        DeviceInstallation.objects.filter(
            is_active=True,
            guest_user__isnull=False,
        ).values_list('guest_user_id', flat=True)
    )

    return candidate_users - active_user_ids, candidate_guests - active_guest_ids


# ── In-app records ───────────────────────────────────────────────────────────

def create_in_app_notifications(installations, title, body, kind, route='',
                                payload=None, campaign=None):
    """Write one in-app Notification per distinct owner behind ``installations``."""
    payload = payload or {}
    seen_users = set()
    seen_guests = set()
    records = []

    for installation in installations:
        if installation.user_id and installation.user_id not in seen_users:
            seen_users.add(installation.user_id)
            records.append(Notification(
                user_id=installation.user_id,
                kind=kind,
                title=title,
                body=body,
                route=route,
                payload=payload,
                campaign=campaign,
            ))
        elif installation.guest_user_id and installation.guest_user_id not in seen_guests:
            seen_guests.add(installation.guest_user_id)
            records.append(Notification(
                guest_user_id=installation.guest_user_id,
                kind=kind,
                title=title,
                body=body,
                route=route,
                payload=payload,
                campaign=campaign,
            ))

    if records:
        Notification.objects.bulk_create(records, batch_size=500)
    return len(records)


def deactivate_invalid_tokens(invalid_tokens):
    """Mark tokens Firebase rejected as inactive so future sends skip them."""
    if not invalid_tokens:
        return 0
    return DeviceInstallation.objects.filter(
        token__in=invalid_tokens,
        is_active=True,
    ).update(is_active=False)


# ── Dispatch ─────────────────────────────────────────────────────────────────

def is_kind_enabled(kind):
    """Whether an admin has left this notification category switched on.

    Manual broadcasts are always allowed (the admin is explicitly confirming
    them). Every other category is gated by its NotificationAutomation row so
    a single switch can silence a whole notification type.
    """
    if kind == NotificationKind.MANUAL:
        return True
    automation = NotificationAutomation.objects.filter(kind=kind).first()
    return bool(automation and automation.is_enabled)


def dispatch_campaign(campaign, use_topic_for_all=True):
    """Send a campaign and record the outcome on the campaign row.

    An "Everyone" campaign uses a single FCM topic message (cheap, instant
    fan-out on Google's side). Targeted campaigns resolve device tokens and
    multicast in batches. In-app records are written either way.

    Returns the refreshed campaign. Never raises for Firebase failures; the
    error is recorded on the campaign so callers can surface it.
    """
    if not is_kind_enabled(campaign.kind):
        return _record_failure(
            campaign,
            f'The "{campaign.get_kind_display()}" notification type is '
            'switched off in Notification Automations.',
        )

    campaign.status = NotificationCampaign.Status.SENDING
    campaign.error_message = ''
    campaign.save(update_fields=['status', 'error_message', 'updated_at'])

    data = {'kind': campaign.kind}
    route = ''
    payload = {}
    if campaign.course_id:
        data['course_id'] = campaign.course_id
        payload['course_id'] = campaign.course_id
        route = '/course-detail'

    is_broadcast = (
        use_topic_for_all
        and campaign.audience == NotificationCampaign.Audience.ALL
    )

    try:
        if is_broadcast:
            message_id = send_topic_notification(
                topic=BROADCAST_TOPIC,
                title=campaign.title,
                body=campaign.message,
                data=data,
            )
            installations = list(
                DeviceInstallation.objects.filter(is_active=True).only(
                    'user_id', 'guest_user_id',
                )
            )
            recipient_count = create_in_app_notifications(
                installations,
                title=campaign.title,
                body=campaign.message,
                kind=campaign.kind,
                route=route,
                payload=payload,
                campaign=campaign,
            )
            failure_count = 0
        else:
            installations = list(
                resolve_campaign_targets(campaign).only(
                    'token', 'user_id', 'guest_user_id',
                )
            )
            tokens = [installation.token for installation in installations]
            message_id = ''
            success_count, failure_count, invalid_tokens = send_token_notifications(
                tokens,
                title=campaign.title,
                body=campaign.message,
                data=data,
            )
            deactivate_invalid_tokens(invalid_tokens)
            create_in_app_notifications(
                installations,
                title=campaign.title,
                body=campaign.message,
                kind=campaign.kind,
                route=route,
                payload=payload,
                campaign=campaign,
            )
            recipient_count = success_count
    except FirebaseNotConfigured as error:
        return _record_failure(campaign, str(error))
    except Exception as error:  # noqa: BLE001 - recorded on the campaign, not swallowed
        logger.exception('Campaign %s failed to send', campaign.pk)
        return _record_failure(campaign, str(error))

    campaign.status = NotificationCampaign.Status.SENT
    campaign.sent_at = timezone.now()
    campaign.firebase_message_id = message_id or ''
    campaign.recipient_count = recipient_count
    campaign.failure_count = failure_count
    campaign.save(update_fields=[
        'status', 'sent_at', 'firebase_message_id', 'recipient_count',
        'failure_count', 'updated_at',
    ])
    return campaign


def _record_failure(campaign, message):
    campaign.status = NotificationCampaign.Status.FAILED
    campaign.error_message = message
    campaign.save(update_fields=['status', 'error_message', 'updated_at'])
    return campaign


def send_direct(installations, title, body, kind, route='', payload=None,
                data=None, campaign=None):
    """Push to specific installations without creating a campaign row.

    Used by per-recipient automations (e.g. inactivity reminders) where each
    recipient may receive a different body.
    """
    installation_list = list(installations)
    tokens = [installation.token for installation in installation_list]
    if not tokens:
        return 0, 0

    merged_data = {'kind': kind}
    merged_data.update(data or {})

    success_count, failure_count, invalid_tokens = send_token_notifications(
        tokens, title=title, body=body, data=merged_data,
    )
    deactivate_invalid_tokens(invalid_tokens)
    create_in_app_notifications(
        installation_list,
        title=title,
        body=body,
        kind=kind,
        route=route,
        payload=payload,
        campaign=campaign,
    )
    return success_count, failure_count


def claim_delivery(kind, dedupe_key):
    """Reserve a (kind, dedupe_key) slot. False when already delivered.

    Makes the cron entrypoint idempotent: running it twice in a day will not
    double-notify, even if the first run crashed partway through.
    """
    try:
        with transaction.atomic():
            NotificationDelivery.objects.create(kind=kind, dedupe_key=dedupe_key)
        return True
    except Exception:  # IntegrityError on the unique constraint
        return False


def find_recent_courses(lookback_hours=24, now=None):
    """Published courses created within the lookback window."""
    now = now or timezone.now()
    return Course.objects.filter(
        is_published=True,
        created_at__gte=now - timedelta(hours=lookback_hours),
    )
