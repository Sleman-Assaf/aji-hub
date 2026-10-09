# people/migrations/0004_employee_cv_bio.py
from django.db import migrations, models
import people.models


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0003_phoneextension_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='bio',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='employee',
            name='cv',
            field=models.FileField(
                blank=True,
                null=True,
                help_text='PDF or Word CV',
                upload_to=people.models._emp_media_path('cvs'),
            ),
        ),
        migrations.AlterField(
            model_name='employee',
            name='photo',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=people.models._emp_media_path('photos'),
            ),
        ),
    ]
