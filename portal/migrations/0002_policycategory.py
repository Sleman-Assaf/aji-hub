# portal/migrations/0002_policycategory.py
from django.db import migrations, models
import django.db.models.deletion


def seed_categories(apps, schema_editor):
    PolicyCategory = apps.get_model('portal', 'PolicyCategory')
    Policy = apps.get_model('portal', 'Policy')

    # Clear old string-based category values to avoid FK violations
    Policy.objects.all().update(category=None)

    # Seed categories matching the prototype
    cats = [
        ('Risk Assessment',            'Project & operational risk',              1),
        ('Cybersecurity Governance',   'IT security & data handling',             2),
        ('QHSE Policy',                'Quality, health, safety & environment',   3),
        ('Environmental Aspects',      'ISO 14001 impacts',                       4),
        ('HR Policies',                'Employment, leave & conduct',             5),
        ('Finance & Procurement',      'Budgets, approvals & purchasing',         6),
        ('IT & Digital',               'Systems, access & data management',       7),
        ('Health & Safety',            'Site safety & emergency procedures',      8),
    ]
    for name, desc, order in cats:
        PolicyCategory.objects.get_or_create(name=name, defaults={'description': desc, 'order': order})


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0001_initial'),
    ]

    operations = [
        # 1. Null out the old string category column first
        migrations.AlterField(
            model_name='policy',
            name='category',
            field=models.CharField(max_length=100, blank=True, null=True),
        ),
        # 2. Create PolicyCategory table
        migrations.CreateModel(
            name='PolicyCategory',
            fields=[
                ('id',          models.BigAutoField(auto_created=True, primary_key=True)),
                ('name',        models.CharField(max_length=200)),
                ('description', models.CharField(blank=True, max_length=300)),
                ('order',       models.PositiveIntegerField(default=0)),
            ],
            options={'ordering': ['order', 'name'], 'verbose_name_plural': 'Policy Categories'},
        ),
        # 3. Add external_url
        migrations.AddField(
            model_name='policy',
            name='external_url',
            field=models.URLField(blank=True),
        ),
        # 4. Add temp FK column (nullable)
        migrations.AddField(
            model_name='policy',
            name='category_fk',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='policies',
                to='portal.policycategory',
            ),
        ),
        # 5. Seed categories + clear old values
        migrations.RunPython(seed_categories, migrations.RunPython.noop),
        # 6. Remove old string category column
        migrations.RemoveField(model_name='policy', name='category'),
        # 7. Rename temp FK to category
        migrations.RenameField(model_name='policy', old_name='category_fk', new_name='category'),
    ]
