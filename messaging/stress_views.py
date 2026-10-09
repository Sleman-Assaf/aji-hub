# messaging/stress_views.py
"""Call capacity: the live picture, and a stress test to find the ceiling.

What limits calling on the Hub is not audio — browsers carry that to each other
over WebRTC — but signalling: every person in a call asks `sync` about once a
second, and every one of those requests reads and writes the database. So the
ceiling on concurrent calls is the number of sync-shaped operations per second
the server can sustain, divided by one per peer per second.

The stress test measures exactly that. It builds a batch of synthetic calls
(in a private test conversation that only the admin is in, so nobody's phone
rings), then hammers them from parallel worker threads with the same mix of
work a real sync does — a heartbeat write, a roster read, a signal-box read,
and the occasional signal write — and reports throughput, latency and derived
capacity. Everything it creates is deleted when it finishes.

Admin-only, behind the same rule as Global Settings.
"""
import json
import os
import random
import secrets
import statistics
import threading
import time

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection
from django.db.utils import DatabaseError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.models import GlobalSetting, user_can_manage_settings

from .call_models import (MAX_PARTICIPANTS, PEER_TIMEOUT, RING_SECONDS,
                          Call, CallPeer, CallSignal)
from .call_views import _still_alive
from .models import Conversation

#: Test conversations are named with this prefix so they can be recognised,
#: excluded from the live picture, and swept up if a test ever dies half-way.
TEST_TAG = '[CALL-STRESS-TEST]'

#: Where the last report lives, so the page can show it after a restart.
REPORT_KEY = 'calls.stress.last_report'

#: A real peer costs one sync per second (the request is also the heartbeat).
SYNCS_PER_PEER_PER_SEC = 1.0

#: Capacity is quoted at 60% of measured throughput: the same server is also
#: answering the four-second chat poll for every signed-in person, serving
#: pages, and running everything else on the Hub.
HEADROOM = 0.6

#: Hard bounds on what one test run may ask for. The test runs inside the
#: request that started it, so the duration cap is what keeps a worker thread
#: from being tied up for minutes.
MAX_TEST_CALLS = 50
#: Peers per simulated call. Above the mesh's 8 because bridge-mode group
#: calls carry up to 64, and every one of those peers' syncs reads the whole
#: roster — a big room is honestly more expensive per sync, so it can be
#: simulated as one.
MAX_TEST_PEERS = 64
MIN_DURATION, MAX_DURATION = 2.0, 20.0
MAX_WORKERS = 64


