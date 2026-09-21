from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from api.models import Answer, Category, GuestUser, Question, UserActivity
from courses.models import Course, CourseEnrollment

from . import automations
from .firebase import FirebaseNotConfigured
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
from .services import find_inactive_owner_ids, resolve_campaign_targets


class DeviceInstallationViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/notifications/installations/'
        self.user = User.objects.create_user(username='learner', password='password')
        self.token = Token.objects.create(user=self.user)
        self.guest = GuestUser.objects.create(device_id='device-123')

    def test_requires_authentication(self):
        response = self.client.post(self.url, {'token': 'fcm-token'}, format='json')
        self.assertIn(response.status_code, [401, 403])

    def test_registers_installation_for_authenticated_user(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        response = self.client.post(
            self.url,
            {'token': 'fcm-token-abc', 'platform': 'android'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        installation = DeviceInstallation.objects.get(token='fcm-token-abc')
        self.assertEqual(installation.user, self.user)
        self.assertIsNone(installation.guest_user)

    def test_registers_installation_for_guest_user(self):
        response = self.client.post(
            self.url,
            {'token': 'fcm-token-guest', 'platform': 'android'},
            format='json',
            HTTP_X_GUEST_TOKEN=str(self.guest.guest_id),
            HTTP_X_DEVICE_ID=self.guest.device_id,
        )

        self.assertEqual(response.status_code, 201)
        installation = DeviceInstallation.objects.get(token='fcm-token-guest')
        self.assertEqual(installation.guest_user, self.guest)
        self.assertIsNone(installation.user)

    def test_refreshing_same_token_updates_existing_installation(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.client.post(self.url, {'token': 'fcm-token-refresh'}, format='json')

        response = self.client.post(
            self.url,
            {'token': 'fcm-token-refresh', 'platform': 'android'},
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            DeviceInstallation.objects.filter(token='fcm-token-refresh').count(),
            1,
        )


class NotificationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='reader', password='password')
        self.token = Token.objects.create(user=self.user)
        self.other_user = User.objects.create_user(username='other', password='password')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        self.mine = Notification.objects.create(
            user=self.user, title='Mine', body='For me',
        )
        Notification.objects.create(
            user=self.other_user, title='Theirs', body='Not for me',
        )

    def test_list_returns_only_own_notifications(self):
        response = self.client.get('/api/notifications/')

        self.assertEqual(response.status_code, 200)
        titles = [item['title'] for item in response.data['results']]
        self.assertEqual(titles, ['Mine'])

    def test_unread_count(self):
        response = self.client.get('/api/notifications/unread-count/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['unread_count'], 1)

    def test_mark_single_notification_read(self):
        response = self.client.post(f'/api/notifications/{self.mine.pk}/read/')

        self.assertEqual(response.status_code, 200)
        self.mine.refresh_from_db()
        self.assertTrue(self.mine.is_read)
        self.assertIsNotNone(self.mine.read_at)

    def test_cannot_mark_another_users_notification_read(self):
        theirs = Notification.objects.get(title='Theirs')

        response = self.client.post(f'/api/notifications/{theirs.pk}/read/')

        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)

    def test_mark_all_read(self):
        response = self.client.post('/api/notifications/read-all/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['marked_read'], 1)
        self.assertEqual(
            Notification.objects.filter(user=self.user, is_read=False).count(), 0,
        )
        # The other user's notification must be untouched.
        self.assertFalse(Notification.objects.get(title='Theirs').is_read)

    def test_guest_sees_only_guest_notifications(self):
        guest = GuestUser.objects.create(device_id='guest-device')
        Notification.objects.create(guest_user=guest, title='Guest note', body='Hi')
        guest_client = APIClient()

        response = guest_client.get(
            '/api/notifications/',
            HTTP_X_GUEST_TOKEN=str(guest.guest_id),
            HTTP_X_DEVICE_ID=guest.device_id,
        )

        self.assertEqual(response.status_code, 200)
        titles = [item['title'] for item in response.data['results']]
        self.assertEqual(titles, ['Guest note'])


class AudienceResolutionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='staff', is_staff=True)
        self.enrolled = User.objects.create_user(username='enrolled')
        self.not_enrolled = User.objects.create_user(username='outsider')
        self.course = Course.objects.create(
            name='Bangla', is_published=True, created_by=self.staff,
        )
        CourseEnrollment.objects.create(course=self.course, user=self.enrolled)

        self.enrolled_install = DeviceInstallation.objects.create(
            user=self.enrolled, token='token-enrolled',
        )
        DeviceInstallation.objects.create(
            user=self.not_enrolled, token='token-outsider',
        )

    def test_course_audience_targets_only_enrolled_devices(self):
        campaign = NotificationCampaign.objects.create(
            title='Class today', message='Join at 8pm',
            audience=NotificationCampaign.Audience.COURSE,
            course=self.course,
        )

        targets = resolve_campaign_targets(campaign)

        self.assertEqual([install.token for install in targets], ['token-enrolled'])

    def test_course_audience_without_course_targets_nobody(self):
        campaign = NotificationCampaign.objects.create(
            title='Broken', message='No course set',
            audience=NotificationCampaign.Audience.COURSE,
        )

        self.assertEqual(resolve_campaign_targets(campaign).count(), 0)

    def test_all_audience_includes_every_active_device(self):
        campaign = NotificationCampaign.objects.create(
            title='Everyone', message='Hello',
            audience=NotificationCampaign.Audience.ALL,
        )

        self.assertEqual(resolve_campaign_targets(campaign).count(), 2)

    def test_inactive_devices_are_excluded(self):
        self.enrolled_install.is_active = False
        self.enrolled_install.save(update_fields=['is_active'])
        campaign = NotificationCampaign.objects.create(
            title='Everyone', message='Hello',
            audience=NotificationCampaign.Audience.ALL,
        )

        self.assertEqual(resolve_campaign_targets(campaign).count(), 1)


class InactivityDetectionTests(TestCase):
    def setUp(self):
        self.active_user = User.objects.create_user(username='active')
        self.idle_user = User.objects.create_user(username='idle')
        DeviceInstallation.objects.create(user=self.active_user, token='t-active')
        DeviceInstallation.objects.create(user=self.idle_user, token='t-idle')

    def test_recent_activity_excludes_user_from_inactive_set(self):
        UserActivity.objects.create(
            user=self.active_user,
            activity_type=UserActivity.ActivityType.QUESTION_ANSWERED,
        )

        user_ids, _guest_ids = find_inactive_owner_ids(inactivity_days=2)

        self.assertIn(self.idle_user.id, user_ids)
        self.assertNotIn(self.active_user.id, user_ids)

    def test_users_without_devices_are_never_returned(self):
        User.objects.create_user(username='deviceless')

        user_ids, _guest_ids = find_inactive_owner_ids(inactivity_days=2)

        self.assertEqual(user_ids, {self.idle_user.id, self.active_user.id})


class InactivityReminderAutomationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='idle')
        DeviceInstallation.objects.create(user=self.user, token='t-idle')
        self.automation = automations.get_automation(
            NotificationKind.INACTIVITY_REMINDER,
        )

    def test_disabled_automation_sends_nothing(self):
        with patch('notifications.services.send_token_notifications') as mock_send:
            result = automations.run_inactivity_reminder()

        mock_send.assert_not_called()
        self.assertEqual(result['skipped'], 'disabled')

    @patch('notifications.services.send_token_notifications')
    def test_enabled_automation_sends_and_creates_in_app_record(self, mock_send):
        mock_send.return_value = (1, 0, [])
        self.automation.is_enabled = True
        self.automation.save()

        result = automations.run_inactivity_reminder(force=True)

        self.assertEqual(result['sent'], 1)
        mock_send.assert_called_once()
        notification = Notification.objects.get(user=self.user)
        self.assertEqual(notification.kind, NotificationKind.INACTIVITY_REMINDER)
        self.assertEqual(notification.route, '/home')

    @patch('notifications.services.send_token_notifications')
    def test_second_run_same_day_is_deduped(self, mock_send):
        mock_send.return_value = (1, 0, [])
        self.automation.is_enabled = True
        self.automation.save()

        automations.run_inactivity_reminder(force=True)
        second = automations.run_inactivity_reminder(force=True)

        self.assertEqual(second['sent'], 0)
        self.assertEqual(second['deduped'], 1)
        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    @patch('notifications.services.send_token_notifications')
    def test_explanation_is_sent_under_did_you_know_title(self, mock_send):
        mock_send.return_value = (1, 0, [])
        category = Category.objects.create(name='Bangla')
        question = Question.objects.create(
            category=category,
            question_text='What is the capital of Bangladesh?',
            explanation='Dhaka has been the capital since 1971.',
        )
        Answer.objects.create(question=question, text='Dhaka', is_correct=True)
        self.automation.is_enabled = True
        self.automation.include_question = True
        self.automation.save()

        automations.run_inactivity_reminder(force=True)

        notification = Notification.objects.get(user=self.user)
        self.assertEqual(notification.title, 'আপনি কি জানেন?')
        self.assertIn('capital since 1971', notification.body)
        # The MCQ text itself must not leak into the explanation reminder.
        self.assertNotIn('What is the capital', notification.body)
        self.assertEqual(notification.payload['question_id'], question.id)

    @patch('notifications.services.send_token_notifications')
    def test_questions_without_explanation_are_skipped(self, mock_send):
        mock_send.return_value = (1, 0, [])
        category = Category.objects.create(name='Bangla')
        Question.objects.create(
            category=category, question_text='No explanation here', explanation='',
        )
        self.automation.is_enabled = True
        self.automation.include_question = True
        self.automation.save()

        automations.run_inactivity_reminder(force=True)

        notification = Notification.objects.get(user=self.user)
        # Falls back to the plain nudge rather than sending an empty body.
        self.assertNotIn('No explanation here', notification.body)
        self.assertTrue(notification.body.strip())

    @patch('notifications.services.send_token_notifications')
    def test_long_explanation_is_truncated(self, mock_send):
        mock_send.return_value = (1, 0, [])
        category = Category.objects.create(name='Bangla')
        Question.objects.create(
            category=category,
            question_text='Q',
            explanation='x' * 1000,
        )
        self.automation.is_enabled = True
        self.automation.include_question = True
        self.automation.save()

        automations.run_inactivity_reminder(force=True)

        notification = Notification.objects.get(user=self.user)
        self.assertLessEqual(
            len(notification.body), automations.EXPLANATION_BODY_LIMIT,
        )

    @patch('notifications.services.send_token_notifications')
    def test_outside_daytime_window_is_skipped(self, mock_send):
        self.automation.is_enabled = True
        self.automation.daytime_start_hour = 8
        self.automation.daytime_end_hour = 22
        self.automation.save()
        # 2 AM UTC is 8 AM in Dhaka (UTC+6) - inside the window, so pick a UTC
        # hour that lands outside it once converted to Asia/Dhaka.
        midnight_dhaka = timezone.now().replace(hour=19, minute=0)  # 1 AM Dhaka

        result = automations.run_inactivity_reminder(now=midnight_dhaka)

        self.assertEqual(result['skipped'], 'outside daytime window')
        mock_send.assert_not_called()


class DailyMcqSubscriptionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = '/api/notifications/daily-mcq/'
        self.user = User.objects.create_user(username='subscriber')
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def test_requires_authentication(self):
        anonymous = APIClient()
        self.assertIn(anonymous.get(self.url).status_code, [401, 403])

    def test_get_returns_unsubscribed_default_without_creating_a_row(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_active'])
        self.assertEqual(response.data['max_questions_per_day'], 12)
        self.assertEqual(DailyMcqSubscription.objects.count(), 0)

    def test_subscribing_creates_the_subscription(self):
        response = self.client.put(
            self.url, {'is_active': True, 'questions_per_day': 5}, format='json',
        )

        self.assertEqual(response.status_code, 200)
        subscription = DailyMcqSubscription.objects.get(user=self.user)
        self.assertTrue(subscription.is_active)
        self.assertEqual(subscription.questions_per_day, 5)

    def test_updating_existing_subscription_does_not_duplicate(self):
        self.client.put(self.url, {'questions_per_day': 2}, format='json')
        self.client.put(self.url, {'questions_per_day': 7}, format='json')

        self.assertEqual(DailyMcqSubscription.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            DailyMcqSubscription.objects.get(user=self.user).questions_per_day, 7,
        )

    def test_accepts_the_new_maximum_of_twelve(self):
        response = self.client.put(
            self.url, {'is_active': True, 'questions_per_day': 12}, format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['questions_per_day'], 12)
        self.assertEqual(response.data['max_questions_per_day'], 12)

    def test_rejects_more_than_twelve_questions(self):
        response = self.client.put(
            self.url, {'questions_per_day': 13}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('questions_per_day', response.data)

    def test_rejects_zero_questions(self):
        response = self.client.put(
            self.url, {'questions_per_day': 0}, format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_unsubscribing_keeps_the_row_but_marks_inactive(self):
        self.client.put(self.url, {'is_active': True}, format='json')

        response = self.client.put(self.url, {'is_active': False}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(DailyMcqSubscription.objects.get(user=self.user).is_active)

    def test_guest_can_subscribe(self):
        guest = GuestUser.objects.create(device_id='mcq-guest')
        guest_client = APIClient()

        response = guest_client.put(
            self.url,
            {'is_active': True, 'questions_per_day': 4},
            format='json',
            HTTP_X_GUEST_TOKEN=str(guest.guest_id),
            HTTP_X_DEVICE_ID=guest.device_id,
        )

        self.assertEqual(response.status_code, 200)
        subscription = DailyMcqSubscription.objects.get(guest_user=guest)
        self.assertEqual(subscription.questions_per_day, 4)
        self.assertIsNone(subscription.user)


class DailyMcqAutomationTests(TestCase):
    """Slot spacing is proportional: (window hours) / questions_per_day.

    The default window here is 8-20 Asia/Dhaka (12 hours) so a 3/day
    subscriber lands on a clean 4-hour spacing (slots at local hours 8, 12,
    16). Dhaka is UTC+6 year-round (no DST), so ``_at_dhaka_hour`` converts a
    desired local hour to the UTC instant ``run_daily_mcq`` actually receives.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='subscriber')
        DeviceInstallation.objects.create(user=self.user, token='t-mcq')
        self.automation = automations.get_automation(NotificationKind.DAILY_MCQ)
        self.automation.is_enabled = True
        self.automation.daytime_start_hour = 8
        self.automation.daytime_end_hour = 20
        self.automation.save()

        category = Category.objects.create(name='Bangla')
        for index in range(12):
            question = Question.objects.create(
                category=category, question_text=f'Question {index}?',
            )
            Answer.objects.create(question=question, text='Right', is_correct=True)
            Answer.objects.create(question=question, text='Wrong', is_correct=False)

        self.subscription = DailyMcqSubscription.objects.create(
            user=self.user, is_active=True, questions_per_day=3,
        )

    def _at_dhaka_hour(self, hour, minute=0):
        """A UTC instant whose Asia/Dhaka (UTC+6) local hour equals ``hour``."""
        utc_hour = (hour - 6) % 24
        return timezone.now().replace(
            hour=utc_hour, minute=minute, second=0, microsecond=0,
        )

    def test_disabled_automation_sends_nothing(self):
        self.automation.is_enabled = False
        self.automation.save()

        with patch('notifications.services.send_token_notifications') as mock_send:
            result = automations.run_daily_mcq(now=self._at_dhaka_hour(8))

        mock_send.assert_not_called()
        self.assertEqual(result['skipped'], 'disabled')

    @patch('notifications.services.send_token_notifications')
    def test_sends_one_mcq_at_the_first_slot(self, mock_send):
        mock_send.return_value = (1, 0, [])

        # 12-hour window / 3 per day = one every 4 hours, starting at hour 8.
        result = automations.run_daily_mcq(now=self._at_dhaka_hour(8))

        self.assertEqual(result['sent'], 1)
        notification = Notification.objects.get(user=self.user)
        self.assertEqual(notification.kind, NotificationKind.DAILY_MCQ)
        self.assertIn('1/3', notification.title)
        self.assertIn('Question', notification.body)
        self.assertIn('ক)', notification.body)

        # A delivery row must exist and be linked from the notification so the
        # app can open an answerable question.
        delivery = DailyMcqDelivery.objects.get(user=self.user)
        self.assertEqual(notification.payload['mcq_delivery_id'], delivery.id)
        self.assertEqual(notification.route, automations.DAILY_MCQ_ROUTE)
        self.assertEqual(delivery.slot, 0)
        self.assertIsNone(delivery.is_correct)
        self.assertFalse(delivery.is_answered)

    @patch('notifications.services.send_token_notifications')
    def test_second_and_third_slots_land_on_the_expected_hours(self, mock_send):
        mock_send.return_value = (1, 0, [])

        automations.run_daily_mcq(now=self._at_dhaka_hour(8))  # slot 0
        automations.run_daily_mcq(now=self._at_dhaka_hour(12))  # slot 1
        automations.run_daily_mcq(now=self._at_dhaka_hour(16))  # slot 2

        titles = list(
            Notification.objects.filter(user=self.user)
            .order_by('id').values_list('title', flat=True)
        )
        self.assertEqual(len(titles), 3)
        self.assertIn('1/3', titles[0])
        self.assertIn('2/3', titles[1])
        self.assertIn('3/3', titles[2])

    @patch('notifications.services.send_token_notifications')
    def test_an_hour_before_the_next_slot_still_maps_to_the_current_one(self, mock_send):
        mock_send.return_value = (1, 0, [])

        # Slot 1 starts at hour 12 and runs through hour 15 (4-hour spacing);
        # hour 15 must still be slot 1, not slot 2.
        automations.run_daily_mcq(now=self._at_dhaka_hour(12))
        second = automations.run_daily_mcq(now=self._at_dhaka_hour(15))

        # Same slot as the first call, so the dedupe ledger blocks the resend.
        self.assertEqual(second['sent'], 0)
        self.assertEqual(second['deduped'], 1)

    @patch('notifications.services.send_token_notifications')
    def test_proportional_spacing_differs_per_subscriber(self, mock_send):
        """Two subscribers with different counts get independent slot maths."""
        mock_send.return_value = (1, 0, [])
        heavy_user = User.objects.create_user(username='heavy')
        DeviceInstallation.objects.create(user=heavy_user, token='t-heavy')
        # 12-hour window / 6 per day = one every 2 hours.
        DailyMcqSubscription.objects.create(
            user=heavy_user, is_active=True, questions_per_day=6,
        )

        # Hour 14 = 6 hours into the window: slot 6/4=1 for the 3/day
        # subscriber, slot 6/2=3 for the 6/day subscriber.
        automations.run_daily_mcq(now=self._at_dhaka_hour(14))

        light_delivery = DailyMcqDelivery.objects.get(user=self.user)
        heavy_delivery = DailyMcqDelivery.objects.get(user=heavy_user)
        self.assertEqual(light_delivery.slot, 1)
        self.assertEqual(heavy_delivery.slot, 3)

    @patch('notifications.services.send_token_notifications')
    def test_does_not_repeat_a_question_already_delivered(self, mock_send):
        mock_send.return_value = (1, 0, [])
        self.subscription.questions_per_day = 6
        self.subscription.save()

        # 12-hour window / 6 per day = one every 2 hours, starting at hour 8.
        for hour in (8, 10, 12, 14, 16, 18):
            automations.run_daily_mcq(now=self._at_dhaka_hour(hour))

        question_ids = list(
            DailyMcqDelivery.objects.filter(user=self.user)
            .values_list('question_id', flat=True)
        )
        self.assertEqual(len(question_ids), 6)
        self.assertEqual(len(set(question_ids)), 6)

    @patch('notifications.services.send_token_notifications')
    def test_supports_twelve_questions_across_a_wider_window(self, mock_send):
        mock_send.return_value = (1, 0, [])
        self.automation.daytime_start_hour = 8
        self.automation.daytime_end_hour = 22
        self.automation.save()
        self.subscription.questions_per_day = 12
        self.subscription.save()

        # 14-hour window / 12 per day ≈ 1.17h spacing; hour 21 (offset 13) is
        # the last slot, index 11.
        result = automations.run_daily_mcq(now=self._at_dhaka_hour(21))

        self.assertEqual(result['sent'], 1)
        delivery = DailyMcqDelivery.objects.get(user=self.user)
        self.assertEqual(delivery.slot, 11)

    @patch('notifications.services.send_token_notifications')
    def test_rerunning_same_slot_is_deduped(self, mock_send):
        mock_send.return_value = (1, 0, [])

        automations.run_daily_mcq(now=self._at_dhaka_hour(8))
        second = automations.run_daily_mcq(now=self._at_dhaka_hour(8))

        self.assertEqual(second['sent'], 0)
        self.assertEqual(second['deduped'], 1)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    @patch('notifications.services.send_token_notifications')
    def test_before_the_daytime_window_is_skipped(self, mock_send):
        result = automations.run_daily_mcq(now=self._at_dhaka_hour(6))

        self.assertEqual(result['skipped'], 'outside daytime window')
        mock_send.assert_not_called()

    @patch('notifications.services.send_token_notifications')
    def test_after_the_daytime_window_is_skipped(self, mock_send):
        # Night time in Dhaka - this is the case the window exists to prevent.
        result = automations.run_daily_mcq(now=self._at_dhaka_hour(23))

        self.assertEqual(result['skipped'], 'outside daytime window')
        mock_send.assert_not_called()

    @patch('notifications.services.send_token_notifications')
    def test_inactive_subscription_is_ignored(self, mock_send):
        self.subscription.is_active = False
        self.subscription.save()

        result = automations.run_daily_mcq(now=self._at_dhaka_hour(8))

        self.assertEqual(result['sent'], 0)
        mock_send.assert_not_called()

    @patch('notifications.services.send_token_notifications')
    def test_guest_subscriber_receives_mcq(self, mock_send):
        mock_send.return_value = (1, 0, [])
        guest = GuestUser.objects.create(device_id='mcq-guest')
        DeviceInstallation.objects.create(guest_user=guest, token='t-guest-mcq')
        DailyMcqSubscription.objects.create(
            guest_user=guest, is_active=True, questions_per_day=1,
        )

        automations.run_daily_mcq(now=self._at_dhaka_hour(8))

        self.assertTrue(Notification.objects.filter(guest_user=guest).exists())

    @patch('notifications.services.send_token_notifications')
    def test_mcq_body_stays_within_limit(self, mock_send):
        mock_send.return_value = (1, 0, [])
        Question.objects.all().delete()
        category = Category.objects.get(name='Bangla')
        question = Question.objects.create(
            category=category, question_text='y' * 2000,
        )
        Answer.objects.create(question=question, text='z' * 500, is_correct=True)

        automations.run_daily_mcq(now=self._at_dhaka_hour(8))

        notification = Notification.objects.get(user=self.user)
        self.assertLessEqual(len(notification.body), automations.MCQ_BODY_LIMIT)


class DailyMcqAnswerFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='answerer')
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

        category = Category.objects.create(name='Bangla')
        self.question = Question.objects.create(
            category=category,
            question_text='Capital of Bangladesh?',
            explanation='Dhaka since 1971.',
        )
        self.correct = Answer.objects.create(
            question=self.question, text='Dhaka', is_correct=True,
        )
        self.wrong = Answer.objects.create(
            question=self.question, text='Khulna', is_correct=False,
        )
        self.delivery = DailyMcqDelivery.objects.create(
            user=self.user,
            question=self.question,
            delivered_date=timezone.now().date(),
            slot=0,
        )
        self.detail_url = f'/api/notifications/daily-mcq/deliveries/{self.delivery.pk}/'
        self.answer_url = f'{self.detail_url}answer/'

    def test_detail_hides_answer_key_before_answering(self):
        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['correct_answer_id'])
        self.assertIsNone(response.data['explanation'])
        self.assertFalse(response.data['is_answered'])
        self.assertEqual(len(response.data['answers']), 2)
        # Options must not leak is_correct.
        self.assertNotIn('is_correct', response.data['answers'][0])

    def test_correct_answer_reveals_key_and_explanation(self):
        response = self.client.post(
            self.answer_url, {'answer_id': self.correct.pk}, format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_correct'])
        self.assertEqual(response.data['correct_answer_id'], self.correct.pk)
        self.assertEqual(response.data['explanation'], 'Dhaka since 1971.')
        self.delivery.refresh_from_db()
        self.assertTrue(self.delivery.is_correct)
        self.assertIsNotNone(self.delivery.answered_at)

    def test_wrong_answer_still_reveals_the_correct_option(self):
        response = self.client.post(
            self.answer_url, {'answer_id': self.wrong.pk}, format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_correct'])
        self.assertEqual(response.data['correct_answer_id'], self.correct.pk)
        self.assertEqual(response.data['explanation'], 'Dhaka since 1971.')

    def test_answering_twice_is_rejected(self):
        self.client.post(self.answer_url, {'answer_id': self.wrong.pk}, format='json')

        response = self.client.post(
            self.answer_url, {'answer_id': self.correct.pk}, format='json',
        )

        self.assertEqual(response.status_code, 409)
        self.delivery.refresh_from_db()
        # The original wrong answer stands; the score cannot be improved.
        self.assertFalse(self.delivery.is_correct)

    def test_answer_from_another_question_is_rejected(self):
        other = Question.objects.create(question_text='Other?')
        foreign_answer = Answer.objects.create(
            question=other, text='Nope', is_correct=True,
        )

        response = self.client.post(
            self.answer_url, {'answer_id': foreign_answer.pk}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.delivery.refresh_from_db()
        self.assertIsNone(self.delivery.is_correct)

    def test_cannot_answer_another_users_delivery(self):
        other_user = User.objects.create_user(username='intruder')
        other_delivery = DailyMcqDelivery.objects.create(
            user=other_user,
            question=self.question,
            delivered_date=timezone.now().date(),
            slot=0,
        )

        response = self.client.post(
            f'/api/notifications/daily-mcq/deliveries/{other_delivery.pk}/answer/',
            {'answer_id': self.correct.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 404)

    def test_history_lists_deliveries(self):
        response = self.client.get('/api/notifications/daily-mcq/history/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)


class DailyMcqStatsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='performer', first_name='Top', last_name='Learner',
        )
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.rival = User.objects.create_user(username='rival', first_name='Rival')
        self.question = Question.objects.create(question_text='Q?')
        self.url = '/api/notifications/daily-mcq/stats/'

    def _deliver(self, owner, correct, when=None, slot=0, is_guest=False):
        field = 'guest_user' if is_guest else 'user'
        return DailyMcqDelivery.objects.create(
            question=self.question,
            delivered_date=when or timezone.now().date(),
            slot=slot,
            is_correct=correct,
            answered_at=None if correct is None else timezone.now(),
            **{field: owner},
        )

    def test_personal_performance_counts(self):
        self._deliver(self.user, True, slot=0)
        self._deliver(self.user, True, slot=1)
        self._deliver(self.user, False, slot=2)
        self._deliver(self.user, None, slot=3)

        response = self.client.get(self.url)

        month = response.data['performance']['month']
        self.assertEqual(month['total'], 4)
        self.assertEqual(month['correct'], 2)
        self.assertEqual(month['incorrect'], 1)
        self.assertEqual(month['unanswered'], 1)
        self.assertEqual(month['answered'], 3)
        self.assertAlmostEqual(month['accuracy'], 66.67, places=1)

    def test_leaderboard_ranks_by_correct_answers(self):
        self._deliver(self.rival, True, slot=0)
        self._deliver(self.rival, True, slot=1)
        self._deliver(self.rival, True, slot=2)
        self._deliver(self.user, True, slot=0)

        response = self.client.get(self.url)

        board = response.data['leaderboard']
        self.assertEqual(board[0]['display_name'], 'Rival')
        self.assertEqual(board[0]['correct'], 3)
        self.assertEqual(board[0]['rank'], 1)
        self.assertEqual(board[1]['display_name'], 'Top Learner')
        self.assertEqual(board[1]['rank'], 2)
        self.assertTrue(board[1]['is_current_user'])
        self.assertEqual(response.data['current_user_entry']['rank'], 2)

    def test_fewer_incorrect_breaks_a_tie(self):
        self._deliver(self.rival, True, slot=0)
        self._deliver(self.rival, False, slot=1)
        self._deliver(self.user, True, slot=0)

        response = self.client.get(self.url)

        board = response.data['leaderboard']
        # Same correct count, but the caller has no wrong answers.
        self.assertEqual(board[0]['display_name'], 'Top Learner')

    def test_previous_month_is_excluded_from_the_leaderboard(self):
        last_month = timezone.now().date().replace(day=1) - timedelta(days=1)
        self._deliver(self.user, True, when=last_month, slot=0)

        response = self.client.get(self.url)

        self.assertEqual(response.data['leaderboard'], [])
        self.assertEqual(response.data['performance']['month']['correct'], 0)
        # All-time still counts it.
        self.assertEqual(response.data['performance']['all_time']['correct'], 1)

    def test_users_with_no_correct_answers_are_not_ranked(self):
        self._deliver(self.rival, False, slot=0)

        response = self.client.get(self.url)

        self.assertEqual(response.data['leaderboard'], [])

    def test_guests_see_own_stats_but_are_not_ranked(self):
        guest = GuestUser.objects.create(device_id='stats-guest')
        self._deliver(guest, True, is_guest=True, slot=0)
        guest_client = APIClient()

        response = guest_client.get(
            self.url,
            HTTP_X_GUEST_TOKEN=str(guest.guest_id),
            HTTP_X_DEVICE_ID=guest.device_id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_ranked'])
        self.assertEqual(response.data['performance']['month']['correct'], 1)
        # Guest deliveries never appear in the public ranking.
        self.assertEqual(response.data['leaderboard'], [])

    def test_accuracy_is_zero_when_nothing_answered(self):
        response = self.client.get(self.url)

        self.assertEqual(response.data['performance']['month']['accuracy'], 0.0)


class NewCourseLaunchAutomationTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='staff', is_staff=True)
        self.learner = User.objects.create_user(username='learner')
        DeviceInstallation.objects.create(user=self.learner, token='t-learner')
        self.automation = automations.get_automation(
            NotificationKind.NEW_COURSE_LAUNCH,
        )
        self.automation.is_enabled = True
        self.automation.save()

    @patch('notifications.services.send_topic_notification')
    def test_new_published_course_is_announced_once(self, mock_send):
        mock_send.return_value = 'projects/mock/messages/1'
        Course.objects.create(
            name='New Course', is_published=True, created_by=self.staff,
        )

        first = automations.run_new_course_launch(force=True)
        second = automations.run_new_course_launch(force=True)

        self.assertEqual(first['sent'], 1)
        self.assertEqual(second['sent'], 0)
        self.assertEqual(mock_send.call_count, 1)
        campaign = NotificationCampaign.objects.get(kind=NotificationKind.NEW_COURSE_LAUNCH)
        self.assertEqual(campaign.status, NotificationCampaign.Status.SENT)
        self.assertIn('New Course', campaign.message)

    @patch('notifications.services.send_topic_notification')
    def test_unpublished_course_is_not_announced(self, mock_send):
        Course.objects.create(
            name='Draft Course', is_published=False, created_by=self.staff,
        )

        result = automations.run_new_course_launch(force=True)

        self.assertEqual(result['sent'], 0)
        mock_send.assert_not_called()

    @patch('notifications.services.send_topic_notification')
    def test_old_course_outside_lookback_is_not_announced(self, mock_send):
        course = Course.objects.create(
            name='Old Course', is_published=True, created_by=self.staff,
        )
        Course.objects.filter(pk=course.pk).update(
            created_at=timezone.now() - timedelta(days=30),
        )

        result = automations.run_new_course_launch(force=True)

        self.assertEqual(result['sent'], 0)
        mock_send.assert_not_called()


class ScheduledCampaignAutomationTests(TestCase):
    def setUp(self):
        self.learner = User.objects.create_user(username='learner')
        DeviceInstallation.objects.create(user=self.learner, token='t-learner')
        self.automation = automations.get_automation(
            NotificationKind.SCHEDULED_BROADCAST,
        )
        self.automation.is_enabled = True
        self.automation.save()

    @patch('notifications.services.send_topic_notification')
    def test_due_campaign_is_sent(self, mock_send):
        mock_send.return_value = 'projects/mock/messages/due'
        campaign = NotificationCampaign.objects.create(
            title='Scheduled', message='Now due',
            status=NotificationCampaign.Status.SCHEDULED,
            scheduled_at=timezone.now() - timedelta(minutes=5),
        )

        result = automations.run_due_scheduled_campaigns()

        self.assertEqual(result['sent'], 1)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, NotificationCampaign.Status.SENT)

    @patch('notifications.services.send_topic_notification')
    def test_future_campaign_is_not_sent(self, mock_send):
        NotificationCampaign.objects.create(
            title='Later', message='Not yet',
            status=NotificationCampaign.Status.SCHEDULED,
            scheduled_at=timezone.now() + timedelta(hours=2),
        )

        result = automations.run_due_scheduled_campaigns()

        self.assertEqual(result['sent'], 0)
        mock_send.assert_not_called()

    @patch('notifications.services.send_topic_notification')
    def test_disabled_automation_skips_due_campaigns(self, mock_send):
        self.automation.is_enabled = False
        self.automation.save()
        NotificationCampaign.objects.create(
            title='Scheduled', message='Now due',
            status=NotificationCampaign.Status.SCHEDULED,
            scheduled_at=timezone.now() - timedelta(minutes=5),
        )

        result = automations.run_due_scheduled_campaigns()

        self.assertEqual(result['skipped'], 'disabled')
        mock_send.assert_not_called()


class CourseAnnouncementDispatchTests(TestCase):
    def setUp(self):
        announcement = automations.get_automation(
            NotificationKind.COURSE_ANNOUNCEMENT,
        )
        announcement.is_enabled = True
        announcement.save()

    @patch('notifications.services.send_token_notifications')
    def test_course_campaign_multicasts_to_enrolled_devices_only(self, mock_send):
        mock_send.return_value = (1, 0, [])
        staff = User.objects.create_user(username='staff', is_staff=True)
        enrolled = User.objects.create_user(username='enrolled')
        outsider = User.objects.create_user(username='outsider')
        course = Course.objects.create(
            name='Live Course', is_published=True, created_by=staff,
        )
        CourseEnrollment.objects.create(course=course, user=enrolled)
        DeviceInstallation.objects.create(user=enrolled, token='t-enrolled')
        DeviceInstallation.objects.create(user=outsider, token='t-outsider')

        campaign = NotificationCampaign.objects.create(
            title='Class tonight', message='8pm sharp',
            kind=NotificationKind.COURSE_ANNOUNCEMENT,
            audience=NotificationCampaign.Audience.COURSE,
            course=course,
        )
        from .services import dispatch_campaign
        dispatch_campaign(campaign)

        sent_tokens = mock_send.call_args.args[0]
        self.assertEqual(sent_tokens, ['t-enrolled'])
        self.assertTrue(Notification.objects.filter(user=enrolled).exists())
        self.assertFalse(Notification.objects.filter(user=outsider).exists())

    @patch('notifications.services.send_token_notifications')
    def test_invalid_tokens_are_deactivated(self, mock_send):
        mock_send.return_value = (0, 1, ['t-dead'])
        user = User.objects.create_user(username='gone')
        DeviceInstallation.objects.create(user=user, token='t-dead')

        campaign = NotificationCampaign.objects.create(
            title='Ping', message='Hello',
            audience=NotificationCampaign.Audience.INACTIVE,
        )
        from .services import dispatch_campaign
        dispatch_campaign(campaign)

        self.assertFalse(
            DeviceInstallation.objects.get(token='t-dead').is_active,
        )

    @patch('notifications.services.send_token_notifications')
    def test_disabled_course_announcement_type_blocks_send(self, mock_send):
        announcement = automations.get_automation(
            NotificationKind.COURSE_ANNOUNCEMENT,
        )
        announcement.is_enabled = False
        announcement.save()
        staff = User.objects.create_user(username='staff2', is_staff=True)
        course = Course.objects.create(
            name='Gated Course', is_published=True, created_by=staff,
        )
        campaign = NotificationCampaign.objects.create(
            title='Blocked', message='Should not send',
            kind=NotificationKind.COURSE_ANNOUNCEMENT,
            audience=NotificationCampaign.Audience.COURSE,
            course=course,
        )

        from .services import dispatch_campaign
        result = dispatch_campaign(campaign)

        mock_send.assert_not_called()
        self.assertEqual(result.status, NotificationCampaign.Status.FAILED)
        self.assertIn('switched off', result.error_message)


class GuestConversionTransfersInstallationTests(TestCase):
    def test_installation_moves_from_guest_to_user_on_conversion(self):
        client = APIClient()
        guest = GuestUser.objects.create(device_id='device-456')
        DeviceInstallation.objects.create(
            guest_user=guest,
            token='fcm-token-convert',
            platform=DeviceInstallation.Platform.ANDROID,
        )

        response = client.post(
            '/api/auth/convert-guest/',
            {
                'guest_id': str(guest.guest_id),
                'device_id': guest.device_id,
                'email': 'new.learner@example.com',
                'name': 'New Learner',
            },
            format='json',
            HTTP_X_GUEST_TOKEN=str(guest.guest_id),
            HTTP_X_DEVICE_ID=guest.device_id,
        )

        self.assertEqual(response.status_code, 200)
        installation = DeviceInstallation.objects.get(token='fcm-token-convert')
        self.assertIsNone(installation.guest_user)
        self.assertIsNotNone(installation.user)
        self.assertEqual(installation.user.email, 'new.learner@example.com')


class NotificationCampaignAdminSendTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff_user = User.objects.create_superuser(
            username='admin', password='password', email='admin@example.com',
        )
        self.client.force_login(self.staff_user)
        self.campaign = NotificationCampaign.objects.create(
            title='New feature',
            message='Check out the new feature!',
        )

    def test_send_action_redirects_to_confirmation_page(self):
        response = self.client.post(
            '/admin/notifications/notificationcampaign/',
            {
                'action': 'send_notification_action',
                '_selected_action': [str(self.campaign.pk)],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'{self.campaign.pk}/send-confirm', response.url)

    @patch('notifications.services.send_topic_notification')
    def test_confirming_send_marks_campaign_sent(self, mock_send):
        mock_send.return_value = 'projects/mock/messages/123'

        response = self.client.post(
            f'/admin/notifications/notificationcampaign/{self.campaign.pk}/send-confirm/',
            {'confirm': 'on'},
        )

        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, NotificationCampaign.Status.SENT)
        self.assertEqual(self.campaign.firebase_message_id, 'projects/mock/messages/123')

    @patch('notifications.services.send_topic_notification')
    def test_already_sent_campaign_can_be_resent(self, mock_send):
        mock_send.return_value = 'projects/mock/messages/first'
        url = f'/admin/notifications/notificationcampaign/{self.campaign.pk}/send-confirm/'
        self.client.post(url, {'confirm': 'on'})

        mock_send.return_value = 'projects/mock/messages/second'
        response = self.client.post(url, {'confirm': 'on'})

        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.firebase_message_id, 'projects/mock/messages/second')
        self.assertEqual(mock_send.call_count, 2)

    @patch('notifications.services.send_topic_notification')
    def test_firebase_not_configured_marks_campaign_failed(self, mock_send):
        mock_send.side_effect = FirebaseNotConfigured('missing credentials path')

        response = self.client.post(
            f'/admin/notifications/notificationcampaign/{self.campaign.pk}/send-confirm/',
            {'confirm': 'on'},
        )

        self.assertEqual(response.status_code, 302)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, NotificationCampaign.Status.FAILED)
        self.assertIn('missing credentials path', self.campaign.error_message)

    def test_automation_changelist_creates_all_switches(self):
        NotificationAutomation.objects.all().delete()

        response = self.client.get('/admin/notifications/notificationautomation/')

        self.assertEqual(response.status_code, 200)
        # One row per kind except MANUAL.
        self.assertEqual(NotificationAutomation.objects.count(), 5)
        self.assertNotIn(
            NotificationKind.MANUAL,
            NotificationAutomation.objects.values_list('kind', flat=True),
        )


class FirebaseHelperTests(TestCase):
    @patch.dict('os.environ', {}, clear=True)
    def test_missing_credentials_path_raises_firebase_not_configured(self):
        from . import firebase

        firebase._app = None
        with self.assertRaises(FirebaseNotConfigured):
            firebase.get_firebase_app()


class DedupeLedgerTests(TestCase):
    def test_claim_delivery_is_single_use(self):
        from .services import claim_delivery

        self.assertTrue(claim_delivery('inactivity_reminder', 'user:1:2026-08-04'))
        self.assertFalse(claim_delivery('inactivity_reminder', 'user:1:2026-08-04'))
        self.assertEqual(NotificationDelivery.objects.count(), 1)

    def test_same_key_different_kind_is_allowed(self):
        from .services import claim_delivery

        self.assertTrue(claim_delivery('inactivity_reminder', 'user:1'))
        self.assertTrue(claim_delivery('new_course_launch', 'user:1'))
