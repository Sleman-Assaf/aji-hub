# messaging/management/commands/run_media_bridge.py
"""Start the AJi Media Bridge — the media server that carries every call,
direct and group, and lifts group calls past the 8-person mesh limit.

Run beside Waitress as its own process (a second scheduled task or NSSM
service on the Windows server):

    python manage.py run_media_bridge

Nothing else to configure. Django checks the bridge's health before routing
any call through it, so starting this process is what puts calls on the
server and stopping it is what falls everything back to the mesh — mid-call
participants keep their bridge connection either way; the mode is decided
per call, when it starts.
"""
from django.core.management.base import BaseCommand

from messaging.bridge_common import (bridge_advertise_ips, bridge_token,
                                     bridge_url)


class Command(BaseCommand):
    help = ('Run the media bridge that carries all voice calls and screen '
            'shares (direct and group) and lifts group calls past the '
            '8-person mesh limit. --log-level debug for a full trace.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--host', default='127.0.0.1',
            help='Control API bind address (default 127.0.0.1 — the media '
                 'itself travels over its own UDP sockets, not this port).')
        parser.add_argument('--port', type=int, default=None,
                            help='Control API port (default: from the '
                                 'MEDIA_BRIDGE setting, 8100).')
        parser.add_argument(
            '--advertise-ip', action='append', default=None, metavar='IP',
            help='Server address to advertise in ICE answers (repeatable). '
                 'Defaults to MEDIA_BRIDGE["advertise_ip"]. On a server '
                 'with more than one interface, set this to the one address '
                 'every subnet reaches — the same one staff load the Hub '
                 'from — or calls between subnets can end up one-way.')
        parser.add_argument(
            '--log-level', default='info',
            choices=['debug', 'info', 'warning'],
            help='Terminal verbosity. info (default) traces every join, '
                 'leave, ICE/connection state change and a per-room '
                 'bandwidth line every few seconds; debug adds per-signal '
                 'and per-frame detail for chasing a specific problem.')

    def handle(self, *args, **opts):
        from messaging import media_bridge   # imports aiortc: fail loudly here
        port = opts['port']
        if port is None:
            # The port Django will call is the port to listen on.
            port = int(bridge_url().rsplit(':', 1)[-1].strip('/') or 8100)
        advertise = opts['advertise_ip'] or bridge_advertise_ips()
        # Screen-share encoder settings from IT ▸ Call settings, read once at
        # startup — the page tells the admin these need a bridge restart.
        from messaging.call_config import effective
        screen = effective()['screen']
        media_bridge.run(bridge_token(), host=opts['host'], port=port,
                         level=opts['log_level'], advertise=advertise,
                         screen_max_w=screen.get('bridge_max_width'),
                         screen_fps=screen.get('bridge_fps'))
