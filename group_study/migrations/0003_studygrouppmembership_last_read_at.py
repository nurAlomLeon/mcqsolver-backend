from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('group_study', '0002_groupmessage'),
    ]

    operations = [
        migrations.AddField(
            model_name='studygroupmembership',
            name='last_read_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
