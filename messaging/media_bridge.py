# messaging/media_bridge.py
"""The AJi Media Bridge: what carries every call — direct and group alike.

A mesh call is every browser sending its microphone to every other browser, and
that is why it stops at eight: the cost of being in the call grows with the
size of the call, and it grows in everybody's uplink at once. The bridge turns
that around. Each browser makes one connection — to this process — sends one
audio stream up, and receives one back: everybody else's voices, already mixed,
minus its own. Being in a fifty-person call now costs a browser exactly what a
two-person call does, and the growth has moved to this server, where an
i9 with 24 cores soaks it up.

Direct (two-person) calls ride the bridge too, and for a networking reason
rather than a capacity one: a mesh needs a route between the two computers
themselves, and office networks are full of places that route does not exist —
Wi-Fi client isolation, VLANs between sites, people on the VPN. Everyone can
reach this server (they loaded the Hub from it), so a call carried here works
wherever the Hub does. The mesh remains only as the fallback for when this
process is not running.

The mixing is deliberate, not an economy. A forwarding-only server would hand
every browser N−1 audio streams to decode and play; mixing hands it one, which
is also what keeps phones and laptops on Wi-Fi comfortable in big calls. The
screen share is forwarded rather than mixed — decoded once from whoever is
sharing, re-encoded once per viewer, capped in size and rate because a slide at
twelve frames is indistinguishable from one at thirty.

This process holds no state worth keeping and knows nothing about users: rooms
are named by Django, peers are the peer ids Django issued, and the control API
answers only to callers bearing a token derived from SECRET_KEY. Rooms build
themselves when the first offer arrives and dissolve when the last connection
drops. Kill the process and calls quietly fall back to the mesh on the next
join — Django checks its health before routing a call here.

The terminal is the bridge's flight recorder. Every join, leave, ICE and
connection state change, share start and stop, and error is logged with the
room and peer it belongs to, and every STATS_EVERY seconds each live room
prints one line of per-peer bandwidth — enough to watch a problem happen
rather than reconstruct it afterwards. `--log-level debug` on the management
command turns on the per-frame details as well. The same numbers feed the
/stats endpoint, which the IT portal's Calls page reads.

Run it with:  python manage.py run_media_bridge
"""
import asyncio
import collections
import fractions
import hmac
import json
import logging
import time

log = logging.getLogger('media_bridge')

# Imported lazily-but-loudly: these are only in requirements for the bridge,
# and the error for a missing one should say what to install, not trace.
try:
    import numpy as np
    from aiohttp import web
    from aiortc import (MediaStreamTrack, RTCConfiguration, RTCPeerConnection,
                        RTCSessionDescription)
    from aiortc.rtcconfiguration import RTCBundlePolicy
    from aiortc.mediastreams import MediaStreamError
    import av
except ImportError as exc:                                  # pragma: no cover
    raise SystemExit(
        f'The media bridge needs aiortc, aiohttp and numpy ({exc}). '
        'Run: pip install -r requirements.txt') from exc

#: Every connection carries three m-lines on one transport. Said explicitly
#: because aiortc's default ("balanced") policy groups transports by kind, and
#: with two video m-lines that leaves the second one stranded on a transport
#: the bundle negotiation then discards — a sender that negotiates perfectly
#: and never sends a packet. max-bundle is also what browsers do.
_PC_CONFIG = RTCConfiguration(bundlePolicy=RTCBundlePolicy.MAX_BUNDLE)

#: Audio is mixed on a fixed lattice: 48 kHz mono, 20 ms frames. Everything
#: arriving is resampled onto it, whatever the browser sent.
RATE = 48000
SAMPLES = 960                # 20 ms
#: How much of each person's audio is held between mixer ticks. Enough to ride
#: out network jitter, small enough that the mix never lags a conversation.
BUFFER_LIMIT = RATE // 5     # 200 ms
#: The screen share, as forwarded: capped width and frame rate. A mesh call
#: caps the same way in the browser (chat_calls.js) for the same reason.
SCREEN_MAX_W = 1440
SCREEN_FPS = 12
#: The stage when nobody is sharing: a small black frame at 2 fps, so every
#: subscriber's video slot stays warm and a share starting needs no
#: renegotiation — the picture simply changes.
IDLE_FPS = 2
VIDEO_CLOCK = 90000

#: How often the terminal gets one bandwidth line per live room, which is also
#: the sampling interval the per-peer kbps figures are computed over.
STATS_EVERY = 10
#: Closed rooms kept for the IT portal's history table. In memory on purpose:
#: this is a monitoring window, not an archive — the call log in the Hub is
#: the archive.
HISTORY_LIMIT = 60


