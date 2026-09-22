from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from notifications.models import DeviceInstallation, Notification, NotificationKind

from .models import (
    GroupMessage,
    GroupStudyAnswer,
    GroupStudyQuestion,
    GroupStudyQuiz,
    GroupStudyQuizAttempt,
    StudyGroup,
    StudyGroupMembership,
)

User = get_user_model()


class GroupStudyApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='alice',
            email='alice@example.com',
            password='pw12345!',
        )
        self.member = User.objects.create_user(
            username='bob',
            email='bob@example.com',
            password='pw12345!',
        )
        self.group = StudyGroup.objects.create(name='Physics', created_by=self.admin)
        StudyGroupMembership.objects.create(
            group=self.group,
            user=self.admin,
            role=StudyGroupMembership.Role.ADMIN,
        )
        StudyGroupMembership.objects.create(group=self.group, user=self.member)

        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.member_client = APIClient()
        self.member_client.force_authenticate(self.member)

    def _group_url(self):
        return f'/api/group-study/groups/{self.group.id}/'

    def _create_quiz(self, published=True, created_by=None):
        now = timezone.now()
        quiz = GroupStudyQuiz.objects.create(
            group=self.group,
            name='Weekly Physics',
            created_by=created_by or self.admin,
            start_at=now,
            end_at=now + timedelta(hours=1),
            duration_minutes=15,
            is_published=published,
        )
        question = GroupStudyQuestion.objects.create(
            quiz=quiz,
            position=1,
            question_text='2 + 2?',
            explanation='Four is correct.',
        )
        GroupStudyAnswer.objects.create(
            question=question,
            position=1,
            text='4',
            is_correct=True,
        )
        GroupStudyAnswer.objects.create(
            question=question,
            position=2,
            text='5',
            is_correct=False,
        )
        return quiz

    def _completed_attempt(self, quiz, user, score, completed_at=None):
        return GroupStudyQuizAttempt.objects.create(
            user=user,
            quiz=quiz,
            expires_at=timezone.now(),
            end_time=completed_at or timezone.now(),
            score=score,
            is_completed=True,
            total_questions=1,
            correct_mark=1,
            wrong_mark=0,
            unanswered_mark=0,
        )

    def test_group_detail_returns_members(self):
        response = self.member_client.get(self._group_url())
        self.assertEqual(response.status_code, 200)
        self.assertIn('members', response.data)
        self.assertEqual(len(response.data['members']), 2)
        usernames = {m['user']['username'] for m in response.data['members']}
        self.assertEqual(usernames, {'alice', 'bob'})
        self.assertEqual(response.data['member_count'], 2)

    def test_group_list_counts_all_members(self):
        response = self.admin_client.get('/api/group-study/groups/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['member_count'], 2)

    def test_unread_message_count_and_mark_read(self):
        GroupMessage.objects.create(
            group=self.group,
            sender=self.admin,
            body='Hello everyone',
        )
        GroupMessage.objects.create(
            group=self.group,
            sender=self.member,
            body='My own message',
        )

        response = self.member_client.get(self._group_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['unread_message_count'], 1)

        response = self.member_client.post(
            f'{self._group_url()}messages/read/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['unread_message_count'], 0)

        response = self.member_client.get(self._group_url())
        self.assertEqual(response.data['unread_message_count'], 0)

        GroupMessage.objects.create(
            group=self.group,
            sender=self.admin,
            body='A newer message',
        )
        response = self.member_client.get(self._group_url())
        self.assertEqual(response.data['unread_message_count'], 1)

    def test_group_detail_exposes_chat_notification_preference(self):
        response = self.member_client.get(self._group_url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['my_notify_messages'])

    def test_member_can_toggle_chat_notifications(self):
        notification_url = f'{self._group_url()}messages/notifications/'

        response = self.member_client.post(
            notification_url,
            {'notify_messages': False},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['notify_messages'])
        self.assertFalse(
            StudyGroupMembership.objects.get(
                group=self.group,
                user=self.member,
            ).notify_messages
        )
        # The setting is per member and must not touch other memberships.
        self.assertTrue(
            StudyGroupMembership.objects.get(
                group=self.group,
                user=self.admin,
            ).notify_messages
        )

        response = self.member_client.get(self._group_url())
        self.assertFalse(response.data['my_notify_messages'])

        response = self.member_client.post(
            notification_url,
            {'notify_messages': True},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['notify_messages'])

        response = self.member_client.post(notification_url, {}, format='json')
        self.assertEqual(response.status_code, 400)

    @patch('notifications.services.send_token_notifications')
    def test_muted_member_does_not_receive_message_notifications(self, mock_send):
        mock_send.return_value = (1, 0, [])
        DeviceInstallation.objects.create(
            user=self.admin,
            token='token-admin',
            is_active=True,
        )
        DeviceInstallation.objects.create(
            user=self.member,
            token='token-member',
            is_active=True,
        )

        self.member_client.post(
            f'{self._group_url()}messages/notifications/',
            {'notify_messages': False},
            format='json',
        )
        response = self.admin_client.post(
            f'{self._group_url()}messages/',
            {'body': 'Are you there?'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(
            Notification.objects.filter(
                user=self.member,
                kind=NotificationKind.GROUP_STUDY_MESSAGE,
            ).exists()
        )

        # Turning notifications back on restores delivery.
        self.member_client.post(
            f'{self._group_url()}messages/notifications/',
            {'notify_messages': True},
            format='json',
        )
        response = self.admin_client.post(
            f'{self._group_url()}messages/',
            {'body': 'Second message'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            Notification.objects.filter(
                user=self.member,
                kind=NotificationKind.GROUP_STUDY_MESSAGE,
            ).exists()
        )

    def test_sending_a_message_clears_the_senders_own_unread_count(self):
        GroupMessage.objects.create(
            group=self.group,
            sender=self.admin,
            body='First',
        )
        response = self.member_client.post(
            f'{self._group_url()}messages/',
            {'body': 'Reply'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)

        response = self.member_client.get(self._group_url())
        self.assertEqual(response.data['unread_message_count'], 0)

        # The admin still has the member's reply unread.
        response = self.admin_client.get(self._group_url())
        self.assertEqual(response.data['unread_message_count'], 1)

    def test_quiz_list_omits_questions_and_reports_counts(self):
        self._create_quiz()
        response = self.admin_client.get(
            f'{self._group_url()}quizzes/',
        )
        self.assertEqual(response.status_code, 200)
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['question_count'], 1)
        self.assertEqual(results[0]['maximum_score'], 1)
        self.assertNotIn('questions', results[0])

    def test_quiz_detail_includes_questions_for_admins(self):
        quiz = self._create_quiz()
        response = self.admin_client.get(
            f'/api/group-study/quizzes/{quiz.id}/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['questions']), 1)
        self.assertTrue(
            response.data['questions'][0]['answers'][0]['is_correct'],
        )

    def test_members_do_not_see_draft_quizzes(self):
        self._create_quiz(published=True)
        self._create_quiz(published=False)

        admin_response = self.admin_client.get(f'{self._group_url()}quizzes/')
        member_response = self.member_client.get(
            f'{self._group_url()}quizzes/',
        )

        self.assertEqual(admin_response.data['count'], 2)
        self.assertEqual(member_response.data['count'], 1)

    def test_member_sees_and_manages_their_own_draft_quiz(self):
        quiz = self._create_quiz(published=False, created_by=self.member)

        response = self.member_client.get(f'{self._group_url()}quizzes/')
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], quiz.id)
        self.assertEqual(
            response.data['results'][0]['created_by']['username'],
            'bob',
        )

        response = self.member_client.get(
            f'/api/group-study/quizzes/{quiz.id}/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('questions', response.data)

        response = self.member_client.post(
            f'/api/group-study/quizzes/{quiz.id}/publish/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_published'])

    def test_member_can_create_a_quiz(self):
        now = timezone.now()
        response = self.member_client.post(
            f'{self._group_url()}quizzes/',
            {
                'name': 'Member quiz',
                'start_at': now.isoformat(),
                'end_at': (now + timedelta(hours=1)).isoformat(),
                'questions': [
                    {
                        'question_text': 'Capital of France?',
                        'answers': [
                            {'text': 'Paris', 'is_correct': True},
                            {'text': 'Rome', 'is_correct': False},
                        ],
                    },
                ],
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['created_by']['username'], 'bob')
        self.assertEqual(response.data['question_count'], 1)

    def test_member_cannot_manage_another_members_quiz(self):
        quiz = self._create_quiz(published=False)

        response = self.member_client.patch(
            f'/api/group-study/quizzes/{quiz.id}/',
            {'name': 'Hijacked'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

        response = self.member_client.post(
            f'/api/group-study/quizzes/{quiz.id}/publish/',
        )
        self.assertEqual(response.status_code, 403)

        response = self.member_client.delete(
            f'/api/group-study/quizzes/{quiz.id}/',
        )
        self.assertEqual(response.status_code, 403)

    def test_group_leaderboard_sums_best_score_per_quiz(self):
        first = self._create_quiz()
        second = self._create_quiz()

        # Bob: 10 + 4 = 14 across two quizzes; an older 6 doesn't count.
        self._completed_attempt(first, self.member, 10)
        self._completed_attempt(first, self.member, 6)
        self._completed_attempt(second, self.member, 4)
        # Alice: 8 + 8 = 16.
        self._completed_attempt(first, self.admin, 8)
        self._completed_attempt(second, self.admin, 8)

        response = self.member_client.get(
            f'{self._group_url()}leaderboard/',
        )
        self.assertEqual(response.status_code, 200)
        results = response.data
        self.assertEqual([entry['user']['username'] for entry in results], [
            'alice',
            'bob',
        ])
        self.assertEqual(results[0]['score'], 16)
        self.assertEqual(results[0]['quizzes_attempted'], 2)
        self.assertEqual(results[1]['score'], 14)
        self.assertFalse(results[0]['is_current_user'])
        self.assertTrue(results[1]['is_current_user'])
        self.assertEqual([entry['rank'] for entry in results], [1, 2])

    def test_quiz_leaderboard_uses_best_attempt(self):
        quiz = self._create_quiz()
        self._completed_attempt(quiz, self.member, 4)
        self._completed_attempt(quiz, self.member, 9)
        self._completed_attempt(quiz, self.admin, 7)

        response = self.member_client.get(
            f'/api/group-study/quizzes/{quiz.id}/leaderboard/',
        )
        self.assertEqual(response.status_code, 200)
        results = response.data
        self.assertEqual([entry['user']['username'] for entry in results], [
            'bob',
            'alice',
        ])
        self.assertEqual(results[0]['score'], 9)
        self.assertEqual(results[0]['rank'], 1)
        self.assertTrue(results[0]['is_current_user'])

    def test_external_users_cannot_read_leaderboards(self):
        outsider = User.objects.create_user(
            username='eve',
            email='eve@example.com',
            password='pw12345!',
        )
        outsider_client = APIClient()
        outsider_client.force_authenticate(outsider)
        quiz = self._create_quiz()

        response = outsider_client.get(f'{self._group_url()}leaderboard/')
        self.assertEqual(response.status_code, 403)
        response = outsider_client.get(
            f'/api/group-study/quizzes/{quiz.id}/leaderboard/',
        )
        self.assertEqual(response.status_code, 403)

    # ── Public / private groups ──────────────────────────────────────────────

    def _create_public_group(self, name='Public Physics', is_active=True):
        return StudyGroup.objects.create(
            name=name,
            created_by=self.admin,
            is_public=True,
            is_active=is_active,
        )

    def test_public_group_can_be_joined(self):
        group = self._create_public_group()
        url = f'/api/group-study/groups/{group.id}/join/'

        response = self.member_client.post(url)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['is_public'])
        self.assertTrue(
            StudyGroupMembership.objects.filter(
                group=group,
                user=self.member,
            ).exists()
        )

        # Joining twice is safe and stays a member.
        response = self.member_client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            StudyGroupMembership.objects.filter(
                group=group,
                user=self.member,
            ).count(),
            1,
        )

        response = self.member_client.get(
            f'/api/group-study/groups/{group.id}/',
        )
        self.assertEqual(response.status_code, 200)

    def test_private_group_cannot_be_joined(self):
        response = self.member_client.post(f'{self._group_url()}join/')
        self.assertEqual(response.status_code, 403)

    def test_inactive_public_group_cannot_be_joined(self):
        group = self._create_public_group(is_active=False)
        response = self.member_client.post(
            f'/api/group-study/groups/{group.id}/join/',
        )
        self.assertEqual(response.status_code, 400)

    def test_public_group_discovery_filters_and_searches(self):
        public = self._create_public_group(name='Physics Public')
        self._create_public_group(name='Physics Hidden', is_active=False)
        StudyGroup.objects.create(
            name='Physics Secret',
            created_by=self.admin,
        )

        response = self.member_client.get('/api/group-study/groups/public/')
        self.assertEqual(response.status_code, 200)
        results = response.data['results']
        self.assertEqual([group['name'] for group in results], ['Physics Public'])
        self.assertFalse(results[0]['is_member'])
        self.assertTrue(results[0]['is_public'])

        response = self.member_client.get(
            '/api/group-study/groups/public/?search=Phys',
        )
        self.assertEqual(
            [group['name'] for group in response.data['results']],
            ['Physics Public'],
        )

        # Prefix search only: a middle-of-the-name match returns nothing.
        response = self.member_client.get(
            '/api/group-study/groups/public/?search=ysics',
        )
        self.assertEqual(response.data['results'], [])

        self.member_client.post(f'/api/group-study/groups/{public.id}/join/')
        response = self.member_client.get('/api/group-study/groups/public/')
        self.assertTrue(response.data['results'][0]['is_member'])
        response = self.member_client.get('/api/group-study/groups/')
        self.assertIn(
            'Physics Public',
            [group['name'] for group in response.data['results']],
        )

    # ── Membership rules ─────────────────────────────────────────────────────

    def test_any_member_can_add_members(self):
        outsider = User.objects.create_user(
            username='carol',
            email='carol@example.com',
            password='pw12345!',
        )
        response = self.member_client.post(
            f'{self._group_url()}members/',
            {'emails': ['carol@example.com']},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['added_count'], 1)
        self.assertTrue(
            StudyGroupMembership.objects.filter(
                group=self.group,
                user=outsider,
            ).exists()
        )

    def test_re_adding_an_admin_does_not_demote_them(self):
        response = self.member_client.post(
            f'{self._group_url()}members/',
            {'emails': ['alice@example.com']},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['already_member_count'], 1)
        self.assertEqual(
            StudyGroupMembership.objects.get(
                group=self.group,
                user=self.admin,
            ).role,
            StudyGroupMembership.Role.ADMIN,
        )

    def test_member_cannot_remove_members(self):
        membership = StudyGroupMembership.objects.get(
            group=self.group,
            user=self.admin,
        )
        response = self.member_client.delete(
            f'{self._group_url()}members/{membership.id}/',
        )
        self.assertEqual(response.status_code, 403)

    def test_member_can_leave_group(self):
        outsider = User.objects.create_user(
            username='carol',
            email='carol@example.com',
            password='pw12345!',
        )
        StudyGroupMembership.objects.create(group=self.group, user=outsider)
        outsider_client = APIClient()
        outsider_client.force_authenticate(outsider)

        response = outsider_client.post(f'{self._group_url()}leave/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            StudyGroupMembership.objects.filter(
                group=self.group,
                user=outsider,
            ).exists()
        )
        response = outsider_client.get(self._group_url())
        self.assertEqual(response.status_code, 404)

    def test_admin_cannot_leave_group(self):
        response = self.admin_client.post(f'{self._group_url()}leave/')
        self.assertEqual(response.status_code, 400)

    def test_members_list_is_paginated(self):
        response = self.member_client.get(f'{self._group_url()}members/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(len(response.data['results']), 2)
        self.assertNotIn('members', response.data)

    # ── Group settings ───────────────────────────────────────────────────────

    def test_admin_can_update_group_settings(self):
        response = self.admin_client.put(
            self._group_url(),
            {
                'name': self.group.name,
                'description': self.group.description,
                'is_active': True,
                'is_public': True,
                'members_can_create_quizzes': False,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertTrue(self.group.is_public)
        self.assertFalse(self.group.members_can_create_quizzes)
        # The response is the full group detail, not the short write payload.
        self.assertIn('members', response.data)
        self.assertIn('created_by', response.data)
        self.assertIn('member_count', response.data)
        self.assertIn('my_role', response.data)
        self.assertIn('created_at', response.data)
        self.assertTrue(response.data['is_public'])
        self.assertFalse(response.data['members_can_create_quizzes'])

    def test_member_cannot_update_group_settings(self):
        response = self.member_client.put(
            self._group_url(),
            {
                'name': 'Hijacked',
                'description': '',
                'is_active': True,
                'is_public': True,
                'members_can_create_quizzes': False,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 403)

    def test_exam_creation_follows_group_setting(self):
        now = timezone.now()
        payload = {
            'name': 'Restricted exam',
            'start_at': now.isoformat(),
            'end_at': (now + timedelta(hours=1)).isoformat(),
            'questions': [
                {
                    'question_text': 'Capital of France?',
                    'answers': [
                        {'text': 'Paris', 'is_correct': True},
                        {'text': 'Rome', 'is_correct': False},
                    ],
                },
            ],
        }

        response = self.member_client.post(
            f'{self._group_url()}quizzes/',
            payload,
            format='json',
        )
        self.assertEqual(response.status_code, 201)

        self.group.members_can_create_quizzes = False
        self.group.save(
            update_fields=['members_can_create_quizzes', 'updated_at'],
        )

        response = self.member_client.post(
            f'{self._group_url()}quizzes/',
            payload,
            format='json',
        )
        self.assertEqual(response.status_code, 403)

        # Admins can always create exams.
        response = self.admin_client.post(
            f'{self._group_url()}quizzes/',
            payload,
            format='json',
        )
        self.assertEqual(response.status_code, 201)

    # ── Query budget (no N+1 / GROUP BY regressions) ─────────────────────────

    def test_group_list_query_count_stays_flat(self):
        self._create_quiz()
        with self.assertNumQueries(2):
            response = self.member_client.get('/api/group-study/groups/')
        self.assertEqual(response.status_code, 200)

    def test_public_group_discovery_query_count_stays_flat(self):
        self._create_public_group()
        with self.assertNumQueries(2):
            response = self.member_client.get('/api/group-study/groups/public/')
        self.assertEqual(response.status_code, 200)

    def test_quiz_list_query_count_stays_flat(self):
        self._create_quiz()
        # group lookup + membership + pagination count + page query.
        with self.assertNumQueries(4):
            response = self.admin_client.get(f'{self._group_url()}quizzes/')
        self.assertEqual(response.status_code, 200)
