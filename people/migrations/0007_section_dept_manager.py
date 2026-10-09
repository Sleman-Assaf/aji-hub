# people/migrations/0007_section_dept_manager.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0006_employee_empcode'),
    ]

    operations = [
        # Add manager FK to Department
        migrations.AddField(
            model_name='department',
            name='manager',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='managed_departments',
                to='people.employee',
                help_text='Employee who manages this department',
            ),
        ),
        # Create Section model
        migrations.CreateModel(
            name='Section',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=200)),
                ('department', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='sections',
                    to='people.department',
                )),
                ('head', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='headed_sections',
                    to='people.employee',
                    help_text='Employee who heads this section',
                )),
            ],
            options={'ordering': ['name'], 'unique_together': {('name', 'department')}},
        ),
        # Add section FK to Employee
        migrations.AddField(
            model_name='employee',
            name='section',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='employees',
                to='people.section',
            ),
        ),
    ]
