from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from api.models import GuestUser


class DeviceInstallation(models.Model):
    """A single FCM-registered device/app installation.

    Owned by either a registered ``User`` or a ``GuestUser``, mirroring the
    ownership pattern already used across the ``api`` app models. Tokens are
    never logged in full; only a short prefix is ever surfaced for support
    /debugging purposes (see ``token_preview``).
    """

    class Platform(models.TextChoices):
        ANDROID = 'android', 'Android'
        IOS = 'ios', 'iOS'
        WEB = 'web', 'Web'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='device_installations',
        null=True,
        blank=True,
    )
    guest_user = models.ForeignKey(
        GuestUser,
        on_delete=models.CASCADE,
        related_name='device_installations',
        null=True,
        blank=True,
    )
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(
        max_length=20,
        choices=Platform.choices,
        default=Platform.ANDROID,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Device Installation'
        verbose_name_plural = 'Device Installations'
        ordering = ['-updated_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='device_installation_must_have_user_or_guest',
            ),
            models.CheckConstraint(
                condition=~(
                    models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)
                ),
                name='device_installation_cannot_have_both_user_and_guest',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active'], name='installation_user_active_idx'),
            models.Index(fields=['guest_user', 'is_active'], name='installation_guest_active_idx'),
        ]

    def __str__(self):
        owner = self.user.username if self.user else f'Guest {self.guest_user.guest_id}'
        return f'{owner} ({self.platform}) - {self.token_preview}'

    @property
    def token_preview(self):
        """Short, non-sensitive token fragment safe to display in admin/logs."""
        if not self.token:
            return ''
        return f'{self.token[:8]}…{self.token[-4:]}' if len(self.token) > 12 else self.token


class NotificationKind(models.TextChoices):
    """Shared taxonomy for automations, campaigns and in-app records."""

    MANUAL = 'manual', 'Manual broadcast'
    INACTIVITY_REMINDER = 'inactivity_reminder', 'Daily inactivity reminder'
    COURSE_ANNOUNCEMENT = 'course_announcement', 'Enrolled course announcement'
    NEW_COURSE_LAUNCH = 'new_course_launch', 'New course launch'
    SCHEDULED_BROADCAST = 'scheduled_broadcast', 'Scheduled broadcast'
    DAILY_MCQ = 'daily_mcq', 'Daily MCQ subscription'
    GROUP_STUDY_ADDED = 'group_study_added', 'Added to a study group'
    GROUP_STUDY_PUBLISH = 'group_study_publish', 'New group study quiz published'
    GROUP_STUDY_REMINDER = 'group_study_reminder', 'Group study schedule reminder'
    GROUP_STUDY_MESSAGE = 'group_study_message', 'New group study message'


class NotificationAutomation(models.Model):
    """Admin on/off switch plus tuning knobs for one automated notification.

    One row per :class:`NotificationKind`. The cron entrypoint
    (``manage.py run_notification_automations``) reads these rows and skips
    any automation whose ``is_enabled`` is False, so an admin can disable a
    whole category without touching the server.
    """

    kind = models.CharField(
        max_length=32,
        choices=NotificationKind.choices,
        unique=True,
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text='Turn this automated notification on or off.',
    )
    title_template = models.CharField(
        max_length=120,
        blank=True,
        default='',
        help_text='Notification title. Leave blank to use the built-in default.',
    )
    message_template = models.TextField(
        blank=True,
        default='',
        help_text=(
            'Notification body. Leave blank to use the built-in default. '
            'Inactivity reminders may append a question when enabled below.'
        ),
    )
    inactivity_days = models.PositiveIntegerField(
        default=2,
        help_text='Inactivity reminder only: days without activity before reminding.',
    )
    include_question = models.BooleanField(
        default=True,
        help_text='Inactivity reminder only: append a random practice question.',
    )
    daytime_start_hour = models.PositiveIntegerField(
        default=8,
        validators=[MaxValueValidator(23)],
        help_text=(
            'Local hour (Asia/Dhaka, 0-23) notifications may start sending, e.g. 8 '
            'for 8 AM. Shared by every automation so nothing fires overnight.'
        ),
    )
    daytime_end_hour = models.PositiveIntegerField(
        default=22,
        validators=[MaxValueValidator(23)],
        help_text=(
            'Local hour (Asia/Dhaka, 0-23) after which sending stops, e.g. 22 for '
            "10 PM. Daily MCQ also spreads each subscriber's questions evenly "
            'across this window instead of sending one per hour.'
        ),
    )
    new_course_lookback_hours = models.PositiveIntegerField(
        default=24,
        help_text='New course launch only: how far back to look for new published courses.',
    )
    default_questions_per_day = models.PositiveIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text=(
            'Daily MCQ only: default number of questions for new subscribers. '
            'Each subscriber can change their own count (1-12).'
        ),
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Notification Automation'
        verbose_name_plural = 'Notification Automations'
        ordering = ['kind']

    def __str__(self):
        state = 'on' if self.is_enabled else 'off'
        return f'{self.get_kind_display()} ({state})'

    def already_ran_today(self, now=None):
        """True when this automation already completed a run in the current
        Asia/Dhaka calendar day.

        Local rather than UTC so the once-per-day gate lines up with the
        daytime window learners actually experience.
        """
        from .timezones import DHAKA_TZ

        now = now or timezone.now()
        if self.last_run_at is None:
            return False
        return self.last_run_at.astimezone(DHAKA_TZ).date() == \
            now.astimezone(DHAKA_TZ).date()