# ── audio in: one publisher's voice, normalised and buffered ─────────────

class AudioIn:
    """The last few hundred milliseconds of one person's voice.

    A fan-out buffer, not a queue: every subscriber's mix reads the same
    samples through its own cursor, because a room of N people has N−1 mixes
    all wanting this voice — a destructive read would hand each of them a
    different shred of it. Positions are absolute sample counts since the
    publisher started; the window slides and a cursor that falls off the back
    is snapped forward to the oldest sample still held.

    Single-threaded asyncio, so no locking — nothing here ever runs
    concurrently with itself.
    """

    def __init__(self):
        self.buf = np.zeros(0, dtype=np.int16)
        self.start = 0            # absolute index of buf[0]

    @property
    def end(self):
        return self.start + len(self.buf)

    def push(self, arr):
        self.buf = np.concatenate([self.buf, arr])
        extra = len(self.buf) - BUFFER_LIMIT
        if extra > 0:
            self.buf = self.buf[extra:]
            self.start += extra

    def read(self, pos, n):
        """n samples from absolute pos → (int32 data or None, next pos).

        Underrun pads with silence and does not advance past what exists, so
        a network hiccup stretches into quiet rather than skipping words.
        """
        if pos < self.start:
            pos = self.start
        avail = self.end - pos
        if avail <= 0:
            return None, pos
        offset = pos - self.start
        take = self.buf[offset:offset + n].astype(np.int32)
        if len(take) < n:
            take = np.pad(take, (0, n - len(take)))
        return take, pos + min(n, avail)


# ── the stage: whoever is sharing a screen ───────────────────────────────

class Stage:
    """The current screen, decoded once, held as the latest frame.

    `owner` is set by Django when somebody presses share — the same server
    decision that stops two people sharing at once in a mesh call. Frames from
    anybody else are dropped on arrival.
    """

    def __init__(self, room_id=''):
        self.room_id = room_id
        self.owner = ''
        self.array = None      # rgb24 ndarray of the latest frame
        self.at = 0.0

    def set_owner(self, peer_id):
        if self.owner != peer_id:
            log.info('room %s: stage owner %s → %s', self.room_id,
                     self.owner or '(nobody)', peer_id or '(nobody)')
            self.owner = peer_id
            self.array = None  # a new sharer's stage starts clean

    def clear_if(self, peer_id):
        if self.owner == peer_id:
            log.info('room %s: stage cleared (was %s)', self.room_id, peer_id)
            self.owner = ''
            self.array = None


# ── what each subscriber receives ────────────────────────────────────────

class MixedAudioTrack(MediaStreamTrack):
    """One person's downlink: everybody in the room except themselves.

    Mix-minus rather than a full mix, because hearing your own voice come back
    a few hundred milliseconds late is unbearable in a way nothing else about
    a call is. Runs on its own 20 ms clock; publishers who have nothing
    buffered this tick simply contribute silence.
    """
    kind = 'audio'

    def __init__(self, room, exclude):
        super().__init__()
        self.room = room
        self.exclude = exclude
        self._start = None
        self._pts = 0
        self._cursor = {}      # publisher peer_id -> my read position

    async def recv(self):
        if self.readyState != 'live':
            raise MediaStreamError
        if self._start is None:
            self._start = time.monotonic()
        else:
            wait = self._start + self._pts / RATE - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)

        mix = np.zeros(SAMPLES, dtype=np.int32)
        for peer_id, audio_in in list(self.room.audio.items()):
            if peer_id == self.exclude:
                continue
            # A publisher first heard mid-call is picked up at the live edge
            # (one frame of preroll), not at the top of their buffer — a new
            # arrival should join the conversation where it is, not replay
            # the last 200 ms of it.
            pos = self._cursor.get(peer_id,
                                   max(audio_in.start, audio_in.end - SAMPLES))
            chunk, self._cursor[peer_id] = audio_in.read(pos, SAMPLES)
            if chunk is not None:
                mix += chunk
        pcm = np.clip(mix, -32768, 32767).astype(np.int16)

        frame = av.AudioFrame(format='s16', layout='mono', samples=SAMPLES)
        frame.planes[0].update(pcm.tobytes())
        frame.sample_rate = RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, RATE)
        self._pts += SAMPLES
        return frame


