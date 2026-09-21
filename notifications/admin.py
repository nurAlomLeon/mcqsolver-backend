import logging

from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path, reverse

from .forms import SendNotificationConfirmationForm
from .models import (
    DailyMcqDelivery,
    DailyMcqSubscription,
    DeviceInstallation,
    Notification,
    NotificationAutomation,
    NotificationCampaign,
    NotificationDelivery,
    NotificationKind,
)
from .services import BROADCAST_TOPIC, dispatch_campaign, resolve_campaign_targets

logger = logging.getLogger(__name__)


@admin.register(NotificationAutomation)
class NotificationAutomationAdmin(admin.ModelAdmin):
    """Master on/off switches for every automated notification."""

    list_display = [
        'get_kind_display', 'is_enabled', 'daytime_start_hour',
        'daytime_end_hour', 'last_run_at',
    ]
    list_filter = ['is_enabled']
    list_editable = ['is_enabled', 'daytime_start_hour', 'daytime_end_hour']
    readonly_fields = ['last_run_at', 'updated_at']
    actions = ['enable_automations', 'disable_automations']

    fieldsets = (
        (None, {
            'fields': ('kind', 'is_enabled'),
        }),
        ('Daytime window (Asia/Dhaka)', {
            'fields': ('daytime_start_hour', 'daytime_end_hour'),
            'description': (
                'Local Bangladesh time this automation is allowed to send, e.g. '
                '8-22 for 8 AM to 10 PM. Applies even though the server clock '
                'itself runs in UTC.'
            ),
        }),
        ('Message', {
            'fields': ('title_template', 'message_template'),
            'description': 'Leave blank to use the built-in default wording.',
        }),
        ('Inactivity reminder settings', {
            'fields': ('inactivity_days', 'include_question'),
            'classes': ('collapse',),
        }),
        ('New course launch settings', {
            'fields': ('new_course_lookback_hours',),
            'classes': ('collapse',),
        }),
        ('Daily MCQ settings', {
            'fields': ('default_questions_per_day',),
            'classes': ('collapse',),
            'description': (
                "Each subscriber's questions (1-12/day) are spread evenly across "
                'the daytime window above, e.g. 7/day across an 8-22 window '
                'delivers one roughly every 2 hours.'
            ),
        }),
        ('Status', {
            'fields': ('last_run_at', 'updated_at'),
        }),
    )

    @admin.display(description='Automation', ordering='kind')
    def get_kind_display(self, obj):
        return obj.get_kind_display()

    @admin.action(description='Turn ON selected automations')
    def enable_automations(self, request, queryset):
        updated = queryset.update(is_enabled=True)
        self.message_user(request, f'Enabled {updated} automation(s).', messages.SUCCESS)

    @admin.action(description='Turn OFF selected automations')
    def disable_automations(self, request, queryset):
        updated = queryset.update(is_enabled=False)
        self.message_user(request, f'Disabled {updated} automation(s).', messages.SUCCESS)

    def get_changelist_instance(self, request):
        # Make sure a row exists for every automation kind so admins always
        # see the full set of switches without creating them by hand.
        for kind, _label in NotificationKind.choices:
            if kind == NotificationKind.MANUAL:
                continue
            NotificationAutomation.objects.get_or_create(kind=kind)
        return super().get_changelist_instance(request)


@admin.register(DeviceInstallation)
class DeviceInstallationAdmin(admin.ModelAdmin):
    list_display = [
        'get_owner_identifier', 'platform', 'token_preview', 'is_active',
        'updated_at',
    ]
    list_filter = ['platform', 'is_active']
    search_fields = ['user__username', 'guest_user__guest_id']
    readonly_fields = ['token', 'created_at', 'updated_at']

    @admin.display(description='Owner')
    def get_owner_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f'Guest {obj.guest_user.guest_id}'

    def token_preview(self, obj):
        return obj.token_preview
    token_preview.short_description = 'Token'

    def has_add_permission(self, request):
        # Installations are created by the Flutter app only.
        return False


