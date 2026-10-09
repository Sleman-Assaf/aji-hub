# portal/migrations/0003_brand_templates.py
from django.db import migrations, models
import django.db.models.deletion
import portal.models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0002_policycategory'),
    ]

    operations = [
        migrations.CreateModel(
            name='BrandSection',
            fields=[
                ('id',           models.BigAutoField(auto_created=True, primary_key=True)),
                ('name',         models.CharField(max_length=200)),
                ('section_type', models.CharField(choices=[('templates','Templates'),('brand_assets','Brand Assets'),('other','Other')], default='other', max_length=30)),
                ('order',        models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['order', 'name'], 'verbose_name_plural': 'Brand Sections'},
        ),
        migrations.CreateModel(
            name='BrandTemplate',
            fields=[
                ('id',          models.BigAutoField(auto_created=True, primary_key=True)),
                ('name',        models.CharField(max_length=300)),
                ('description', models.CharField(blank=True, max_length=300)),
                ('preview',     models.ImageField(blank=True, null=True, upload_to='brand/previews/')),
                ('order',       models.PositiveIntegerField(default=0)),
                ('section',     models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                   related_name='templates', to='portal.brandsection')),
            ],
            options={'ordering': ['order', 'name'], 'verbose_name_plural': 'Brand Templates'},
        ),
        migrations.CreateModel(
            name='BrandTemplateFile',
            fields=[
                ('id',         models.BigAutoField(auto_created=True, primary_key=True)),
                ('label',      models.CharField(max_length=300)),
                ('file',       models.FileField(upload_to=portal.models._brand_file_path)),
                ('version',    models.CharField(blank=True, max_length=50)),
                ('updated_at', models.DateField(auto_now=True)),
                ('template',   models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                  related_name='files', to='portal.brandtemplate')),
            ],
            options={'ordering': ['label'], 'verbose_name_plural': 'Brand Template Files'},
        ),
    ]