class NotificationCampaign(models.Model):
    """A notification send, either admin-authored or automation-generated.

    Manual campaigns targeting everyone are broadcast through a single FCM
    topic message. Targeted campaigns (a course's enrolled learners, inactive
    learners) fan out to individual device tokens in batches, which is safe
    because those sends run from cron rather than a web request.
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SCHEDULED = 'scheduled', 'Scheduled'
        SENDING = 'sending', 'Sending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    class Audience(models.TextChoices):
        ALL = 'all', 'Everyone'
        COURSE = 'course', 'Learners enrolled in a course'
        INACTIVE = 'inactive', 'Inactive learners'

    title = models.CharField(max_length=120)
    message = models.TextField()
    kind = models.CharField(
        max_length=32,
        choices=NotificationKind.choices,
        default=NotificationKind.MANUAL,
    )
    audience = models.CharField(
        max_length=20,
        choices=Audience.choices,
        default=Audience.ALL,
    )
    course = models.ForeignKey(
        'courses.Course',
        on_delete=models.SET_NULL,
        related_name='notification_campaigns',
        null=True,
        blank=True,
        help_text='Required when the audience is "Learners enrolled in a course".',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            'Set a future time and status "Scheduled" to have cron send this '
            'automatically. Leave blank to send manually.'
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='notification_campaigns',
        null=True,
        blank=True,
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    firebase_message_id = models.CharField(max_length=255, blank=True, default='')
    recipient_count = models.PositiveIntegerField(default=0)
    failure_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Notification Campaign'
        verbose_name_plural = 'Notification Campaigns'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'scheduled_at'], name='campaign_status_sched_idx'),
        ]

    def __str__(self):
        return f'{self.title} ({self.get_status_display()})'

    def clean(self):
        if self.audience == self.Audience.COURSE and self.course_id is None:
            raise ValidationError({
                'course': 'Select a course when targeting enrolled learners.',
            })
        if self.status == self.Status.SCHEDULED and self.scheduled_at is None:
            raise ValidationError({
                'scheduled_at': 'Set a send time for a scheduled campaign.',
            })


class Notification(models.Model):
    """In-app notification record shown in the app's notification screen.

    Written alongside every push so the user still sees the message if the
    push was missed, dismissed, or arrived while notifications were disabled.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
        null=True,
        blank=True,
    )
    guest_user = models.ForeignKey(
        GuestUser,
        on_delete=models.CASCADE,
        related_name='notifications',
        null=True,
        blank=True,
    )
    campaign = models.ForeignKey(
        NotificationCampaign,
        on_delete=models.SET_NULL,
        related_name='notifications',
        null=True,
        blank=True,
    )
    kind = models.CharField(
        max_length=32,
        choices=NotificationKind.choices,
        default=NotificationKind.MANUAL,
    )
    title = models.CharField(max_length=120)
    body = models.TextField()
    route = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text='Optional in-app route the notification should open when tapped.',
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text='Extra tap-target data, e.g. {"course_id": 12}.',
    )
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='notification_must_have_user_or_guest',
            ),
            models.CheckConstraint(
                condition=~(
                    models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)
                ),
                name='notification_cannot_have_both_user_and_guest',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'is_read', '-created_at'], name='notif_user_unread_idx'),
            models.Index(fields=['guest_user', 'is_read', '-created_at'], name='notif_guest_unread_idx'),
        ]

    def __str__(self):
        owner = self.user.username if self.user else f'Guest {self.guest_user.guest_id}'
        return f'{owner}: {self.title}'

    def mark_read(self):
        if self.is_read:
            return
        self.is_read = True
        self.read_at = timezone.now()
        self.save(update_fields=['is_read', 'read_at'])


