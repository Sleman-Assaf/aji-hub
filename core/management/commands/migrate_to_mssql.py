# core/management/commands/migrate_to_mssql.py
"""Copy the whole Hub from SQLite into SQL Server, table by table.

Run while `mssql.json` has `"enabled": false`: the Hub is still living on
SQLite, and SQL Server is standing by under the 'mssql' alias. The command
builds the schema there (`migrate`), empties whatever `migrate` seeded, then
copies every row of every model — primary keys included, so nothing that
points at anything comes apart — with foreign-key checking switched off for
the duration and verified in one sweep at the end. It finishes with a
row-count comparison of every table on both sides, which is the report that
says the copy is complete.

Everything runs in one transaction on the target: a copy that fails half-way
leaves SQL Server exactly as it was, and the command can simply be run again.
SQLite is only ever read.

Usage, in order:
    1. tools\\install_mssql.ps1        (installs SQL Server, writes mssql.json)
    2. pip install -r requirements.txt (adds mssql-django + pyodbc)
    3. python manage.py migrate_to_mssql
    4. set "enabled": true in mssql.json, restart the Hub

Idempotent: the target is emptied before every copy, so re-running after
fixing a problem — or re-copying fresher data just before the switch-over —
is the normal way to use it, not a special case.
"""
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connections, router, transaction


