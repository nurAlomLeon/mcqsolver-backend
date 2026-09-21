"""Cron entrypoint for all automated notifications.

Intended to run hourly. Each automation self-gates on its admin toggle, its
configured send hour, and a dedupe ledger, so running this more often than
necessary is harmless.

cPanel cron example (adjust the virtualenv path to match your account):

    /home/mcqsolve/virtualenv/qb.mcqsolver.com/3.11/bin/python \
        /home/mcqsolve/qb.mcqsolver.com/manage.py run_notification_automations
"""
from django.core.management.base import BaseCommand

from notifications.automations import RUNNERS, run_all


class Command(BaseCommand):
    help = 'Run enabled automated notifications (inactivity, new course, scheduled).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--only',
            choices=sorted(RUNNERS.keys()),
            help='Run a single automation instead of all of them.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Ignore the send-hour and once-per-day gates (still deduped).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be sent without contacting Firebase.',
        )

    def handle(self, *args, **options):
        results = run_all(
            force=options['force'],
            dry_run=options['dry_run'],
            only=options.get('only'),
        )

        for result in results:
            kind = result.get('kind', 'unknown')
            if result.get('error'):
                self.stderr.write(self.style.ERROR(
                    f'{kind}: error - {result["error"]}'
                ))
            elif result.get('skipped'):
                self.stdout.write(f'{kind}: skipped ({result["skipped"]})')
            else:
                detail = ', '.join(
                    f'{key}={value}'
                    for key, value in result.items()
                    if key != 'kind'
                )
                self.stdout.write(self.style.SUCCESS(f'{kind}: {detail}'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run - nothing was sent.'))
