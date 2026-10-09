# people/migrations/0006_employee_empcode.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0005_employee_last_seen'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='emp_code',
            field=models.CharField(
                blank=True, db_index=True, max_length=30,
                help_text='Unique employee code from the HR system (e.g. 8001)'
            ),
        ),
    ]
