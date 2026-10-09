# portal/migrations/0005_boothphoto.py
from django.db import migrations, models
import django.db.models.deletion
import portal.models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0004_brandcolour_boothalum'),
    ]

    operations = [
        migrations.CreateModel(
            name='BoothPhoto',
            fields=[
                ('id',          models.BigAutoField(auto_created=True, primary_key=True)),
                ('image',       models.ImageField(upload_to=portal.models._booth_photo_path)),
                ('caption',     models.CharField(blank=True, max_length=300)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('album',       models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                   related_name='photos',
                                                   to='portal.boothalbum')),
            ],
            options={'ordering': ['uploaded_at']},
        ),
    ]
