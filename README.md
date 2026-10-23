# mcqsolver-backend

## Daily Exam

A shared 20-question exam is generated once per Bangladesh calendar date and
runs from **9:00 PM to 11:00 PM Asia/Dhaka**. The exam is only generated when
at least one leaf subcategory has 20 valid questions (at least two options and
exactly one correct answer).

Useful commands:

```bash
# Apply migrations
python manage.py migrate

# Generate/inspect today's exam
python manage.py create_daily_exam --dry-run
python manage.py create_daily_exam

# Force a specific subcategory (before the exam opens, no attempts)
python manage.py create_daily_exam --date 2026-10-19 --category-id 12 --force
```

The **10:00 AM reminder** is part of the existing notification automation:

```bash
python manage.py run_notification_automations --only daily_exam_reminder
```

Cron must run `run_notification_automations` hourly. The daily exam itself is
still generated lazily by the app's first status request, but the reminder only
fires from that cron command. The notification automation is enabled by the
migration and can be switched off in Django admin.

The exam window and duration can be overridden for staging with:

```
DAILY_EXAM_OPEN_HOUR=21
DAILY_EXAM_CLOSE_HOUR=23
DAILY_EXAM_DURATION_MINUTES=20
```