class DailyMcqSubscription(models.Model):
    """Opt-in daily MCQ delivery, owned by a registered user or a guest.

    Questions are spread evenly across the Daily MCQ automation's local
    (Asia/Dhaka) daytime window (``daytime_start_hour``-``daytime_end_hour``),
    so a subscriber asking for 7 per day across a 14-hour window receives one
    roughly every 2 hours, rather than one per hour regardless of count.
    """

    MIN_QUESTIONS_PER_DAY = 1
    MAX_QUESTIONS_PER_DAY = 12

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='daily_mcq_subscription',
        null=True,
        blank=True,
    )
    guest_user = models.OneToOneField(
        GuestUser,
        on_delete=models.CASCADE,
        related_name='daily_mcq_subscription',
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    questions_per_day = models.PositiveIntegerField(
        default=3,
        validators=[
            MinValueValidator(MIN_QUESTIONS_PER_DAY),
            MaxValueValidator(MAX_QUESTIONS_PER_DAY),
        ],
        help_text='How many MCQs to deliver each day (1-12).',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Daily MCQ Subscription'
        verbose_name_plural = 'Daily MCQ Subscriptions'
        ordering = ['-updated_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='mcq_subscription_must_have_user_or_guest',
            ),
            models.CheckConstraint(
                condition=~(
                    models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)
                ),
                name='mcq_subscription_cannot_have_both_user_and_guest',
            ),
            models.CheckConstraint(
                condition=models.Q(questions_per_day__gte=1)
                & models.Q(questions_per_day__lte=12),
                name='mcq_subscription_questions_per_day_range',
            ),
        ]
        indexes = [
            models.Index(fields=['is_active'], name='mcq_subscription_active_idx'),
        ]

    def __str__(self):
        owner = self.user.username if self.user else f'Guest {self.guest_user.guest_id}'
        state = 'active' if self.is_active else 'paused'
        return f'{owner}: {self.questions_per_day}/day ({state})'


class DailyMcqDelivery(models.Model):
    """One MCQ actually delivered to one subscriber, plus their answer.

    This is both the audit trail of what was sent and the answer sheet the
    app writes back to, which is what powers the monthly leaderboard and each
    subscriber's correct/incorrect breakdown.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='daily_mcq_deliveries',
        null=True,
        blank=True,
    )
    guest_user = models.ForeignKey(
        GuestUser,
        on_delete=models.CASCADE,
        related_name='daily_mcq_deliveries',
        null=True,
        blank=True,
    )
    question = models.ForeignKey(
        'api.Question',
        on_delete=models.CASCADE,
        related_name='daily_mcq_deliveries',
    )
    delivered_date = models.DateField()
    slot = models.PositiveIntegerField(
        help_text='Which delivery slot of the day this question was sent in.',
    )
    selected_answer = models.ForeignKey(
        'api.Answer',
        on_delete=models.SET_NULL,
        related_name='daily_mcq_selections',
        null=True,
        blank=True,
    )
    is_correct = models.BooleanField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Daily MCQ Delivery'
        verbose_name_plural = 'Daily MCQ Deliveries'
        ordering = ['-delivered_date', '-slot', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='mcq_delivery_must_have_user_or_guest',
            ),
            models.CheckConstraint(
                condition=~(
                    models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)
                ),
                name='mcq_delivery_cannot_have_both_user_and_guest',
            ),
            models.UniqueConstraint(
                fields=['user', 'delivered_date', 'slot'],
                condition=models.Q(user__isnull=False),
                name='unique_user_mcq_delivery_slot',
            ),
            models.UniqueConstraint(
                fields=['guest_user', 'delivered_date', 'slot'],
                condition=models.Q(guest_user__isnull=False),
                name='unique_guest_mcq_delivery_slot',
            ),
        ]
        indexes = [
            models.Index(
                fields=['user', 'delivered_date'],
                name='mcq_delivery_user_date_idx',
            ),
            models.Index(
                fields=['guest_user', 'delivered_date'],
                name='mcq_delivery_guest_date_idx',
            ),
            # Backs the monthly leaderboard aggregation.
            models.Index(
                fields=['delivered_date', 'is_correct'],
                name='mcq_delivery_date_correct_idx',
            ),
        ]

    def __str__(self):
        owner = self.user.username if self.user else f'Guest {self.guest_user.guest_id}'
        state = 'unanswered' if self.is_correct is None else (
            'correct' if self.is_correct else 'wrong'
        )
        return f'{owner} - {self.delivered_date} slot {self.slot} ({state})'

    @property
    def is_answered(self):
        return self.answered_at is not None

    def record_answer(self, answer):
        """Store the chosen answer and grade it. Idempotent guard is on the view."""
        self.selected_answer = answer
        self.is_correct = bool(answer.is_correct)
        self.answered_at = timezone.now()
        self.save(update_fields=['selected_answer', 'is_correct', 'answered_at'])
        return self


class NotificationDelivery(models.Model):
    """Dedupe ledger so an automation never re-notifies the same target twice.

    Keyed by (kind, dedupe_key) where dedupe_key encodes the recipient and the
    logical occurrence, e.g. ``user:41:2026-08-04`` for a daily reminder or
    ``user:41:course:7`` for a one-time course launch notice.
    """

    kind = models.CharField(max_length=32, choices=NotificationKind.choices)
    dedupe_key = models.CharField(max_length=190)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Notification Delivery'
        verbose_name_plural = 'Notification Deliveries'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['kind', 'dedupe_key'],
                name='unique_notification_delivery',
            ),
        ]

    def __str__(self):
        return f'{self.kind}:{self.dedupe_key}'
