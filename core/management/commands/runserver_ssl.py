# core/management/commands/runserver_ssl.py
"""The development server, over https.

    manage.py runserver_ssl                 → https://127.0.0.1:8443/
    manage.py runserver_ssl 0.0.0.0:8443    → and to everybody on the network
    manage.py runserver_ssl 172.12.0.190:8443

Django's own runserver speaks plain http and has no option for anything else,
which is a problem the moment the Hub is opened by its address rather than as
localhost: browsers only give a page a microphone or a screen on a secure
origin, so voice calls and screen sharing are simply unavailable over http.
This is that server with TLS in front of it, and a certificate it makes for
itself — see core/certs.py.

It is Django's runserver in every other respect, autoreloader included, and it
inherits from it rather than reimplementing it: two hooks are all that differ.
`protocol` is what the startup banner prints, and `server_cls` is the socket.

The same caveats apply as to runserver — one process, a toy server, not for
production. What changes for production is what serves the Hub, not this file.
"""
import ssl

from django.core.management.commands.runserver import (
    Command as RunserverCommand, naiveip_re,
)
from django.core.servers.basehttp import WSGIServer


class SecureWSGIServer(WSGIServer):
    """The development server's socket, wrapped in TLS.

    The wrapping happens per connection rather than on the listening socket. It
    is the difference between a browser that speaks plain http to an https port
    — which people do, constantly, by leaving the s off — being dropped, and it
    taking the whole server down: socketserver catches OSError from get_request
    and carries on, and ssl.SSLError is one.
    """

    ssl_context = None                     # set by the command, per run

    def get_request(self):
        sock, addr = super().get_request()
        return self.ssl_context.wrap_socket(sock, server_side=True), addr

    def server_bind(self):
        super().server_bind()
        # Django decides whether a request is secure from wsgi.url_scheme, and
        # wsgiref works that out from HTTPS in the environment. Without this the
        # Hub would believe it was being served over http while the browser knew
        # otherwise, and CSRF — which compares the Origin header against the
        # scheme and host it thinks it is on — would reject every form on the
        # site. Nothing about the connection would look wrong; posting would
        # just stop working.
        self.base_environ['HTTPS'] = 'on'


class Command(RunserverCommand):
    help = 'Runs the development server over https, with a certificate it makes itself.'

    protocol = 'https'
    default_port = '8443'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--cert', dest='cert_file',
            help='A certificate to use instead of the generated one — a real '
                 'one from AJi\'s CA, for instance. Requires --key.')
        parser.add_argument(
            '--key', dest='key_file',
            help='The private key for --cert.')
        parser.add_argument(
            '--host', dest='extra_hosts', action='append', default=[],
            help='An extra name or address the certificate should cover. May be '
                 'repeated. The machine\'s own names and addresses are always '
                 'included.')
        parser.add_argument(
            '--new-cert', action='store_true', dest='force_cert',
            help='Reissue the certificate even if the current one still fits.')

    def handle(self, *args, **options):
        cert, key = options.get('cert_file'), options.get('key_file')
        if bool(cert) != bool(key):
            from django.core.management.base import CommandError
            raise CommandError('--cert and --key go together.')

        if not cert:
            from core import certs
            # The address being served is one of the names the certificate has
            # to carry, and it is the one the browser will be checking — so it
            # goes in alongside whatever the machine calls itself.
            hosts = list(options.get('extra_hosts') or [])
            # Parsed with Django's own expression rather than split on the last
            # colon, because a bare port is a valid address to give runserver and
            # splitting one leaves the port itself looking like a hostname: the
            # certificate came out naming a machine called "8443".
            match = naiveip_re.match(options.get('addrport') or '')
            if match:
                addr = (match.group('ipv6') or match.group('ipv4')
                        or match.group('fqdn'))
                if addr:
                    hosts.append(addr.strip('[]'))
            cert, key, what = certs.ensure(hosts, force=options.get('force_cert'))
            if what != 'kept':
                seen = certs.described(cert)
                self.stdout.write(self.style.SUCCESS(
                    f'Certificate {what} for: {", ".join(seen["names"])}'))
                self.stdout.write(f'  expires {seen["expires"]}')
                self.stdout.write(
                    '  Browsers will warn until the authority is trusted — '
                    'run "manage.py make_cert --explain" for how.')

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        # run() builds the server class itself and gives it no room for extra
        # arguments, so the context is carried on a subclass made for this run.
        self.server_cls = type('SecureWSGIServer', (SecureWSGIServer,),
                               {'ssl_context': context})
        super().handle(*args, **options)
