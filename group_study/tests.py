from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    GroupMessage,
    GroupStudyAnswer,
    GroupStudyQuestion,
    GroupStudyQuiz,
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

    def _create_quiz(self, published=True):
        now = timezone.now()
        quiz = GroupStudyQuiz.objects.create(
            group=self.group,
            name='Weekly Physics',
            created_by=self.admin,
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
