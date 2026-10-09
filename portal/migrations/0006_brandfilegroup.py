# portal/migrations/0006_brandfilegroup.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0005_boothphoto'),
    ]

    operations = [
        migrations.CreateModel(
            name='BrandFileGroup',
            fields=[
                ('id',    models.BigAutoField(auto_created=True, primary_key=True)),
                ('name',  models.CharField(max_length=200)),
                ('order', models.PositiveIntegerField(default=0)),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                related_name='groups', to='portal.brandtemplate')),
            ],
            options={'ordering': ['order', 'name'], 'verbose_name_plural': 'Brand File Groups'},
        ),
        migrations.AddField(
            model_name='brandtemplatefile',
            name='group',
            field=models.ForeignKey(blank=True, null=True,
                                     on_delete=django.db.models.deletion.SET_NULL,
                                     related_name='files', to='portal.brandfilegroup',
                                     help_text='Optional subgroup for arranging files'),
        ),
        migrations.AlterField(
            model_name='brandtemplatefile',
            name='updated_at',
            field=models.DateField(auto_now=True),
        ),
    ]
