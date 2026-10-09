# messaging/call_config.py
"""Every knob calling has, in one place, admin-changeable without a deploy.

The defaults are the values that have been shipping all along — settings.py
for the ones that already lived there (MEDIA_BRIDGE, WEBRTC_ICE_SERVERS),
constants for the rest — and the database stores only what an admin has
actually changed on the IT ▸ Call settings page. effective() lays the stored
overrides on top of the defaults, so deleting the row, or never touching the
page, is exactly today's behaviour. That layering is also why the bridge can
import this before its table exists: no row reads as no overrides.

What each knob reaches:

- routing: read by Django on every join, so changes apply to the next call.
- audio and screen: handed to the browser in the join response; the client
  sets codec preferences on its transceivers before offering (which decides
  the codec on both legs, since the answerer can only pick from what was
  offered), writes the Opus fmtp parameters into the SDP it sends (an fmtp
  line governs what the *other* side's encoder sends back), and caps its
  screen sender's bitrate directly.
- transport: ICE servers and policy go to the browser per call; advertise_ip
  and the bridge's screen encoder settings are read by the bridge process at
  startup, which is why the page marks them "bridge restart".

Codec choices are limited to what BOTH ends speak. The bridge (aiortc) does
audio in Opus, PCMU and PCMA and video in VP8 and H.264 — offering the admin
a codec the bridge cannot answer would break every call the moment it was
saved, so those four-plus-one are the whole menu.
"""
import copy
import time

from django.conf import settings

#: What the admin may pick from — the intersection of browser and aiortc.
AUDIO_CODECS = ('opus', 'PCMU', 'PCMA')
SCREEN_CODECS = ('auto', 'VP8', 'H264')
ICE_POLICIES = ('all', 'relay')


def _bridge_setting():
    return getattr(settings, 'MEDIA_BRIDGE', {}) or {}


def defaults():
    """The configuration as it stands with nothing saved — settings.py and
    the constants the code has always used."""
    mb = _bridge_setting()
    adv = mb.get('advertise_ip') or ''
    if isinstance(adv, (list, tuple)):
        adv = ', '.join(adv)
    return {
        'routing': {
            'carry_p2p': bool(mb.get('carry_p2p', True)),
            'bridge_max_participants': int(mb.get('max_participants', 64)),
            'mesh_max_participants': 8,
        },
        'audio': {
            'codec': 'opus',
            'opus_max_kbps': 0,          # 0 = the browser's default (~32)
            'opus_fec': True,            # in-band FEC: rides out packet loss
            'opus_dtx': False,           # silence suppression: saves uplink
            'echo_cancellation': True,
            'noise_suppression': True,
            'auto_gain': True,
        },
        'screen': {
            'codec': 'auto',
            'max_kbps': 0,               # sender cap; 0 = browser decides
            'capture_fps': 15,
            'capture_width': 1920,
            'bridge_max_width': 1440,    # bridge restart
            'bridge_fps': 12,            # bridge restart
        },
        'transport': {
            'ice_servers': list(getattr(settings, 'WEBRTC_ICE_SERVERS', [])
                                or []),
            'ice_policy': 'all',
            'advertise_ip': adv,         # bridge restart
        },
    }


_CACHE = {'at': 0.0, 'data': None}
_TTL = 5.0


def invalidate():
    _CACHE['data'] = None
    _CACHE['at'] = 0.0


def effective():
    """Defaults with the admin's saved overrides on top.

    Cached for a few seconds because Django reads this on every call join
    and every bridge health decision; the save view calls invalidate() so an
    admin's change is live within one page refresh. Any database trouble —
    including the table not existing yet on a box that has not migrated —
    reads as "nothing saved", never as an error: calling must not depend on
    the settings page's table.
    """
    now = time.monotonic()
    if _CACHE['data'] is not None and now - _CACHE['at'] < _TTL:
        return copy.deepcopy(_CACHE['data'])
    data = defaults()
    try:
        from .call_models import CallConfig
        row = CallConfig.objects.order_by('pk').first()
        stored = (row.data or {}) if row else {}
        for section, values in stored.items():
            if section in data and isinstance(values, dict):
                for key, value in values.items():
                    if key in data[section]:
                        data[section][key] = value
    except Exception:
        pass
    _CACHE['data'] = data
    _CACHE['at'] = now
    # A copy on the way out, always: callers edit what they get (the settings
    # form builds its save on top of this), and an edit must never reach the
    # cache except through save().
    return copy.deepcopy(data)


def save(data, user=None):
    """Store the admin's values (the whole effective dict is fine — anything
    equal to a default is still just an override that happens to match) and
    make them live."""
    from .call_models import CallConfig
    row = CallConfig.objects.order_by('pk').first() or CallConfig()
    row.data = data
    if user is not None:
        row.updated_by = user
    row.save()
    invalidate()
    return row


def reset(user=None):
    from .call_models import CallConfig
    CallConfig.objects.all().delete()
    invalidate()


def client_rtc():
    """The slice the browser needs, sent in every join response. Only what
    the client acts on — bridge-side values stay server-side."""
    c = effective()
    return {
        'audio': {k: c['audio'][k] for k in
                  ('codec', 'opus_max_kbps', 'opus_fec', 'opus_dtx',
                   'echo_cancellation', 'noise_suppression', 'auto_gain')},
        'screen': {k: c['screen'][k] for k in
                   ('codec', 'max_kbps', 'capture_fps', 'capture_width')},
        'transport': {'ice_policy': c['transport']['ice_policy']},
    }
