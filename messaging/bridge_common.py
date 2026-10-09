# messaging/bridge_common.py
"""What Django and the media bridge agree on: where it is, and the password.

Two processes, one file. The bridge is started with `manage.py
run_media_bridge`, so both sides load the same settings — the control token is
derived from SECRET_KEY rather than being one more secret to keep in step, and
the URL and room size come from the MEDIA_BRIDGE setting with defaults that
suit one machine running both.
"""
import hashlib

from django.conf import settings


def bridge_conf():
    return getattr(settings, 'MEDIA_BRIDGE', None) or {}


def bridge_url():
    """Where Django reaches the bridge's control API. Localhost by default —
    the control plane never needs to leave the machine; the media itself goes
    over the bridge's own UDP sockets, not this."""
    return (bridge_conf().get('url') or 'http://127.0.0.1:8100').rstrip('/')


def bridge_max_participants():
    """The room size with the bridge carrying the call. Not a mesh, so the
    per-person cost no longer grows with the room — this bound is about the
    server's CPU, mostly the screen-share encodes."""
    return int(_cfg()['routing'].get('bridge_max_participants', 64))


def bridge_carries_p2p():
    """Whether direct calls also go through the bridge when it is running.

    On by default, and for a networking reason rather than a capacity one: a
    mesh call needs a route between the two computers themselves, and office
    networks are full of places that route does not exist — Wi-Fi client
    isolation, VLANs between sites, people on the VPN. Everyone can reach the
    server (they loaded the Hub from it), so a call carried by the server
    works wherever the Hub does. The cost to the server of a two-person call
    is two Opus encodes — nothing. Set "carry_p2p": false in MEDIA_BRIDGE to
    keep direct calls browser-to-browser anyway.
    """
    return bool(_cfg()['routing'].get('carry_p2p', True))


def _cfg():
    """The admin's saved configuration, or the settings.py defaults when the
    page has never been touched — see call_config.effective(). Imported
    lazily so this module keeps working in the bridge process before the
    config table has been migrated."""
    from .call_config import effective
    return effective()


def bridge_advertise_ips():
    """The server addresses the bridge advertises in its ICE answers.

    Empty means all of them, which is fine on a single-NIC server. On a
    multi-homed one (second NIC, Hyper-V or VPN adapters) it is the cause of
    one-way audio across subnets: the browser and the bridge can settle on
    different address pairs, each direction of audio rides a different UDP
    path, and an inter-VLAN ACL that passes only one of them kills the other.
    Set "advertise_ip" in MEDIA_BRIDGE to the one address every subnet
    reaches — the same one staff load the Hub from — and both sides are left
    with a single path they must agree on.
    """
    raw = _cfg()['transport'].get('advertise_ip') or []
    if isinstance(raw, str):
        raw = raw.split(',')
    return [ip.strip() for ip in raw if ip and str(ip).strip()]


def bridge_token():
    """The control API's password, same derivation on both sides."""
    return hashlib.sha256(
        f'aji-media-bridge:{settings.SECRET_KEY}'.encode()).hexdigest()
