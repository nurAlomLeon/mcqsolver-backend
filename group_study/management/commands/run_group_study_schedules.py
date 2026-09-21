"""Cron entrypoint for Group Study start/end reminders.

Intended to run every few minutes. Each reminder is deduped through
``notifications.services.claim_delivery`` so overlapping runs never double-notify.

cPanel cron example (adjust the virtualenv path to match your account):

    /home/mcqsolve/virtualenv/qb.mcqsolver.com/3.11/bin/python \
        /home/mcqsolve/qb.mcqsolver.com/manage.py run_group_study_schedules
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from group_study.models import GroupStudyQuiz, GroupStudyQuizAttempt
from notifications.models import NotificationKind
from notifications.services import claim_delivery, send_direct, DeviceInstallation


REMINDER_WINDOW = timedelta(minutes=15)
GROUP_STUDY_ROUTE = '/group-study'


def _notify_user_ids(user_ids, title, body, payload):
    if not user_ids:
        return 0, 0
    installations = DeviceInstallation.objects.filter(
        is_active=True,
        user_id__in=user_ids,
    ).only('token', 'user_id')
    return send_direct(
        installations,
        title=title,
        body=body,
        kind=NotificationKind.GROUP_STUDY_REMINDER,
        route=GROUP_STUDY_ROUTE,
        payload=payload,
        data=payload,
    )


class Command(BaseCommand):
    help = 'Send Group Study start and end reminders for upcoming quizzes.'

    def handle(self, *args, **options):
        now = timezone.now()
        window_end = now + REMINDER_WINDOW

        upcoming = GroupStudyQuiz.objects.filter(
            is_published=True,
            start_at__gt=now,
            start_at__lte=window_end,
        ).select_related('group')
        for quiz in upcoming:
            dedupe_key = f'group-study-quiz-{quiz.id}-start'
            if not claim_delivery(NotificationKind.GROUP_STUDY_REMINDER, dedupe_key):
                continue
            attempted_ids = GroupStudyQuizAttempt.objects.filter(quiz=quiz).values_list('user_id', flat=True)
            target_ids = list(
                quiz.group.memberships.exclude(user_id__in=attempted_ids).values_list('user_id', flat=True)
            )
            payload = {'group_id': quiz.group_id, 'quiz_id': quiz.id}
            _notify_user_ids(
                target_ids,
                title='Group quiz starting soon',
                body=f'"{quiz.name}" starts in {quiz.group.name}. Get ready!',
                payload=payload,
            )
            self.stdout.write(f'start reminder: quiz={quiz.id} recipients={len(target_ids)}')

        ending = GroupStudyQuiz.objects.filter(
            is_published=True,
            end_at__gt=now,
            end_at__lte=window_end,
        ).select_related('group')
        for quiz in ending:
            dedupe_key = f'group-study-quiz-{quiz.id}-end'
            if not claim_delivery(NotificationKind.GROUP_STUDY_REMINDER, dedupe_key):
                continue
            target_ids = list(
                GroupStudyQuizAttempt.objects.filter(
                    quiz=quiz,
                    is_completed=False,
                ).values_list('user_id', flat=True)
            )
            payload = {'group_id': quiz.group_id, 'quiz_id': quiz.id}
            _notify_user_ids(
                target_ids,
                title='Group quiz ending soon',
                body=f'"{quiz.name}" closes soon. Submit your answers now.',
                payload=payload,
            )
            self.stdout.write(f'end reminder: quiz={quiz.id} recipients={len(target_ids)}')

        self.stdout.write(self.style.SUCCESS('Group Study schedule pass complete.'))