class Command(BaseCommand):
    help = ('Copy all data from the SQLite database into SQL Server '
            '(configure mssql.json first; see tools/install_mssql.ps1).')

    def add_arguments(self, parser):
        parser.add_argument('--source', default=None,
                            help='Database alias to read from (default: auto).')
        parser.add_argument('--target', default=None,
                            help='Database alias to write to (default: auto).')
        parser.add_argument('--batch', type=int, default=800,
                            help='Rows per insert batch (default 800).')
        parser.add_argument('--yes', action='store_true',
                            help='Do not ask before emptying the target.')

    # ── which two databases ──────────────────────────────────────────────

    def _aliases(self, opts):
        """Work out source and target from how mssql.json is set.

        Before the switch-over the pair is default→mssql; after it (re-running
        to compare, or to re-copy) it is sqlite→default. Explicit --source and
        --target override both.
        """
        if opts['source'] and opts['target']:
            src, tgt = opts['source'], opts['target']
        elif 'mssql' in connections:
            src, tgt = 'default', 'mssql'
        elif 'sqlite' in connections:
            src, tgt = 'sqlite', 'default'
        else:
            raise CommandError(self._why_not_configured())
        for alias in (src, tgt):
            if alias not in connections:
                raise CommandError(f'No database alias "{alias}" in settings.')
        if src == tgt:
            raise CommandError('Source and target are the same database.')
        return src, tgt

    @staticmethod
    def _why_not_configured():
        """Say exactly why mssql.json did not take, not merely that it didn't.

        settings.py ignores a bad file silently — the right behaviour for
        starting the Hub, which must come up on SQLite whatever the file says,
        and the wrong one for this command, whose whole job depends on it.
        """
        import json
        from django.conf import settings
        path = getattr(settings, 'MSSQL_CONFIG_FILE', None)
        if path is None:
            return ('This settings module has no MSSQL support — '
                    'aji_hub/settings.py is missing the MSSQL_CONFIG_FILE '
                    'block.')
        if not path.exists():
            hint = ''
            twins = list(path.parent.glob('mssql.json*'))
            if twins:
                hint = (f'\nFound {twins[0].name} in that folder — Windows '
                        'Notepad often saves "mssql.json.txt" while hiding '
                        'the extension. Rename it to exactly mssql.json.')
            return (f'mssql.json not found at {path} — it must sit beside '
                    f'manage.py. tools/install_mssql.ps1 writes it.{hint}')
        try:
            cfg = json.loads(path.read_text(encoding='utf-8-sig'))
        except ValueError as exc:
            return (f'{path} exists but is not valid JSON ({exc}).\n'
                    'The usual cause on Windows: a single backslash in the '
                    'host. JSON needs it doubled — write '
                    '"host": "localhost\\\\SQLEXPRESS", '
                    'not "localhost\\SQLEXPRESS".')
        if not isinstance(cfg, dict):
            return f'{path} must contain a JSON object, not {type(cfg).__name__}.'
        if not cfg.get('host'):
            return (f'{path} parsed, but has no "host" value — settings.py '
                    'ignores the file without one. Add e.g. '
                    '"host": "localhost\\\\SQLEXPRESS".')
        return (f'{path} looks fine but settings did not pick it up — '
                'was the server restarted / the command run from the '
                'project folder that contains this file?')

    def _models(self, source):
        """Every model with a real table, through-tables of M2Ms included.

        Proxies have no table of their own and unmanaged models are somebody
        else's tables; both are skipped. Ordering does not matter because the
        copy runs with constraint checking off — the check happens once, at
        the end, over everything.
        """
        seen, out = set(), []
        for model in apps.get_models(include_auto_created=True):
            meta = model._meta
            if meta.proxy or not meta.managed:
                continue
            if meta.db_table in seen:
                continue
            if not router.allow_migrate_model(source, model):
                continue
            seen.add(meta.db_table)
            out.append(model)
        return out

    # ── the copy ─────────────────────────────────────────────────────────

    @staticmethod
    def _backend_error(exc):
        """Turn a backend-load failure into the sentence that fixes it.

        The whole chain is inspected, not just the top: Django wraps the real
        error in an ImproperlyConfigured whose text mentions 'mssql' whatever
        actually went wrong, and matching on that once hid a pyodbc DLL
        problem behind advice to install a package that was already there.
        """
        chain, e, seen = [], exc, set()
        while e is not None and id(e) not in seen:
            seen.add(id(e))
            chain.append(f'{type(e).__name__}: {e}')
            e = e.__cause__ or e.__context__
        text = '\n  '.join(chain)

        if "No module named 'mssql'" in text:
            return ('The SQL Server backend is not installed in this '
                    'environment. Run:\n\n'
                    '    pip install "mssql-django>=1.5" pyodbc\n\n'
                    'and try again.')
        if "No module named 'pyodbc'" in text:
            return ('mssql-django is installed but pyodbc is not. Run:\n\n'
                    '    pip install pyodbc\n\nand try again.')
        if 'pyodbc' in text and 'DLL load failed' in text:
            return ('pyodbc is installed but its DLL would not load:\n\n  '
                    + text +
                    '\n\nOn Windows this is one of two missing pieces: the '
                    '"Microsoft Visual C++ 2015-2022 Redistributable (x64)" '
                    'or the "ODBC Driver 18 for SQL Server" — '
                    'tools/install_mssql.ps1 installs the driver. Also check '
                    'the venv Python and the packages are the same '
                    'architecture (both 64-bit).')
        return ('Could not load the SQL Server database backend. The '
                'underlying error:\n\n  ' + text)

    def handle(self, *args, **opts):
        src, tgt = self._aliases(opts)
        try:
            s_conn, t_conn = connections[src], connections[tgt]
        except Exception as exc:
            raise CommandError(self._backend_error(exc))

        batch = max(50, opts['batch'])

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Copying {src} ({s_conn.vendor}) → {tgt} ({t_conn.vendor})'))

        # Reachable, before anything else — a wrong password should say so
        # here, not three steps in.
        try:
            t_conn.ensure_connection()
        except Exception as exc:
            raise CommandError(
                f'Cannot connect to the target database: {exc}\n'
                'Check mssql.json (host, user, password) and that the ODBC '
                'driver named in it is installed.')

        # The schema, from the same migrations SQLite ran. Some of the Hub's
        # data migrations query through the default manager without naming a
        # database, which in a two-database setup would send those queries to
        # SQLite while SQL Server is the one being built — mid-sequence, the
        # two schemas disagree, and the migration crashes on a column that
        # only exists on one side. A router that answers "the target" to every
        # question, installed just for this call, sends them where the
        # migration is actually running.
        self.stdout.write('Building schema on the target (migrate)…')

        class _EverythingTo:
            def __init__(self, alias):
                self.alias = alias

            def db_for_read(self, model, **hints):
                return self.alias

            def db_for_write(self, model, **hints):
                return self.alias

            def allow_relation(self, *args, **kwargs):
                return True

            def allow_migrate(self, *args, **kwargs):
                return None

        previous_routers = router.routers
        router.routers = [_EverythingTo(tgt)]
        try:
            call_command('migrate', database=tgt, interactive=False,
                         verbosity=0)
        finally:
            router.routers = previous_routers

        # The copy reads every column the models declare, so the source has to
        # be up to date with its own migrations — half-way through a table is
        # a bad place to find out it is not.
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(s_conn)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            raise CommandError(
                f'The source database "{src}" has {len(pending)} unapplied '
                f'migration(s). Run `python manage.py migrate` first (with '
                f'mssql.json still disabled), then run this command again.')

        models = self._models(src)
        counts = {m: m.objects.using(src).count() for m in models}
        total = sum(counts.values())

        if not opts['yes']:
            answer = input(
                f'About to EMPTY {len(models)} tables on "{tgt}" and copy '
                f'{total} rows into them. Type yes to continue: ')
            if answer.strip().lower() != 'yes':
                raise CommandError('Cancelled — nothing was changed.')

        qn = t_conn.ops.quote_name
        copied = {}
        with transaction.atomic(using=tgt):
            with t_conn.constraint_checks_disabled():
                # Empty what `migrate` seeded (content types, permissions —
                # their generated ids would collide with the real ones about
                # to arrive) and anything a previous run left.
                with t_conn.cursor() as cur:
                    for model in models:
                        cur.execute(f'DELETE FROM {qn(model._meta.db_table)}')

                for model in models:
                    n = counts[model]
                    label = f'{model._meta.db_table} ({n})'
                    if n == 0:
                        copied[model] = 0
                        self.stdout.write(f'  {label:60s} —')
                        continue
                    done = 0
                    buf = []
                    # .iterator() keeps memory flat however big a table is;
                    # rows are re-inserted with their primary keys, which the
                    # SQL Server backend handles by switching IDENTITY_INSERT
                    # on around the statement.
                    for obj in (model.objects.using(src).order_by('pk')
                                .iterator(chunk_size=batch)):
                        buf.append(obj)
                        if len(buf) >= batch:
                            model.objects.using(tgt).bulk_create(buf, batch_size=batch)
                            done += len(buf)
                            buf = []
                    if buf:
                        model.objects.using(tgt).bulk_create(buf, batch_size=batch)
                        done += len(buf)
                    copied[model] = done
                    self.stdout.write(f'  {label:60s} ✓ {done}')

            # Constraint checking is back on; prove every foreign key that was
            # copied blind actually points at something.
            self.stdout.write('Checking foreign keys…')
            t_conn.check_constraints(
                table_names=[m._meta.db_table for m in models])

        # Identity seeds. Rows arrived with explicit ids, so each table's
        # auto-number must be told where to continue from — otherwise the
        # first new message after the switch-over collides with an old one.
        reseed = t_conn.ops.sequence_reset_sql(no_style(), models)
        if reseed:
            self.stdout.write('Resetting identity seeds…')
            with t_conn.cursor() as cur:
                for sql in reseed:
                    cur.execute(sql)

        # ── the report ───────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('Verification'))
        mismatched = 0
        for model in models:
            got = model.objects.using(tgt).count()
            want = counts[model]
            if got != want:
                mismatched += 1
                self.stdout.write(self.style.ERROR(
                    f'  {model._meta.db_table}: source {want}, target {got}'))
        if mismatched:
            raise CommandError(f'{mismatched} table(s) do not match — '
                               'the copy is not complete.')
        self.stdout.write(self.style.SUCCESS(
            f'Done. {total} rows in {len(models)} tables copied and verified.\n'
            'Next: set "enabled": true in mssql.json and restart the Hub. '
            'Media files (uploads, photos) live on disk, not in the database, '
            'and need no migration.'))
