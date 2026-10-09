# people/migrations/0002_phoneextension_office.py
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('people', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='phoneextension',
            old_name='location',
            new_name='office',
        ),
        migrations.AlterField(
            model_name='phoneextension',
            name='office',
            field=models.CharField(
                blank=True,
                help_text='AJi Jordan / AJi Studio',
                max_length=100,
            ),
        ),
    ]
