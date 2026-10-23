"""Generate or inspect a Daily Exam from the command line.

Examples:
    python manage.py create_daily_exam --dry-run
    python manage.py create_daily_exam --date 2026-10-19 --category-id 12
    python manage.py create_daily_exam --date 2026-10-19 --force
"""
from datetime import date as date_type

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from api.daily_exam_services import (
    ensure_daily_exam,
    local_date,
    select_daily_exam_category,
)
from api.models import Category, DailyExam


class Command(BaseCommand):
    help = 'Create the shared Daily Exam for a Dhaka calendar date.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            help='Exam date in YYYY-MM-DD (defaults to today in Asia/Dhaka).',
        )
        parser.add_argument(
            '--category-id',
            type=int,
            help='Force a category instead of picking a random eligible one.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help=(
                'Replace an existing exam. Only allowed before the exam opens '
                'and when no attempts exist.'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be generated without writing anything.',
        )

    def handle(self, *args, **options):
        exam_date = (
            date_type.fromisoformat(options['date'])
            if options.get('date')
            else local_date()
        )
        category = None
        if options.get('category_id'):
            category = Category.objects.filter(pk=options['category_id']).first()
            if category is None:
                raise CommandError('Category not found.')
            if category.parent_id is None:
                raise CommandError(
                    'Choose a subcategory, not a root category.'
                )

        if options['dry_run']:
            chosen = category or select_daily_exam_category()
            if chosen is None:
                self.stdout.write(self.style.WARNING(
                    'No eligible subcategory has enough valid questions.'
                ))
                return
            self.stdout.write(
                f'Would generate daily exam for {exam_date} from '
                f'{chosen.get_full_path()}.'
            )
            return

        existing = DailyExam.objects.filter(exam_date=exam_date).first()
        if existing is not None and options['force']:
            now = timezone.now()
            if existing.attempts.exists():
                raise CommandError(
                    'Cannot replace an exam that already has attempts.'
                )
            if now >= existing.opens_at:
                raise CommandError(
                    'Cannot replace an exam after it has opened.'
                )
            quiz = existing.quiz
            existing.delete()
            quiz.delete()
            existing = None

        if existing is not None:
            self.stdout.write(self.style.WARNING(
                f'Daily exam for {exam_date} already exists '
                f'({existing.category.get_full_path()}).'
            ))
            return

        exam, created = ensure_daily_exam(
            exam_date=exam_date,
            category=category,
            force=True,
        )
        if exam is None:
            raise CommandError(
                'Could not generate an exam: no eligible subcategory with '
                'enough valid questions.'
            )

        state = 'Created' if created else 'Already existed'
        self.stdout.write(self.style.SUCCESS(
            f'{state}: {exam.exam_date} - {exam.category.get_full_path()} '
            f'({exam.quiz.questions.count()} questions, '
            f'{exam.quiz.duration_minutes} minutes).'
        ))