class StageVideoTrack(MediaStreamTrack):
    """One person's view of the shared screen.

    Yields the stage's latest frame at SCREEN_FPS while somebody else is
    sharing, and a small black frame at IDLE_FPS otherwise — including while
    *this* person is the one sharing, since sending their own screen back to
    them would be paying an encode for a picture they are looking at anyway.
    """
    kind = 'video'

    _BLACK = np.zeros((180, 320, 3), dtype=np.uint8)

    def __init__(self, room, exclude):
        super().__init__()
        self.room = room
        self.exclude = exclude
        self._start = None
        self._pts = 0

    async def recv(self):
        if self.readyState != 'live':
            raise MediaStreamError
        stage = self.room.stage
        live = stage.array is not None and stage.owner != self.exclude
        ptime = 1.0 / (SCREEN_FPS if live else IDLE_FPS)

        if self._start is None:
            self._start = time.monotonic()
        else:
            self._pts += int(ptime * VIDEO_CLOCK)
            wait = self._start + self._pts / VIDEO_CLOCK - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)

        # Re-read after the sleep: the share may have started or stopped.
        stage = self.room.stage
        live = stage.array is not None and stage.owner != self.exclude
        arr = stage.array if live else self._BLACK
        frame = av.VideoFrame.from_ndarray(arr, format='rgb24')
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, VIDEO_CLOCK)
        return frame


# ── per-peer accounting, for the terminal and for /stats ─────────────────

class PeerMeta:
    """What the bridge knows about one connection beyond the connection itself.

    All of it is bookkeeping for the flight recorder: when they joined, the
    transport byte counters as last sampled (bytesReceived is their uplink to
    the bridge, bytesSent the bridge's downlink to them), the rates computed
    from the last two samples, and how much audio has actually arrived — the
    number that separates "connected but silent" from "not connected".
    """

    def __init__(self):
        self.joined = time.monotonic()
        self.up_bytes = 0        # transport bytesReceived (browser → bridge)
        self.down_bytes = 0      # transport bytesSent    (bridge → browser)
        self.up_kbps = 0.0
        self.down_kbps = 0.0
        self.audio_frames = 0    # decoded audio frames pushed into the mix
        self.audio_since = 0.0   # monotonic time of the last audio frame
        self.state = 'new'       # RTCPeerConnection.connectionState
        self.ice = 'new'         # iceConnectionState


async def transport_bytes(pc):
    """(sent, received) over this connection's transport, best effort.

    Read from aiortc's stats report. Transport stats are the honest number —
    they include RTP, RTCP, retransmits and DTLS, which is what the wire
    actually carried. Falls back to summing the RTP stream stats if a build
    of aiortc reports no transport entry, and to zeros rather than an
    exception in any case: accounting must never take a call down.
    """
    sent = received = 0
    try:
        report = await pc.getStats()
        for stat in report.values():
            kind = getattr(stat, 'type', '')
            if kind == 'transport':
                sent += int(getattr(stat, 'bytesSent', 0) or 0)
                received += int(getattr(stat, 'bytesReceived', 0) or 0)
        if not (sent or received):
            for stat in report.values():
                kind = getattr(stat, 'type', '')
                if kind == 'outbound-rtp':
                    sent += int(getattr(stat, 'bytesSent', 0) or 0)
                elif kind == 'inbound-rtp':
                    received += int(getattr(stat, 'bytesReceived', 0) or 0)
    except Exception as exc:
        log.debug('stats read failed: %s', exc)
    return sent, received


