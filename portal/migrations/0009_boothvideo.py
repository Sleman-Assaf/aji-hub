# portal/migrations/0009_boothvideo.py
from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import portal.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('portal', '0008_ceo_handbook'),
    ]

    operations = [
        migrations.CreateModel(
            name='BoothVideo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('video', models.FileField(upload_to=portal.models._booth_video_path, validators=[django.core.validators.FileExtensionValidator(allowed_extensions=['mp4', 'webm', 'mov', 'm4v'])])),
                ('caption', models.CharField(blank=True, max_length=300)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('album', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='videos', to='portal.boothalbum')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['uploaded_at'],
            },
        ),
    ]
