# portal/migrations/0008_ceo_handbook.py
from django.db import migrations, models
import portal.models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0007_hr_content'),
    ]

    operations = [
        migrations.CreateModel(
            name='CEOLetter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('name', models.CharField(default='Hamzah Awwad', max_length=200)),
                ('title', models.CharField(default='CEO · AJi Group', max_length=200)),
                ('photo', models.ImageField(blank=True, null=True, upload_to=portal.models._ceo_photo_path)),
                ('body', models.TextField(default='Dear New Joiner,\n\nI am delighted to extend a heartfelt welcome to you as you embark on this exciting journey with AJi Group.', help_text='Use blank lines to separate paragraphs.')),
                ('sign_off', models.CharField(default='Sincerely, Hamzah Awwad', max_length=200)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'CEO Letter', 'verbose_name_plural': 'CEO Letter'},
        ),
        migrations.CreateModel(
            name='EmployeeHandbookFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('title', models.CharField(default='Employee Handbook', max_length=200)),
                ('description', models.TextField(default="A full overview of AJi's culture, expectations, policies and procedures — your reference for responsibilities, rights, benefits and the standards that guide daily work.")),
                ('file', models.FileField(blank=True, null=True, upload_to=portal.models._handbook_file_path)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'verbose_name': 'Employee Handbook File', 'verbose_name_plural': 'Employee Handbook File'},
        ),
    ]