def _fmt_bytes(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


# ── rooms ────────────────────────────────────────────────────────────────

class Room:
    """One call's worth of connections, buffers, its stage — and its ledger."""

    def __init__(self, room_id):
        self.id = room_id
        self.pcs = {}          # peer_id -> RTCPeerConnection
        self.audio = {}        # peer_id -> AudioIn
        self.meta = {}         # peer_id -> PeerMeta
        self.stage = Stage(room_id)
        self.created = time.monotonic()
        self.opened_at = time.time()
        self.peak_peers = 0
        self.peers_served = 0
        #: Bytes contributed by peers who have already left, so a room's total
        #: is not only whoever happens to still be connected.
        self.done_up = 0
        self.done_down = 0

    def live_totals(self):
        up = self.done_up + sum(m.up_bytes for m in self.meta.values())
        down = self.done_down + sum(m.down_bytes for m in self.meta.values())
        return up, down

    async def drop(self, peer_id, why='left'):
        pc = self.pcs.pop(peer_id, None)
        self.audio.pop(peer_id, None)
        meta = self.meta.pop(peer_id, None)
        self.stage.clear_if(peer_id)
        if pc is not None and meta is not None:
            # One last read of the meters before the connection is torn down,
            # so a short call is not accounted as a free one.
            sent, received = await transport_bytes(pc)
            meta.up_bytes = max(meta.up_bytes, received)
            meta.down_bytes = max(meta.down_bytes, sent)
        if meta is not None:
            # Banked BEFORE pc.close(): closing fires the connection-state
            # handler, which garbage-collects and may retire the room — the
            # ledger has to be complete by the time that runs, or the last
            # peer's bytes vanish from the room's history line.
            self.done_up += meta.up_bytes
            self.done_down += meta.down_bytes
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                pass
        if meta is not None:
            stayed = time.monotonic() - meta.joined
            log.info('room %s: %s %s after %s — ↑%s from them, ↓%s to them, '
                     '%d audio frames (%d in room)',
                     self.id, peer_id, why, _fmt_secs(stayed),
                     _fmt_bytes(meta.up_bytes), _fmt_bytes(meta.down_bytes),
                     meta.audio_frames, len(self.pcs))

    async def close(self):
        for peer_id in list(self.pcs):
            await self.drop(peer_id, why='dropped (room closing)')


def _fmt_secs(s):
    s = int(s)
    if s < 60:
        return f'{s}s'
    if s < 3600:
        return f'{s // 60}m {s % 60:02d}s'
    return f'{s // 3600}h {s % 3600 // 60:02d}m'


ROOMS = {}
STARTED = time.monotonic()
STARTED_AT = time.time()

#: Closed rooms, newest last, for the IT portal's history table.
HISTORY = collections.deque(maxlen=HISTORY_LIMIT)

#: Everything since this process started, for the portal's headline numbers.
TOTALS = {'rooms': 0, 'peers': 0, 'up_bytes': 0, 'down_bytes': 0}


def _room(room_id):
    room = ROOMS.get(room_id)
    if room is None:
        room = ROOMS[room_id] = Room(room_id)
        TOTALS['rooms'] += 1
        log.info('room %s opened (%d rooms live)', room_id, len(ROOMS))
    return room


def _retire(room):
    """Write a closed room into the history and the running totals."""
    up, down = room.live_totals()
    TOTALS['up_bytes'] += up
    TOTALS['down_bytes'] += down
    HISTORY.append({
        'room': room.id,
        'opened_at': room.opened_at,
        'closed_at': time.time(),
        'duration_s': int(time.monotonic() - room.created),
        'peak_peers': room.peak_peers,
        'peers_served': room.peers_served,
        'up_bytes': up,
        'down_bytes': down,
    })
    log.info('room %s closed after %s — peak %d peers, %d served, '
             '↑%s ↓%s total (%d rooms live)',
             room.id, _fmt_secs(time.monotonic() - room.created),
             room.peak_peers, room.peers_served,
             _fmt_bytes(up), _fmt_bytes(down), len(ROOMS) - 1)


def _gc(room):
    if room.id in ROOMS and not room.pcs:
        _retire(room)
        del ROOMS[room.id]


# ── reading what each publisher sends ────────────────────────────────────

async def _read_audio(room, peer_id, track):
    """Their voice, onto the mixing lattice, until the track ends."""
    resampler = av.AudioResampler(format='s16', layout='mono', rate=RATE)
    audio_in = room.audio.get(peer_id)
    meta = room.meta.get(peer_id)
    if audio_in is None:
        return
    first = True
    try:
        while True:
            frame = await track.recv()
            if first:
                first = False
                log.info('room %s: %s audio flowing (%d Hz %s → mixed at '
                         '%d Hz mono)', room.id, peer_id, frame.sample_rate,
                         frame.layout.name, RATE)
            for resampled in resampler.resample(frame):
                audio_in.push(resampled.to_ndarray().flatten())
            if meta is not None:
                meta.audio_frames += 1
                meta.audio_since = time.monotonic()
                if meta.audio_frames % 3000 == 0:   # ~once a minute at 20 ms
                    log.debug('room %s: %s audio healthy — %d frames, '
                              'buffer %d ms', room.id, peer_id,
                              meta.audio_frames,
                              int(len(audio_in.buf) / RATE * 1000))
    except MediaStreamError:
        log.debug('room %s: %s audio track ended', room.id, peer_id)
    except Exception as exc:
        log.warning('room %s: audio reader for %s failed: %s',
                    room.id, peer_id, exc)


async def _read_video(room, peer_id, track):
    """Their screen, decoded once, throttled, onto the stage.

    Frames from anyone Django has not named as the sharer are read and dropped
    — reading them is what keeps the connection healthy while somebody's
    replaceTrack races the state announcement by a moment.
    """
    last_size = None
    try:
        while True:
            frame = await track.recv()
            stage = room.stage
            if stage.owner and stage.owner != peer_id:
                continue
            now = time.monotonic()
            if now - stage.at < 1.0 / SCREEN_FPS:
                continue
            if frame.width > SCREEN_MAX_W:
                frame = frame.reformat(
                    width=SCREEN_MAX_W,
                    height=int(frame.height * SCREEN_MAX_W / frame.width) // 2 * 2)
            if (frame.width, frame.height) != last_size:
                last_size = (frame.width, frame.height)
                log.info('room %s: %s screen frames on stage at %dx%d',
                         room.id, peer_id, frame.width, frame.height)
            stage.array = frame.to_rgb().to_ndarray()
            stage.at = now
            if not stage.owner:
                # Fallback if the state announcement was lost: frames flowing
                # are a claim to the stage nobody else has made.
                log.info('room %s: %s claimed the stage by sending frames',
                         room.id, peer_id)
                stage.owner = peer_id
    except MediaStreamError:
        log.debug('room %s: %s video track ended', room.id, peer_id)
    except Exception as exc:
        log.warning('room %s: video reader for %s failed: %s',
                    room.id, peer_id, exc)


# ── the ten-second flight recorder ───────────────────────────────────────

async def _stats_loop():
    """One line per live room, every STATS_EVERY seconds.

    This is the loop that turns "the call was bad" into a line in a terminal:
    per-peer up/down bitrate, connection and ICE state, audio buffer depth,
    and whether audio has actually arrived recently. It is also what samples
    the byte counters the /stats endpoint and the room ledgers report.
    """
    while True:
        await asyncio.sleep(STATS_EVERY)
        try:
            for room in list(ROOMS.values()):
                parts = []
                for peer_id, pc in list(room.pcs.items()):
                    meta = room.meta.get(peer_id)
                    if meta is None:
                        continue
                    sent, received = await transport_bytes(pc)
                    meta.up_kbps = max(0.0, (received - meta.up_bytes)
                                       * 8 / 1000.0 / STATS_EVERY)
                    meta.down_kbps = max(0.0, (sent - meta.down_bytes)
                                         * 8 / 1000.0 / STATS_EVERY)
                    meta.up_bytes = max(meta.up_bytes, received)
                    meta.down_bytes = max(meta.down_bytes, sent)
                    audio_in = room.audio.get(peer_id)
                    buf_ms = int(len(audio_in.buf) / RATE * 1000) if audio_in else 0
                    quiet = (meta.audio_since
                             and time.monotonic() - meta.audio_since > 5)
                    parts.append(
                        f'{peer_id} [{meta.state}/{meta.ice}] '
                        f'↑{meta.up_kbps:.0f} ↓{meta.down_kbps:.0f} kbps '
                        f'buf {buf_ms}ms'
                        + (' NO-AUDIO-5s' if quiet else ''))
                    if quiet:
                        log.warning('room %s: %s connected but no audio for '
                                    '>5 s — check their microphone or network',
                                    room.id, peer_id)
                if parts:
                    up, down = room.live_totals()
                    share = f', sharing: {room.stage.owner}' if room.stage.owner else ''
                    log.info('room %s │ %d peer(s)%s │ total ↑%s ↓%s │ %s',
                             room.id, len(room.pcs), share,
                             _fmt_bytes(up), _fmt_bytes(down),
                             ' ║ '.join(parts))
        except Exception as exc:
            log.warning('stats loop hiccup: %s', exc)


# ── the control API ──────────────────────────────────────────────────────

TOKEN = ''      # set by run()

#: Server addresses to advertise in answers; empty advertises everything the
#: machine has. Set by run() from MEDIA_BRIDGE["advertise_ip"] — the fix for
#: one-way audio across subnets on a multi-homed server: one address means
#: one path, which both ICE agents must then agree on.
ADVERTISE_IPS = []


def _sdp_candidates(sdp):
    """(address, port, type) of every ICE candidate in an SDP — the line of
    logging that shows which subnet each peer is negotiating from."""
    out = []
    for line in sdp.splitlines():
        line = line.strip()
        if line.startswith('a=candidate:'):
            parts = line.split()
            if len(parts) >= 8 and parts[6] == 'typ':
                out.append((parts[4], parts[5], parts[7]))
    return out


def _fmt_candidates(cands):
    return ', '.join(f'{a}:{p}/{t}' for a, p, t in cands) or '(none)'


def _advertised(sdp):
    """The answer, with its candidates cut down to ADVERTISE_IPS.

    Returns (sdp, kept, dropped). Filtering everything away would leave the
    browser nothing to connect to, so a filter that matches no gathered
    candidate is refused with a warning and the answer goes out whole —
    a misconfigured setting must degrade to today's behaviour, not to
    silence.
    """
    cands = _sdp_candidates(sdp)
    if not ADVERTISE_IPS:
        return sdp, cands, 0
    keep = [c for c in cands if c[0] in ADVERTISE_IPS]
    if not keep:
        log.warning('advertise_ip %s matches none of the gathered '
                    'candidates (%s) — answering with all of them; fix '
                    'MEDIA_BRIDGE["advertise_ip"]',
                    ADVERTISE_IPS, _fmt_candidates(cands))
        return sdp, cands, 0
    lines = sdp.replace('\r\n', '\n').split('\n')
    out, dropped = [], 0
    for line in lines:
        s = line.strip()
        if s.startswith('a=candidate:'):
            parts = s.split()
            if len(parts) >= 5 and parts[4] not in ADVERTISE_IPS:
                dropped += 1
                continue
        out.append(line)
    return '\r\n'.join(out), keep, dropped


def _remote_directions(sdp):
    """Each m-line's declared direction, by mid, straight from the offer.

    Only as much SDP reading as the question needs: sections start at 'm=',
    and within one, 'a=mid:' names it and one of the four direction lines
    says which way it points.
    """
    out, mid, direction = {}, None, 'sendrecv'
    for line in sdp.splitlines():
        line = line.strip()
        if line.startswith('m='):
            if mid is not None:
                out[mid] = direction
            mid, direction = None, 'sendrecv'
        elif line.startswith('a=mid:'):
            mid = line[6:].strip()
        elif line in ('a=sendrecv', 'a=sendonly', 'a=recvonly', 'a=inactive'):
            direction = line[2:]
    if mid is not None:
        out[mid] = direction
    return out


@web.middleware
async def _auth(request, handler):
    given = request.headers.get('X-Bridge-Token', '')
    if not (TOKEN and hmac.compare_digest(given, TOKEN)):
        log.warning('control API: rejected %s %s from %s (bad token)',
                    request.method, request.path, request.remote)
        return web.json_response({'error': 'forbidden'}, status=403)
    return await handler(request)


async def offer(request):
    """One browser's offer in, this bridge's answer out.

    The client lays out three slots and this fills them: its microphone
    (answered with the mix), its screen (received onto the stage when it is
    the sharer), and a slot for the stage. Non-trickle on both ends — the SDPs
    carry their candidates — so signalling is one round trip through Django.
    """
    began = time.monotonic()
    data = await request.json()
    room_id = str(data.get('room') or '')
    peer_id = str(data.get('peer') or '')
    sdp = data.get('sdp') or ''
    if not (room_id and peer_id and sdp):
        log.warning('offer rejected: room=%r peer=%r sdp=%d bytes',
                    room_id, peer_id, len(sdp))
        return web.json_response({'error': 'room, peer and sdp required'},
                                 status=400)

    offered = _remote_directions(sdp)
    their_cands = _sdp_candidates(sdp)
    log.info('room %s: offer from %s — %d bytes, m-lines %s, '
             'their candidates: %s', room_id, peer_id, len(sdp),
             json.dumps(offered), _fmt_candidates(their_cands))

    room = _room(room_id)
    # The same peer offering again is a reconnect (a page reloaded mid-call);
    # whatever was there before is theirs to replace.
    if peer_id in room.pcs:
        log.info('room %s: %s is reconnecting — replacing their old '
                 'connection', room_id, peer_id)
        await room.drop(peer_id, why='replaced by reconnect')

    pc = RTCPeerConnection(_PC_CONFIG)
    room.pcs[peer_id] = pc
    room.audio[peer_id] = AudioIn()
    meta = room.meta[peer_id] = PeerMeta()
    room.peers_served += 1
    room.peak_peers = max(room.peak_peers, len(room.pcs))
    TOTALS['peers'] += 1

    @pc.on('track')
    def on_track(track):
        log.info('room %s: %s %s track arrived', room_id, peer_id, track.kind)
        if track.kind == 'audio':
            asyncio.ensure_future(_read_audio(room, peer_id, track))
        else:
            asyncio.ensure_future(_read_video(room, peer_id, track))

    @pc.on('connectionstatechange')
    async def on_state():
        meta.state = pc.connectionState
        lvl = (logging.WARNING if pc.connectionState == 'failed'
               else logging.INFO)
        log.log(lvl, 'room %s: %s connection → %s', room_id, peer_id,
                pc.connectionState)
        if pc.connectionState in ('failed', 'closed'):
            await room.drop(peer_id, why=f'connection {pc.connectionState}')
            _gc(room)

    @pc.on('iceconnectionstatechange')
    def on_ice():
        meta.ice = pc.iceConnectionState
        lvl = (logging.WARNING if pc.iceConnectionState in ('failed',
                                                            'disconnected')
               else logging.DEBUG)
        log.log(lvl, 'room %s: %s ICE → %s', room_id, peer_id,
                pc.iceConnectionState)

    @pc.on('icegatheringstatechange')
    def on_gather():
        log.debug('room %s: %s ICE gathering → %s', room_id, peer_id,
                  pc.iceGatheringState)

    await pc.setRemoteDescription(RTCSessionDescription(sdp, 'offer'))

    # Fill what the offer laid out, and say so: replaceTrack alone does not
    # change a transceiver's negotiated direction — an answer left at the
    # default would tell the browser this side only listens, and it would sit
    # in a silent call. Which slot is which is read from the offer itself,
    # matched by mid, rather than assumed from ordering: the client's
    # sendonly video is its screen (this side receives), its recvonly video
    # is the stage (this side sends).
    for transceiver in pc.getTransceivers():
        their = offered.get(transceiver.mid, 'sendrecv')
        if transceiver.kind == 'audio':
            transceiver.direction = 'sendrecv'
            transceiver.sender.replaceTrack(MixedAudioTrack(room, peer_id))
        elif their == 'recvonly':
            transceiver.direction = 'sendonly'
            transceiver.sender.replaceTrack(StageVideoTrack(room, peer_id))
        else:
            transceiver.direction = 'recvonly'

    answer_sdp = await pc.createAnswer()
    await pc.setLocalDescription(answer_sdp)   # gathers ICE before resolving

    # A multi-homed server advertises every interface it has, and two ICE
    # agents free to pick among them can settle on different pairs — each
    # direction of audio then rides a different path, which is how a call
    # goes one-way across subnets. advertise_ip pins the answer to the one
    # address every subnet reaches.
    out_sdp, kept, dropped = _advertised(pc.localDescription.sdp)
    log.info('room %s: %s joined — answered in %d ms, advertising %s%s '
             '(%d in room, peak %d)', room_id, peer_id,
             int((time.monotonic() - began) * 1000), _fmt_candidates(kept),
             f' [{dropped} filtered by advertise_ip]' if dropped else '',
             len(room.pcs), room.peak_peers)
    return web.json_response({'sdp': out_sdp,
                              'type': pc.localDescription.type})


async def state(request):
    """Django's word on who holds the stage. Follows the same rule the mesh
    enforces — one screen at a time, starting takes it over."""
    data = await request.json()
    room = ROOMS.get(str(data.get('room') or ''))
    if room is None:
        return web.json_response({'ok': True})
    peer_id = str(data.get('peer') or '')
    if data.get('sharing'):
        room.stage.set_owner(peer_id)
    else:
        room.stage.clear_if(peer_id)
    return web.json_response({'ok': True})


async def leave(request):
    data = await request.json()
    room = ROOMS.get(str(data.get('room') or ''))
    if room is not None:
        await room.drop(str(data.get('peer') or ''), why='hung up')
        _gc(room)
    return web.json_response({'ok': True})


async def close(request):
    room = ROOMS.get(str((await request.json()).get('room') or ''))
    if room is not None:
        log.info('room %s: Django says the call is over', room.id)
        await room.close()
        # close() dropped everyone, so this retires the room into the history
        # — unless a racing offer re-created a peer between the two, in which
        # case it stays: a live connection is never yanked by bookkeeping.
        _gc(room)
    return web.json_response({'ok': True})


async def health(request):
    """Alive, and how busy. Django asks this before routing a call here, and
    the capacity page shows it."""
    return web.json_response({
        'ok': True,
        'rooms': len(ROOMS),
        'peers': sum(len(r.pcs) for r in ROOMS.values()),
        'sharing': sum(1 for r in ROOMS.values() if r.stage.array is not None),
        'uptime_s': int(time.monotonic() - STARTED),
    })


async def stats(request):
    """The full picture for the IT portal's Calls page.

    Live rooms with per-peer bandwidth (rates from the last stats-loop sample,
    byte totals read fresh from the transport right now), the recent history
    of closed rooms, and the running totals since this process started. The
    bridge speaks peer ids; Django turns them back into names.
    """
    rooms = []
    for room in list(ROOMS.values()):
        peers = []
        for peer_id, pc in list(room.pcs.items()):
            meta = room.meta.get(peer_id)
            if meta is None:
                continue
            sent, received = await transport_bytes(pc)
            meta.up_bytes = max(meta.up_bytes, received)
            meta.down_bytes = max(meta.down_bytes, sent)
            audio_in = room.audio.get(peer_id)
            peers.append({
                'peer': peer_id,
                'state': meta.state,
                'ice': meta.ice,
                'connected_s': int(time.monotonic() - meta.joined),
                'up_bytes': meta.up_bytes,
                'down_bytes': meta.down_bytes,
                'up_kbps': round(meta.up_kbps, 1),
                'down_kbps': round(meta.down_kbps, 1),
                'audio_buffer_ms': int(len(audio_in.buf) / RATE * 1000)
                                   if audio_in else 0,
                'audio_frames': meta.audio_frames,
                'sharing': room.stage.owner == peer_id,
            })
        up, down = room.live_totals()
        rooms.append({
            'room': room.id,
            'opened_at': room.opened_at,
            'age_s': int(time.monotonic() - room.created),
            'peak_peers': room.peak_peers,
            'peers_served': room.peers_served,
            'sharing_owner': room.stage.owner,
            'up_bytes': up,
            'down_bytes': down,
            'peers': peers,
        })
    live_up = sum(r['up_bytes'] for r in rooms)
    live_down = sum(r['down_bytes'] for r in rooms)
    return web.json_response({
        'ok': True,
        'started_at': STARTED_AT,
        'uptime_s': int(time.monotonic() - STARTED),
        'stats_interval_s': STATS_EVERY,
        'rooms': rooms,
        'history': list(HISTORY),
        'totals': {
            'rooms_served': TOTALS['rooms'],
            'peers_served': TOTALS['peers'],
            # Closed rooms plus whatever the live ones have clocked so far.
            'up_bytes': TOTALS['up_bytes'] + live_up,
            'down_bytes': TOTALS['down_bytes'] + live_down,
        },
    })


def build_app(token):
    global TOKEN
    TOKEN = token
    app = web.Application(middlewares=[_auth])
    app.router.add_post('/offer', offer)
    app.router.add_post('/state', state)
    app.router.add_post('/leave', leave)
    app.router.add_post('/close', close)
    app.router.add_get('/health', health)
    app.router.add_get('/stats', stats)

    async def start_stats(app):
        app['stats_task'] = asyncio.ensure_future(_stats_loop())

    async def stop_stats(app):
        app['stats_task'].cancel()

    app.on_startup.append(start_stats)
    app.on_cleanup.append(stop_stats)
    return app


def run(token, host='127.0.0.1', port=8100, level='info', advertise=None,
        screen_max_w=None, screen_fps=None):
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format='%(asctime)s.%(msecs)03d [bridge] %(levelname)-7s %(message)s',
        datefmt='%H:%M:%S')
    # aiohttp's access log is one line per control-API request — noise at
    # info (Django polls /health), useful at debug.
    logging.getLogger('aiohttp.access').setLevel(logging.DEBUG)
    global ADVERTISE_IPS, SCREEN_MAX_W, SCREEN_FPS
    ADVERTISE_IPS = list(advertise or [])
    # The IT ▸ Call settings page holds these; they are read once here, which
    # is why the page marks them "bridge restart".
    if screen_max_w:
        SCREEN_MAX_W = max(320, min(3840, int(screen_max_w)))
    if screen_fps:
        SCREEN_FPS = max(1, min(30, int(screen_fps)))
    log.info('AJi Media Bridge on http://%s:%d — media over UDP, control '
             'token from SECRET_KEY', host, port)
    if ADVERTISE_IPS:
        log.info('advertising media on %s only — other interfaces are '
                 'stripped from answers', ', '.join(ADVERTISE_IPS))
    else:
        log.info('advertising media on ALL interfaces — on a multi-homed '
                 'server set MEDIA_BRIDGE["advertise_ip"] to the one '
                 'address every subnet reaches, or cross-subnet calls can '
                 'go one-way')
    log.info('mix: %d Hz mono, %d ms frames, %d ms buffer │ screen: max '
             '%dpx wide at %d fps │ room stats every %d s │ log level %s',
             RATE, SAMPLES * 1000 // RATE, BUFFER_LIMIT * 1000 // RATE,
             SCREEN_MAX_W, SCREEN_FPS, STATS_EVERY, str(level).lower())
    log.info('carrying ALL calls while up — direct and group; the browser '
             'mesh is the fallback when this process is not running')
    web.run_app(build_app(token), host=host, port=port, print=None)
