from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase

from api.daily_exam_services import ensure_daily_exam
from api.models import Answer, Category, Question
from notifications import automations
from notifications.models import (
    DeviceInstallation,
    NotificationAutomation,
    NotificationCampaign,
    NotificationDelivery,
    NotificationKind,
)

DHAKA_TZ = ZoneInfo('Asia/Dhaka')


def at_dhaka(hour, minute=0):
    return datetime(2026, 10, 19, hour, minute, tzinfo=DHAKA_TZ)


class DailyExamReminderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='learner',
            password='password',
        )
        DeviceInstallation.objects.create(
            user=self.user,
            token='daily-exam-reminder-token',
        )
        self.root = Category.objects.create(name='Math', level=0)
        self.leaf = Category.objects.create(name='Algebra', parent=self.root)
        for index in range(20):
            question = Question.objects.create(
                category=self.leaf,
                question_text=f'Question {index}',
            )
            Answer.objects.create(
                question=question,
                text='Correct',
                is_correct=True,
            )
            Answer.objects.create(
                question=question,
                text='Wrong',
                is_correct=False,
            )

        self.automation, _ = NotificationAutomation.objects.get_or_create(
            kind=NotificationKind.DAILY_EXAM_REMINDER,
            defaults={
                'is_enabled': True,
                'daytime_start_hour': 10,
                'daytime_end_hour': 11,
            },
        )
        self.automation.is_enabled = True
        self.automation.daytime_start_hour = 10
        self.automation.daytime_end_hour = 11
        self.automation.last_run_at = None
        self.automation.save()

        self.exam, _ = ensure_daily_exam(
            exam_date=date(2026, 10, 19),
            now=at_dhaka(10, 0),
        )

    def _fake_dispatch(self, campaign):
        campaign.status = NotificationCampaign.Status.SENT
        campaign.save(update_fields=['status'])
        return campaign

    def test_reminder_is_skipped_before_10_am(self):
        result = automations.run_daily_exam_reminder(now=at_dhaka(9, 0))
        self.assertEqual(result['sent'], 0)
        self.assertEqual(result['skipped'], 'outside daytime window')

    def test_reminder_is_skipped_after_11_am(self):
        result = automations.run_daily_exam_reminder(now=at_dhaka(11, 0))
        self.assertEqual(result['sent'], 0)
        self.assertEqual(result['skipped'], 'outside daytime window')

    @patch('notifications.automations.dispatch_campaign')
    def test_reminder_sends_once_with_daily_exam_route(self, dispatch):
        dispatch.side_effect = self._fake_dispatch

        result = automations.run_daily_exam_reminder(now=at_dhaka(10, 5))

        self.assertEqual(result['sent'], 1)
        campaign = NotificationCampaign.objects.get(pk=result['campaign_id'])
        self.assertEqual(campaign.kind, NotificationKind.DAILY_EXAM_REMINDER)
        self.assertEqual(campaign.route, '/daily-exam')
        self.assertEqual(campaign.payload, {'daily_exam_id': self.exam.pk})
        self.assertIn(self.exam.category.name, campaign.message)
        dispatch.assert_called_once()

        second = automations.run_daily_exam_reminder(now=at_dhaka(10, 30))
        self.assertEqual(second['sent'], 0)
        self.assertIn(second['skipped'], {'already sent', 'already ran today'})
        self.assertEqual(
            NotificationDelivery.objects.filter(
                kind=NotificationKind.DAILY_EXAM_REMINDER,
            ).count(),
            1,
        )

    @patch('notifications.automations.dispatch_campaign')
    def test_reminder_is_deduped_even_without_last_run_gate(self, dispatch):
        dispatch.side_effect = self._fake_dispatch
        automations.run_daily_exam_reminder(now=at_dhaka(10, 5))
        self.automation.refresh_from_db()
        self.automation.last_run_at = None
        self.automation.save(update_fields=['last_run_at'])

        result = automations.run_daily_exam_reminder(now=at_dhaka(10, 30))
        self.assertEqual(result['sent'], 0)
        self.assertEqual(result['skipped'], 'already sent')
        dispatch.assert_called_once()

    def test_disabled_automation_does_not_send(self):
        self.automation.is_enabled = False
        self.automation.save(update_fields=['is_enabled'])
        result = automations.run_daily_exam_reminder(
            now=at_dhaka(10, 5),
            force=True,
        )
        self.assertEqual(result['sent'], 0)
        self.assertEqual(result['skipped'], 'disabled')

    @patch('notifications.automations.dispatch_campaign')
    def test_failed_campaign_is_retryable(self, dispatch):
        dispatch.return_value = NotificationCampaign(
            pk=999,
            status=NotificationCampaign.Status.FAILED,
        )

        result = automations.run_daily_exam_reminder(now=at_dhaka(10, 5))

        self.assertEqual(result['sent'], 0)
        self.assertFalse(NotificationDelivery.objects.filter(
            kind=NotificationKind.DAILY_EXAM_REMINDER,
        ).exists())

        self.automation.refresh_from_db()
        self.assertIsNone(self.automation.last_run_at)


class DailyExamCampaignDispatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dispatch-learner')
        DeviceInstallation.objects.create(
            user=self.user,
            token='dispatch-token',
        )

    @patch('notifications.services.create_in_app_notifications')
    @patch('notifications.services.send_topic_notification')
    def test_broadcast_carries_route_and_payload(self, send_topic, in_app):
        send_topic.return_value = 'firebase-message-id'
        in_app.return_value = 1
        campaign = NotificationCampaign.objects.create(
            title='Daily Exam',
            message='Tonight',
            audience=NotificationCampaign.Audience.ALL,
            route='/daily-exam',
            payload={'daily_exam_id': 42},
        )

        from .services import dispatch_campaign
        result = dispatch_campaign(campaign)

        self.assertEqual(result.status, NotificationCampaign.Status.SENT)
        send_topic.assert_called_once()
        self.assertEqual(
            send_topic.call_args.kwargs['data']['route'],
            '/daily-exam',
        )
        self.assertEqual(
            send_topic.call_args.kwargs['data']['daily_exam_id'],
            42,
        )
        self.assertEqual(
            in_app.call_args.kwargs['route'],
            '/daily-exam',
        )
        self.assertEqual(
            in_app.call_args.kwargs['payload'],
            {'daily_exam_id': 42},
        )
