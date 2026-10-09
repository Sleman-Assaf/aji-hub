# people/migrations/0008_employee_office_drop_phoneextension.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0007_section_dept_manager'),
    ]

    operations = [
        # 1. Add office field to Employee
        migrations.AddField(
            model_name='employee',
            name='office',
            field=models.CharField(
                blank=True, max_length=100,
                help_text='Office / site (e.g. AJi Jordan)',
            ),
        ),
        # 2. Copy extension + office data from PhoneExtension → Employee.
        # Written in SQL every backend speaks: the original used SQLite's
        # `LIMIT 1` inside a scalar subquery, which SQL Server rejects — and a
        # migration replays on every new database, including the MSSQL one
        # migrate_to_mssql builds. MIN() says the same thing portably: one
        # value per employee (deterministically the smallest, where the old
        # form took whichever row came first).
        migrations.RunSQL(
            sql="""
                UPDATE people_employee
                SET
                    extension = COALESCE(
                        NULLIF(people_employee.extension, ''),
                        (SELECT MIN(extension) FROM people_phoneextension WHERE employee_id = people_employee.id)
                    ),
                    office = COALESCE(
                        NULLIF(people_employee.office, ''),
                        (SELECT MIN(office) FROM people_phoneextension WHERE employee_id = people_employee.id)
                    )
                WHERE EXISTS (
                    SELECT 1 FROM people_phoneextension WHERE employee_id = people_employee.id
                )
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        # 3. Drop PhoneExtension table
        migrations.DeleteModel(
            name='PhoneExtension',
        ),
    ]
