"""Give `allowed_users` an explicit through model, so a person can be read-only.

The table already exists — Django created it for the implicit many-to-many — and
it already holds rows. So the model is introduced state-only: the project learns
about a table it has been using all along, and not a single row moves. The one
real database operation is the new `role` column.

`role` defaults to 'download', which is what every named person effectively had
before the column existed. Defaulting to 'read' would have been a permission
change wearing a schema change's clothes: everybody who could take a copy
yesterday would quietly stop being able to.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('portal', '0021_policycategory_managers_alter_policycategory_access_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # Nothing to do in the database: this is the table the implicit
            # through model has been writing to since the field was added. The
            # id column is an INTEGER PRIMARY KEY AUTOINCREMENT, which is what
            # AutoField maps to on SQLite — declared as such rather than as the
            # project's BigAutoField default, so the state matches the table.
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='LibraryMember',
                    fields=[
                        # Declared exactly as the model declares it, not as an
                        # auto-created pk — otherwise the state and the model
                        # differ by that flag alone and the project asks forever
                        # for a migration that would change nothing.
                        ('id', models.AutoField(primary_key=True, serialize=False)),
                        ('policycategory', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='memberships', to='portal.policycategory')),
                        ('user', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='library_memberships',
                            to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'db_table': 'portal_policycategory_allowed_users',
                        'verbose_name': 'library member',
                        'unique_together': {('policycategory', 'user')},
                    },
                ),
                migrations.AlterField(
                    model_name='policycategory',
                    name='allowed_users',
                    field=models.ManyToManyField(
                        blank=True,
                        help_text='Individuals, on top of any groups above.',
                        related_name='policy_libraries',
                        through='portal.LibraryMember',
                        to=settings.AUTH_USER_MODEL),
                ),
            ],
        ),
        migrations.AddField(
            model_name='librarymember',
            name='role',
            field=models.CharField(
                choices=[('read', 'Read in the Hub only'),
                         ('download', 'Read and download')],
                default='download', max_length=10),
        ),
    ]
