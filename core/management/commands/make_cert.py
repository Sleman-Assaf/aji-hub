# core/management/commands/make_cert.py
"""Make, inspect and explain the Hub's development certificate.

    manage.py make_cert                 → make it if it is missing or outdated
    manage.py make_cert --force         → reissue it
    manage.py make_cert --host aji.local --host 10.0.0.5
    manage.py make_cert --explain       → what to hand IT, and what to do with it

runserver_ssl does this on its own at startup, so this command is for the two
occasions that are not a server start: adding a name the machine does not know
it has, and finding the authority's certificate to install on the estate.
"""
from django.core.management.base import BaseCommand

from core import certs


class Command(BaseCommand):
    help = 'Makes the certificate the Hub uses to serve https in development.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Reissue even if the current one still fits.')
        parser.add_argument('--host', dest='hosts', action='append', default=[],
                            help='An extra name or address to cover. May be repeated.')
        parser.add_argument('--explain', action='store_true',
                            help='Print what to do about the browser warning.')

    def handle(self, *args, **options):
        p = certs.paths()

        if options['explain']:
            self.explain(p)
            return

        cert, key, what = certs.ensure(options['hosts'], force=options['force'])
        seen = certs.described(cert)
        verb = {'kept': 'Already good', 'issued': 'Issued',
                'reissued': 'Reissued'}[what]
        self.stdout.write(self.style.SUCCESS(f'{verb}.'))
        self.stdout.write(f'  certificate  {cert}')
        self.stdout.write(f'  private key  {key}')
        self.stdout.write(f'  covers       {", ".join(seen["names"])}')
        self.stdout.write(f'  expires      {seen["expires"]}')
        if what != 'kept':
            self.stdout.write('')
            self.stdout.write('Run "manage.py make_cert --explain" to stop '
                              'browsers warning about it.')

    def explain(self, p):
        w = self.stdout.write
        w(self.style.MIGRATE_HEADING('The browser warning, and how to end it'))
        w('')
        w('The Hub signs its own certificate, so browsers do not know the')
        w('authority behind it and say so. Clicking through the warning works —')
        w('the page is a secure context afterwards and calls will run — but a')
        w('warning people are trained to click through is a bad thing to leave')
        w('in place.')
        w('')
        w('Install this one file as a trusted root and the warning is gone for')
        w('good, on every certificate this ever issues:')
        w('')
        w(self.style.SUCCESS(f'  {p["ca_cert"]}'))
        w('')
        w(self.style.MIGRATE_LABEL('  On one Windows machine'))
        w('    Double-click it → Install Certificate → Local Machine →')
        w('    Place all certificates in the following store →')
        w('    Trusted Root Certification Authorities.')
        w('    Or, in an elevated prompt:')
        w(f'      certutil -addstore -f Root "{p["ca_cert"]}"')
        w('')
        w(self.style.MIGRATE_LABEL('  Across AJi, for IT'))
        w('    Group Policy → Computer Configuration → Policies → Windows')
        w('    Settings → Security Settings → Public Key Policies →')
        w('    Trusted Root Certification Authorities → Import.')
        w('')
        w(self.style.MIGRATE_LABEL('  Never send this one anywhere'))
        w(f'    {p["ca_key"]}')
        w('    Whoever holds it can issue certificates that AJi machines trust.')
        w('    It stays on this machine, and out of source control.')
        w('')
        w(self.style.MIGRATE_LABEL('  The better ending'))
        w('    If AJi runs its own certificate authority, ask IT for a')
        w('    certificate for the Hub\'s hostname and use it directly:')
        w('      manage.py runserver_ssl 0.0.0.0:8443 --cert aji.crt --key aji.key')
        w('    Nothing needs installing then — the estate already trusts it.')
