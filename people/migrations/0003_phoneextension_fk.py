# people/migrations/0003_phoneextension_fk.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0002_phoneextension_office'),
    ]

    operations = [
        migrations.AlterField(
            model_name='phoneextension',
            name='employee',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='phone_extensions',
                to='people.employee',
            ),
        ),
    ]
