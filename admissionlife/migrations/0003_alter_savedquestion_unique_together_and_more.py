# Custom migration to fix MySQL duplicate key error when altering SavedQuestion.
# The issue: migration 0002 created a unique_together on (user, question) which
# caused MySQL to auto-create FK index 'admissionlife_savedquestion_user_id_9dda2925'.
# When Django tries to drop the unique_together, it attempts to re-create that same
# FK index first (MySQL behavior), causing a "Duplicate key name" error.
# Fix: Drop the FK index manually before letting Django alter the unique_together.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('admissionlife', '0002_add_question_bank_models'),
        ('api', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Step 1: Drop the duplicate FK index manually so Django can proceed.
        migrations.RunSQL(
            sql="""
                SET @exist := (
                    SELECT COUNT(1)
                    FROM information_schema.statistics
                    WHERE table_schema = DATABASE()
                      AND table_name = 'admissionlife_savedquestion'
                      AND index_name = 'admissionlife_savedquestion_user_id_9dda2925'
                );
                SET @sql := IF(
                    @exist > 0,
                    'ALTER TABLE admissionlife_savedquestion DROP INDEX admissionlife_savedquestion_user_id_9dda2925',
                    'SELECT 1'
                );
                PREPARE stmt FROM @sql;
                EXECUTE stmt;
                DEALLOCATE PREPARE stmt;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),

        # Step 2: Remove the unique_together constraint on SavedQuestion.
        migrations.AlterUniqueTogether(
            name='savedquestion',
            unique_together=set(),
        ),

        # Step 3: Make SavedQuestion.user nullable (to support guest users).
        migrations.AlterField(
            model_name='savedquestion',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='admissionlife_saved_questions',
                to=settings.AUTH_USER_MODEL,
            ),
        ),

        # Step 4: Add guest_user FK to SavedQuestion.
        migrations.AddField(
            model_name='savedquestion',
            name='guest_user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='admissionlife_saved_questions',
                to='api.guestuser',
            ),
        ),

        # Step 5: Make QuizAttempt.user nullable (to support guest users).
        migrations.AlterField(
            model_name='quizattempt',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='admissionlife_quiz_attempts',
                to=settings.AUTH_USER_MODEL,
            ),
        ),

        # Step 6: Add guest_user FK to QuizAttempt.
        migrations.AddField(
            model_name='quizattempt',
            name='guest_user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='admissionlife_quiz_attempts',
                to='api.guestuser',
            ),
        ),

        # Step 7: Make QuestionReport.user nullable (to support guest users).
        migrations.AlterField(
            model_name='questionreport',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='admissionlife_reported_questions',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