def _db_ping_ms(samples=15):
    """The cost of asking the database anything at all: median SELECT 1.

    Separates two stories the load numbers alone cannot: a slow test on a
    fast query engine (driver or network between Django and the server) from
    a fast round trip that collapses under concurrency (real contention).
    """
    times = []
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')          # connection warm-up, not timed
            for _ in range(samples):
                t0 = time.perf_counter()
                cur.execute('SELECT 1')
                cur.fetchone()
                times.append(time.perf_counter() - t0)
    except Exception:
        return None
    times.sort()
    return round(times[len(times) // 2] * 1000.0, 2)


def _environment():
    """What the numbers were measured on, so two reports can be compared.

    Capacity here is a property of the machine and the database engine — a
    report without the environment is a number without units. The engine
    section adapts: journal mode is the story on SQLite, version and edition
    are the story on SQL Server (Express caps itself at 4 cores and ~1.4 GB
    of buffer pool, so which edition answered matters).
    """
    import ctypes
    import platform

    ram_gb = None
    try:
        if os.name == 'nt':
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong),
                            ('dwMemoryLoad', ctypes.c_ulong),
                            ('ullTotalPhys', ctypes.c_ulonglong),
                            ('ullAvailPhys', ctypes.c_ulonglong),
                            ('ullTotalPageFile', ctypes.c_ulonglong),
                            ('ullAvailPageFile', ctypes.c_ulonglong),
                            ('ullTotalVirtual', ctypes.c_ulonglong),
                            ('ullAvailVirtual', ctypes.c_ulonglong),
                            ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            ram_gb = round(st.ullTotalPhys / 1024 ** 3, 1)
        else:
            with open('/proc/meminfo') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        ram_gb = round(int(line.split()[1]) / 1024 ** 2, 1)
                        break
    except Exception:
        pass

    env = {
        'db_engine': connection.vendor,
        'os': f'{platform.system()} {platform.release()}',
        'cpu': platform.processor() or platform.machine(),
        'cpu_cores': os.cpu_count(),
        'ram_gb': ram_gb,
        'python': platform.python_version(),
        'db_ping_ms': _db_ping_ms(),
    }
    if connection.vendor == 'sqlite':
        try:
            with connection.cursor() as c:
                c.execute('PRAGMA journal_mode;')
                env['sqlite_journal_mode'] = c.fetchone()[0]
                c.execute('PRAGMA synchronous;')
                env['sqlite_synchronous'] = \
                    {0: 'OFF', 1: 'NORMAL', 2: 'FULL', 3: 'EXTRA'}.get(
                        c.fetchone()[0], '?')
        except Exception:
            pass
    elif connection.vendor == 'microsoft':
        try:
            with connection.cursor() as c:
                c.execute("SELECT SERVERPROPERTY('ProductVersion'), "
                          "SERVERPROPERTY('Edition'), DB_NAME()")
                version, edition, dbname = c.fetchone()
                env['mssql_version'] = str(version)
                env['mssql_edition'] = str(edition)
                env['mssql_database'] = str(dbname)
                # MAXDOP-relevant: how many schedulers the engine is actually
                # allowed to use — Express answers 4 whatever the machine has.
                c.execute('SELECT COUNT(*) FROM sys.dm_os_schedulers '
                          "WHERE status = 'VISIBLE ONLINE'")
                env['mssql_schedulers'] = int(c.fetchone()[0])
        except Exception:
            pass
    return env


def _require_admin(user):
    if not user_can_manage_settings(user):
        raise PermissionDenied


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


# ── the live picture ─────────────────────────────────────────────────────

def _live_snapshot():
    """Every real call going on right now, swept for ghosts first."""
    calls = (Call.objects
             .filter(status=Call.Status.LIVE)
             .exclude(conversation__name__startswith=TEST_TAG)
             .select_related('conversation', 'started_by')
             .order_by('started_at'))
    rows, peers_total, group_n, p2p_n, sharing_n = [], 0, 0, 0, 0
    for call in calls:
        # The same sweep the call API runs: ends calls whose browsers have all
        # gone quiet, so the count here is people actually talking, not ghosts.
        if not _still_alive(call):
            continue
        peers = list(call.active_peers())
        conv = call.conversation
        is_group = conv.is_group
        group_n += 1 if is_group else 0
        p2p_n += 0 if is_group else 1
        peers_total += len(peers)
        sharing = any(p.sharing for p in peers)
        sharing_n += 1 if sharing else 0
        rows.append({
            'id': call.pk,
            'kind': 'group' if is_group else 'p2p',
            'name': (conv.name or 'Group') if is_group
                    else ' & '.join(sorted(
                        (p.user.get_full_name() or p.user.username)
                        for p in peers)[:2]) or (call.started_by.get_full_name()
                                                 or call.started_by.username),
            'peers': len(peers),
            'sharing': sharing,
            'started_at': call.started_at.isoformat(),
            'seconds': call.duration_seconds,
        })
    return {
        'concurrent_calls': len(rows),
        'group_calls': group_n,
        'p2p_calls': p2p_n,
        'peers_on_calls': peers_total,
        'screens_sharing': sharing_n,
        'calls': rows,
    }


def _last_report():
    raw = GlobalSetting.get(REPORT_KEY, '')
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


@login_required
@require_GET
def api_stress_status(request):
    """The live picture, the configured limits, and the last test's verdict."""
    _require_admin(request.user)
    from .call_views import bridge_up
    from .bridge_common import bridge_max_participants, bridge_token, bridge_url
    bridge = {'up': False, 'max_participants': bridge_max_participants()}
    if bridge_up():
        try:
            import requests
            r = requests.get(bridge_url() + '/health',
                             headers={'X-Bridge-Token': bridge_token()},
                             timeout=0.7)
            bridge.update(r.json(), up=True)
        except Exception:
            pass
    return JsonResponse({
        'now': timezone.now().isoformat(),
        'live': _live_snapshot(),
        'bridge': bridge,
        'config': {
            'max_participants_per_call': MAX_PARTICIPANTS,
            'ring_seconds': RING_SECONDS,
            'peer_timeout_seconds': int(PEER_TIMEOUT.total_seconds()),
            'syncs_per_peer_per_sec': SYNCS_PER_PEER_PER_SEC,
            'headroom': HEADROOM,
            'db_engine': connection.vendor,
        },
        'environment': _environment(),
        'leftover_test_conversations':
            Conversation.objects.filter(name__startswith=TEST_TAG).count(),
        'last_report': _last_report(),
    })


# ── the test itself ──────────────────────────────────────────────────────

def _one_sync(call_id, peer_pk, peer_id, mates):
    """One simulated sync: the same database work the real endpoint does.

    A heartbeat write, a roster read, a mailbox read, and — at roughly the rate
    a live call produces them — one signal written to somebody else. Payload
    sizes bracket what browsers actually send: a one-line ICE candidate and a
    few-kilobyte SDP.
    """
    CallPeer.objects.filter(pk=peer_pk).update(last_ping=timezone.now())
    list(CallPeer.objects.filter(call_id=call_id, left_at__isnull=True)
         .values_list('peer_id', 'muted', 'sharing'))
    list(CallSignal.objects.filter(call_id=call_id, to_peer=peer_id)
         .order_by('id').values_list('id', flat=True)[:100])
    if mates and random.random() < 0.15:
        CallSignal.objects.create(
            call_id=call_id, from_peer=peer_id,
            to_peer=random.choice(mates), kind=CallSignal.Kind.ICE,
            payload='x' * random.choice((180, 2400)))


def _worker(deadline, targets, latencies, errors, stop):
    """Loop until the deadline, syncing random peers, keeping its own score.

    Each thread is its own database connection — that is what makes the test a
    test of concurrency rather than of one connection in a loop — and closes it
    on the way out, because Django only cleans up connections it opened on a
    request thread.
    """
    close_old_connections()
    mine, my_errors = [], 0
    try:
        while time.monotonic() < deadline and not stop.is_set():
            call_id, peer_pk, peer_id, mates = random.choice(targets)
            t0 = time.perf_counter()
            try:
                _one_sync(call_id, peer_pk, peer_id, mates)
                mine.append(time.perf_counter() - t0)
            except DatabaseError:
                # Contention, whatever the engine calls it: SQLite says
                # "database is locked", SQL Server raises deadlock victims
                # (1205) and lock timeouts. All of them are the thing this
                # test exists to count. Counted, not fatal — and the
                # connection is turned over, because a deadlock-victim
                # connection can be left in a state the next query trips on.
                my_errors += 1
                try:
                    connection.close()
                except Exception:
                    pass
                close_old_connections()
                time.sleep(0.01)
    finally:
        latencies.append(mine)
        errors.append(my_errors)
        try:
            connection.close()
        except Exception:
            pass


@login_required
@require_POST
def api_stress_run(request):
    """Build synthetic calls, hammer them, report, tear down.

    Runs inside this request — bounded at MAX_DURATION seconds — so there is
    nothing to schedule and nothing left running if the admin closes the tab.
    """
    _require_admin(request.user)

    def _num(name, default, cast=int):
        try:
            return cast(request.POST.get(name, default))
        except (TypeError, ValueError):
            return default

    n_calls = _clamp(_num('calls', 6), 1, MAX_TEST_CALLS)
    n_peers = _clamp(_num('peers', 4), 2, MAX_TEST_PEERS)
    duration = _clamp(_num('duration', 6, float), MIN_DURATION, MAX_DURATION)
    workers = _clamp(_num('workers', 8), 1, MAX_WORKERS)

    # A private room: only the admin is in it, so no phone anywhere rings, the
    # chat poll never mentions it, and the live picture above excludes it.
    conv = Conversation.objects.create(
        kind=Conversation.Kind.GROUP,
        name=f'{TEST_TAG} {timezone.now():%Y-%m-%d %H:%M:%S}',
        created_by=request.user)
    conv.participants.add(request.user)

    report = None
    try:
        targets = []          # (call_id, peer_pk, peer_id, [other peer_ids])
        for _ in range(n_calls):
            call = Call.objects.create(conversation=conv,
                                       started_by=request.user)
            ids = [secrets.token_hex(8) for _ in range(n_peers)]
            CallPeer.objects.bulk_create([
                CallPeer(call=call, user=request.user, peer_id=pid)
                for pid in ids])
            rows = list(CallPeer.objects.filter(call=call)
                        .values_list('pk', 'peer_id'))
            for pk, pid in rows:
                targets.append((call.pk, pk, pid,
                                [o for _, o in rows if o != pid]))

        latencies, errors, stop = [], [], threading.Event()
        deadline = time.monotonic() + duration
        started = time.monotonic()
        threads = [threading.Thread(target=_worker, daemon=True,
                                    args=(deadline, targets, latencies,
                                          errors, stop))
                   for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            # A worker that hangs (a wedged connection) must not hang the
            # request with it: the join is bounded and stragglers are told to
            # stop and abandoned — they are daemons and hold nothing.
            t.join(timeout=duration + 10)
        stop.set()
        elapsed = time.monotonic() - started

        samples = [s for chunk in latencies for s in chunk]
        total_ops = len(samples)
        lock_errors = sum(errors)
        ops_per_sec = total_ops / elapsed if elapsed > 0 else 0.0

        def _ms(v):
            return round(v * 1000.0, 2)

        if samples:
            samples.sort()
            avg_ms = _ms(statistics.fmean(samples))
            p50_ms = _ms(samples[len(samples) // 2])
            p95_ms = _ms(samples[min(len(samples) - 1,
                                     int(len(samples) * 0.95))])
            max_ms = _ms(samples[-1])
        else:
            avg_ms = p50_ms = p95_ms = max_ms = 0.0

        # Throughput → capacity. Each concurrent peer costs one sync a second,
        # quoted with headroom for everything else the server does. Peer-to-peer
        # calls are two peers; a full group call is MAX_PARTICIPANTS.
        sustained = ops_per_sec * HEADROOM
        max_peers = int(sustained / SYNCS_PER_PEER_PER_SEC)
        report = {
            'ran_at': timezone.now().isoformat(),
            'ran_by': request.user.get_full_name() or request.user.username,
            'environment': _environment(),
            'params': {'calls': n_calls, 'peers_per_call': n_peers,
                       'duration_s': round(duration, 1), 'workers': workers,
                       'simulated_peers': len(targets)},
            'measured': {
                'elapsed_s': round(elapsed, 2),
                'total_syncs': total_ops,
                'syncs_per_sec': round(ops_per_sec, 1),
                'avg_ms': avg_ms, 'p50_ms': p50_ms,
                'p95_ms': p95_ms, 'max_ms': max_ms,
                'db_errors': lock_errors,
                'db_engine': connection.vendor,
            },
            'capacity': {
                'headroom': HEADROOM,
                'sustained_syncs_per_sec': round(sustained, 1),
                'max_concurrent_peers': max_peers,
                'max_concurrent_p2p_calls': max_peers // 2,
                'max_concurrent_full_group_calls':
                    max_peers // MAX_PARTICIPANTS,
                'max_participants_per_call': MAX_PARTICIPANTS,
                'note': ('Signalling capacity, with %d%% headroom. Audio and '
                         'screen video travel browser-to-browser and do not '
                         'load this server; the per-call limit of %d is the '
                         'WebRTC mesh limit, not a server limit.'
                         % (int(HEADROOM * 100), MAX_PARTICIPANTS)),
            },
        }
        GlobalSetting.set(REPORT_KEY, json.dumps(report), request.user)
    finally:
        # Everything the test made hangs off the conversation; deleting it
        # cascades through calls, peers and signals. In finally, so a failure
        # half-way still cleans up after itself.
        try:
            conv.delete()
        except Exception:
            pass

    return JsonResponse({'ok': True, 'report': report})


@login_required
@require_POST
def api_stress_cleanup(request):
    """Sweep up test conversations a crashed run may have left behind."""
    _require_admin(request.user)
    qs = Conversation.objects.filter(name__startswith=TEST_TAG)
    n = qs.count()
    qs.delete()
    return JsonResponse({'ok': True, 'removed': n})


@login_required
def stress_page(request):
    _require_admin(request.user)
    return render(request, 'messaging/call_stress.html', {
        'max_participants': MAX_PARTICIPANTS,
        'max_test_calls': MAX_TEST_CALLS,
        'max_test_peers': MAX_TEST_PEERS,
        'max_workers': MAX_WORKERS,
        'max_duration': int(MAX_DURATION),
    })
