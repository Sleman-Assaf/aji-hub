# portal/migrations/0004_brandcolour_boothalum.py
from django.db import migrations, models
import portal.models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0003_brand_templates'),
    ]

    operations = [
        migrations.CreateModel(
            name='BrandColour',
            fields=[
                ('id',    models.BigAutoField(auto_created=True, primary_key=True)),
                ('name',  models.CharField(max_length=100)),
                ('hex',   models.CharField(max_length=7)),
                ('ref',   models.CharField(blank=True, max_length=100)),
                ('light', models.BooleanField(default=False)),
                ('order', models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['order', 'name'], 'verbose_name_plural': 'Brand Colours'},
        ),
        migrations.CreateModel(
            name='BoothAlbum',
            fields=[
                ('id',          models.BigAutoField(auto_created=True, primary_key=True)),
                ('name',        models.CharField(max_length=200)),
                ('period',      models.CharField(blank=True, max_length=100)),
                ('tag',         models.CharField(choices=[('sport','Sport'),('company-wide','Company-wide'),('wellness','Wellness'),('celebration','Celebration'),('recognition','Recognition'),('other','Other')], default='other', max_length=30)),
                ('cover_photo', models.ImageField(blank=True, null=True, upload_to=portal.models._booth_cover_path)),
                ('photo_count', models.PositiveIntegerField(default=0)),
                ('order',       models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['order', '-pk'], 'verbose_name_plural': 'Booth Albums'},
        ),
    ]
