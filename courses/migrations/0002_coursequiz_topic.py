from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='coursequiz',
            name='topic',
            field=models.TextField(blank=True, default=''),
        ),
    ]