@admin.register(NotificationCampaign)
class NotificationCampaignAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'kind', 'audience', 'course', 'status', 'scheduled_at',
        'recipient_count', 'sent_at',
    ]
    list_filter = ['status', 'kind', 'audience']
    search_fields = ['title', 'message']
    autocomplete_fields = ['course']
    readonly_fields = [
        'kind', 'created_by', 'sent_at', 'firebase_message_id',
        'recipient_count', 'failure_count', 'error_message',
        'created_at', 'updated_at',
    ]
    fieldsets = (
        ('Message', {
            'fields': ('title', 'message'),
        }),
        ('Targeting', {
            'fields': ('audience', 'course'),
            'description': (
                'Choose "Everyone" for a topic broadcast, or target a course\'s '
                'enrolled learners / inactive learners for a per-device send.'
            ),
        }),
        ('Scheduling', {
            'fields': ('status', 'scheduled_at'),
            'description': (
                'To schedule: set status to "Scheduled" and pick a future time. '
                'Cron will send it automatically (requires the "Scheduled '
                'broadcast" automation to be ON).'
            ),
        }),
        ('Result', {
            'fields': (
                'kind', 'created_by', 'sent_at', 'recipient_count',
                'failure_count', 'firebase_message_id', 'error_message',
                'created_at', 'updated_at',
            ),
        }),
    )
    actions = ['send_notification_action']

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        # Status is editable so admins can move a draft to "Scheduled".
        return [field for field in fields if field != 'status']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:campaign_id>/send-confirm/',
                self.admin_site.admin_view(self.send_confirm_view),
                name='notifications_campaign_send_confirm',
            ),
        ]
        return custom_urls + urls

    @admin.action(description='Send notification')
    def send_notification_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                'Select exactly one campaign to send.',
                messages.ERROR,
            )
            return None

        campaign = queryset.first()
        url = reverse(
            'admin:notifications_campaign_send_confirm',
            args=[campaign.pk],
        )
        return redirect(url)

    def send_confirm_view(self, request, campaign_id):
        campaign = self.get_object(request, campaign_id)
        if campaign is None:
            self.message_user(request, 'Campaign not found.', messages.ERROR)
            return redirect('admin:notifications_notificationcampaign_changelist')

        if request.method == 'POST':
            form = SendNotificationConfirmationForm(request.POST)
            if form.is_valid():
                self._send_campaign(request, campaign)
                return redirect('admin:notifications_notificationcampaign_changelist')
        else:
            form = SendNotificationConfirmationForm()

        is_broadcast = campaign.audience == NotificationCampaign.Audience.ALL
        recipient_estimate = (
            None if is_broadcast else resolve_campaign_targets(campaign).count()
        )

        context = self.admin_site.each_context(request)
        context.update({
            'opts': self.model._meta,
            'title': f'Send notification: {campaign.title}',
            'campaign': campaign,
            'form': form,
            'topic': BROADCAST_TOPIC,
            'is_broadcast': is_broadcast,
            'recipient_estimate': recipient_estimate,
        })
        return render(request, 'admin/notifications/send_confirmation.html', context)

    def _send_campaign(self, request, campaign):
        result = dispatch_campaign(campaign)

        if result.status == NotificationCampaign.Status.SENT:
            self.message_user(
                request,
                f'"{result.title}" sent to {result.recipient_count} recipient(s).',
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                f'Failed to send "{result.title}": {result.error_message}',
                messages.ERROR,
            )


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['get_owner_identifier', 'title', 'kind', 'is_read', 'created_at']
    list_filter = ['kind', 'is_read', 'created_at']
    search_fields = ['title', 'body', 'user__username', 'guest_user__guest_id']
    readonly_fields = ['created_at', 'read_at']

    @admin.display(description='Recipient')
    def get_owner_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f'Guest {obj.guest_user.guest_id}'

    def has_add_permission(self, request):
        return False


@admin.register(DailyMcqSubscription)
class DailyMcqSubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        'get_owner_identifier', 'questions_per_day', 'is_active', 'updated_at',
    ]
    list_filter = ['is_active', 'questions_per_day']
    search_fields = ['user__username', 'guest_user__guest_id']
    list_editable = ['is_active']
    readonly_fields = ['created_at', 'updated_at']

    @admin.display(description='Subscriber')
    def get_owner_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f'Guest {obj.guest_user.guest_id}'


@admin.register(DailyMcqDelivery)
class DailyMcqDeliveryAdmin(admin.ModelAdmin):
    list_display = [
        'get_owner_identifier', 'delivered_date', 'slot', 'question',
        'is_correct', 'answered_at',
    ]
    list_filter = ['is_correct', 'delivered_date']
    search_fields = [
        'user__username', 'guest_user__guest_id', 'question__question_text',
    ]
    readonly_fields = [
        'user', 'guest_user', 'question', 'delivered_date', 'slot',
        'selected_answer', 'is_correct', 'answered_at', 'created_at',
    ]

    @admin.display(description='Recipient')
    def get_owner_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f'Guest {obj.guest_user.guest_id}'

    def has_add_permission(self, request):
        return False


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = ['kind', 'dedupe_key', 'created_at']
    list_filter = ['kind', 'created_at']
    search_fields = ['dedupe_key']
    readonly_fields = ['kind', 'dedupe_key', 'created_at']

    def has_add_permission(self, request):
        return False
