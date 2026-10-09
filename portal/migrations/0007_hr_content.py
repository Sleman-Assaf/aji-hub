# portal/migrations/0007_hr_content.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0006_brandfilegroup'),
    ]

    operations = [
        migrations.CreateModel(
            name='OnboardingHandbookItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('tab', models.CharField(choices=[
                    ('conduct', 'Code of Conduct'), ('att', 'Attendance'),
                    ('leave', 'Time Off'), ('prof', 'Workplace'),
                    ('comp', 'Compensation'), ('perf', 'Performance'),
                    ('exit', 'Leaving AJi'),
                ], max_length=20)),
                ('title', models.CharField(max_length=200)),
                ('body', models.TextField()),
                ('order', models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['tab', 'order', 'title'], 'verbose_name': 'Onboarding Handbook Item', 'verbose_name_plural': 'Onboarding Handbook Items'},
        ),
        migrations.CreateModel(
            name='WellnessProgram',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=200)),
                ('description', models.TextField()),
                ('period', models.CharField(blank=True, max_length=150)),
                ('order', models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['order', 'name']},
        ),
        migrations.CreateModel(
            name='ChildcarePartner',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=200)),
                ('order', models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['order', 'name']},
        ),
        migrations.CreateModel(
            name='GrowthCertification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=100)),
                ('order', models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['order', 'name']},
        ),
    ]
