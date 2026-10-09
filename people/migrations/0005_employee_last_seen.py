# people/migrations/0005_employee_last_seen.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0004_employee_cv_bio'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='last_seen',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
