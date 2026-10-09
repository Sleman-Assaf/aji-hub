# assist/migrations/0002_assistsession.py
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('assist', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AssistSession',
            fields=[
                ('id',         models.BigAutoField(auto_created=True, primary_key=True)),
                ('name',       models.CharField(blank=True, max_length=200)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('ended_at',   models.DateTimeField(blank=True, null=True)),
                ('is_active',  models.BooleanField(default=True)),
                ('user',       models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                  related_name='assist_sessions',
                                                  to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-started_at']},
        ),
        migrations.AddField(
            model_name='assistmessage',
            name='session',
            field=models.ForeignKey(blank=True, null=True,
                                     on_delete=django.db.models.deletion.CASCADE,
                                     related_name='messages',
                                     to='assist.assistsession'),
        ),
        migrations.AlterField(
            model_name='assistmessage',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
    ]
