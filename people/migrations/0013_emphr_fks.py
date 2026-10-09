# people/migrations/0013_emphr_fks.py
# EmpHR rebuilt: text columns become real foreign keys into the org graph
# (Entity, Department, Section, manager as self-FK). The table is a mirror of
# HR's Excel, re-imported at will, so dropping and recreating it is cheaper
# and safer than field-by-field surgery on disposable rows.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('people', '0012_emphr')]

    operations = [
        migrations.DeleteModel(name='EmpHR'),
        migrations.CreateModel(
            name='EmpHR',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(blank=True, help_text='The unique link to the AD-side account.', max_length=254, null=True, unique=True)),
                ('location', models.CharField(blank=True, max_length=100)),
                ('emp_code', models.CharField(db_index=True, max_length=30)),
                ('full_name', models.CharField(max_length=200)),
                ('division', models.CharField(blank=True, max_length=200)),
                ('site_project', models.CharField(blank=True, max_length=200)),
                ('position', models.CharField(blank=True, max_length=200)),
                ('birth_date', models.DateField(blank=True, null=True)),
                ('country', models.CharField(blank=True, max_length=100)),
                ('manager_code', models.CharField(blank=True, max_length=30)),
                ('manager_name', models.CharField(blank=True, max_length=200)),
                ('imported_at', models.DateTimeField(auto_now=True)),
                ('entity', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hr_rows', to='people.entity')),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hr_rows', to='people.department')),
                ('section', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hr_rows', to='people.section')),
                ('manager', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reports', to='people.emphr')),
            ],
            options={
                'verbose_name': 'HR employee record',
                'verbose_name_plural': 'HR employee records',
                'ordering': ['full_name'],
                'unique_together': {('entity', 'emp_code')},
            },
        ),
    ]
