# cms/providers.py
"""Live data for the section templates that read the Hub rather than stored text.

A block like "Latest news" has to work on any page it is dropped onto, so it
cannot depend on a particular view having prepared a context variable. Each
provider below takes the section and the request and returns the context that
section's template needs, reading its limits and links from Section.config.

This is what lets the same block be placed on the homepage, in a column, or on a
brand new page and behave identically in all three.
"""
from datetime import date, datetime, time, timedelta

from django.db.models import Q


def _limit(section, default, cap=24):
    return _cfg_int(section, 'limit', default, cap)


def _cfg_int(section, key, default, cap=24):
    """One whole number off a block's config, clamped.

    Split out from `_limit` because a block with two lists in it had them both
    reading `limit`: the communities block capped its chips at six and its
    threads at four off the same number, so tuning either moved both and the
    thread cap could not be set at all.
    """
    try:
        n = int((section.config or {}).get(key) or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, cap))


def news_list(section, request):
    from content.models import NewsArticle
    n = _limit(section, 10)
    qs = NewsArticle.objects.filter(published=True).select_related('category')
    # `date` is a day, so several articles published on the same day tie, and a
    # tie leaves SQLite free to return them in whatever order it likes — which
    # was insertion order, putting the newest of the day last. On a three-item
    # block that pushed the most recent article off the homepage entirely.
    # `-created_at` is the tie-break the model itself declares in Meta.ordering;
    # this ordering only adds `-pinned` in front of it.
    qs = (qs.order_by('-pinned', '-date', '-created_at') if _has_pinned(qs)
          else qs.order_by('-date', '-created_at'))
    # The lead and then everything that goes in the strip under it, in one
    # slice. The block's limit is the whole card: a lead plus limit-1 in the
    # strip, which is why the default is ten rather than the four it takes to
    # fill a static row.
    items = list(qs[:n])
    return {'articles': items, 'more': max(0, qs.count() - n)}


def _has_pinned(qs):
    return any(f.name == 'pinned' for f in qs.model._meta.get_fields())


#: Event types that belong to a person's working day rather than to AJi. The
#: calendar holds both in one table, and "Official events" was showing every row
#: in it — so a sprint planning call sat on the homepage looking like a company
#: announcement. What the card is for is the things AJi puts on: holidays, town
#: halls, company-wide events, training. A meeting is somebody's diary, and it
#: belongs in "Your workspace" instead.
PERSONAL_EVENT_TYPES = ('meeting', 'online_meeting', 'remote_session')


def event_list(section, request):
    """Events looking forward — the "Official events" card.

    Company events only. See PERSONAL_EVENT_TYPES for what is kept out and why.
    """
    from content.models import CalendarEvent
    n = _limit(section, 3)
    today = date.today()
    qs = (CalendarEvent.objects
          .filter(date__gte=today)
          .exclude(event_type__in=PERSONAL_EVENT_TYPES)
          .order_by('date', 'time'))
    return {'events': list(qs[:n]), 'more': max(0, qs.count() - n)}


def _outlook_today(request, want):
    """Today from the signed-in person's Outlook calendar, or None to fall back.

    None and an empty list mean different things here and the difference is the
    whole feature: None is "Outlook could not answer", which sends the caller to
    the Hub's own calendar, while [] is Outlook saying the day is clear — and
    showing "No events today" is then correct rather than a hidden failure.
    """
    from django.conf import settings as dj_settings

    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None

    from core import msgraph
    if not msgraph.calendar_enabled():
        return None

    # The mailbox is the person's email address. LDAP fills it in at login; a
    # local account created by hand may not have one, and there is nothing to
    # ask Microsoft about in that case.
    upn = (getattr(user, 'email', '') or '').strip()
    if not upn:
        return None

    start = datetime.combine(date.today(), time.min)
    entries, _error = msgraph.calendar_for(
        upn, start, start + timedelta(days=1),
        tz=getattr(dj_settings, 'TIME_ZONE', 'UTC') or 'UTC',
        # A few more than the card shows, so "+N more today" can be counted
        # without a second request.
        limit=want + 10,
    )
    return entries


def today_events(request, want):
    """Today's events for this person, and where they came from.

    Returns (events, total, source). Shared by the agenda card and the greeting
    header so the two cannot disagree about what is on today — which is the
    whole reason this is a function rather than the same query written twice.
    """
    from content.models import CalendarEvent

    entries = _outlook_today(request, want)
    if entries is not None:
        return entries[:want], len(entries), 'outlook'

    today = date.today()
    qs = CalendarEvent.objects.filter(
        Q(date=today) | Q(date__lte=today, end_date__gte=today)
    ).order_by('time', 'date')
    return list(qs[:want]), qs.count(), 'hub'


def _is_over(event, now):
    """Whether this event has already finished.

    Written to work on both shapes the agenda can be handed: the Hub's own event
    spells the finish `end_time`, an Outlook one spells it `end`, and neither is
    guaranteed to be set.

    An all-day event is never over during its own day, and one with no start time
    cannot be placed on a clock at all. Both stay pending, because a struck-out
    line is a claim about the past and neither of those supports it.

    Compared as datetimes rather than as times: an event starting at 23:45 with
    no finish would otherwise be assumed to end at 00:15, which is earlier than
    any evening clock and would show tonight's meeting as already done.
    """
    from content.models import CalendarEvent

    start = getattr(event, 'time', None)
    if getattr(event, 'all_day', False) or not start:
        return False

    today = now.date()
    start_at = datetime.combine(today, start)
    end = getattr(event, 'end_time', None) or getattr(event, 'end', None)
    # A finish at or before the start is no finish at all, so it falls back to
    # the same half hour the calendar already assumes for a block without one.
    end_at = (datetime.combine(today, end) if end and end > start
              else start_at + timedelta(minutes=CalendarEvent.DEFAULT_MINUTES))
    return end_at <= now


def _week_event_count():
    """Events on the Hub calendar between today and a week out.

    The figure under today's agenda. Everything is counted, meetings included:
    the card is a reading of the person's own week, not of what AJi is putting
    on, so filtering to company events would have the number disagreeing with
    the rows above it.
    """
    from content.models import CalendarEvent
    today = date.today()
    return (CalendarEvent.objects
            .filter(date__gte=today, date__lte=today + timedelta(days=6))
            .count())


def agenda(section, request):
    """Today's agenda — Outlook when it is available, the Hub's calendar if not.

    Strictly today, including a multi-day event whose span covers today.
    Sharing this rule with the homepage is deliberate: the two used to disagree,
    which is why a meeting on the 28th appeared under "today's agenda".

    What is still to come wins the card. Anything already finished stays visible,
    struck through, but only in the room left over: on a day with six meetings
    and three slots, the card shows the three still ahead rather than this
    morning's stand-up. By the afternoon the same card is mostly struck through,
    which is the honest picture of a day that is nearly done.
    """
    n = _limit(section, 3)
    # More than the card shows, so the finished ones can be passed over without
    # the card coming up short of the pending ones sitting behind them.
    events, total, source = today_events(request, n + 12)

    now = datetime.now()
    for event in events:
        event.is_over = _is_over(event, now)
    pending = [e for e in events if not e.is_over]
    over = [e for e in events if e.is_over]

    # Pending first, then the most recently finished fill whatever is left.
    keep = {id(e) for e in pending[:n]}
    for event in reversed(over):
        if len(keep) >= n:
            break
        keep.add(id(event))

    # Filtered rather than re-sorted, so the day still reads top to bottom in
    # whichever order the calendar that answered put it in.
    shown = [e for e in events if id(e) in keep]
    for event in shown:
        # The marker rail means "next up", not "first row" — once a finished
        # event can sit above a pending one, those are different rows.
        event.is_next = False
    for event in shown:
        if not event.is_over:
            event.is_next = True
            break

    # What "+N more" counts is what is still to come and did not fit, plus
    # anything today beyond what was fetched — which, the day being in order, is
    # later than everything here and therefore also still ahead.
    unfetched = max(0, total - len(events))
    more = max(0, len(pending) - n) + unfetched
    return {'events': shown, 'more': more, 'source': source,
            'all_done': bool(shown) and not pending,
            # The figure under the rows. Counted rather than inferred from
            # `shown`, which is only today and only what fitted.
            'week_count': _week_event_count()}


def project_reel(section, request):
    """The three newest projects, for the homepage strip.

    There was a curated list behind this: editors put projects on a carousel and
    arranged them, and the strip showed that if it was set and fell back to the
    newest if it was not. Two ways for the same three tiles to be chosen meant
    the homepage could not be explained without opening the portfolio manager,
    and a strip that is stale is worse than one that is merely automatic. It is
    the newest three now, always, and there is nothing to maintain.

    nulls_last is spelled out for the same reason the portfolio page spells it
    out: SQLite sorts NULL below every number so a descending sort happens to put
    undated projects last, while PostgreSQL sorts NULL above and would open the
    strip on blanks.
    """
    from django.db.models import F

    from content.models import Project
    newest = list(Project.objects
                  .order_by(F('year').desc(nulls_last=True), 'title', 'pk')
                  [:_limit(section, 3)])
    return {'projects': newest}


#: How long the strip trusts its own answer about which covers are on disk.
#: Short, so restoring a missing file shows up without a restart.
_BOOTH_TTL = 300


def booth_reel(section, request):
    """AJi Booth albums, newest first, for the homepage strip.

    Albums rather than loose photos: an album carries a name — "Orphans Iftar",
    "Career Fair" — and somewhere to go when clicked, where a bare grid of
    pictures carries neither.

    The file is checked, not just the field. One album on this site has nine
    photo records and no files at all behind them, so a cover that is set and a
    cover that exists are different things — and the strip is the one block on
    the page whose entire content is pictures, where a broken one is not a
    degraded tile but a hole. Checking costs a stat per album, so the answer is
    cached for a few minutes rather than taken on every homepage load.
    """
    from django.core.cache import cache
    from portal.models import BoothAlbum

    want = _limit(section, 8, cap=16)
    key = f'booth_reel:ids:{want}'
    ids = cache.get(key)
    if ids is None:
        # A few more than needed, so albums dropped for missing files do not
        # leave the strip short.
        candidates = (BoothAlbum.objects.exclude(cover_photo='')
                      .exclude(cover_photo__isnull=True)
                      .order_by('order', '-id')[:want + 6])
        ids = []
        for album in candidates:
            try:
                if album.cover_photo.storage.exists(album.cover_photo.name):
                    ids.append(album.pk)
            except (OSError, ValueError):
                continue
            if len(ids) >= want:
                break
        cache.set(key, ids, _BOOTH_TTL)

    if not ids:
        return {'albums': []}
    # Counted in the query. The template used to ask each album for its own
    # photo count, twice per tile — twenty extra queries on every homepage.
    #
    # Named photos_total, not n_photos: BoothAlbum already has an n_photos
    # property, and Django sets an annotation with setattr. Against a property
    # with no setter that raises AttributeError for the whole queryset, which
    # took out this block entirely — the strip showed "No albums yet" while
    # thirteen albums sat behind it.
    from django.db.models import Count
    by_id = (BoothAlbum.objects.filter(pk__in=ids)
             .annotate(photos_total=Count('photos'))
             .in_bulk())
    return {'albums': [by_id[i] for i in ids if i in by_id]}


def birthdays(section, request):
    """Everybody whose birthday it is today — all of them.

    This used to stop at three, which on a six-birthday day quietly left three
    people out of their own block. A birthday list that omits people is worse
    than no list: the ones missing are the ones nobody wishes.
    """
    from people.models import Employee
    today = date.today().strftime('%m-%d')
    return {'employees': list(Employee.objects.filter(birthday=today, is_active=True)
                              .select_related('entity'))}


def community(section, request):
    """The reader's own communities, and what has been asked in them.

    The community list was already scoped to the reader; the thread list was not.
    It took the newest threads across every community on the Hub, so a block
    titled "From your communities" showed questions from communities the reader
    had never joined — and, on a private one, would have shown them to somebody
    with no right to look. Both halves now come from the same membership.
    """
    from exchange.models import Community, Thread
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'communities': [], 'threads': []}

    mine = Community.objects.filter(members=user)
    communities = list(mine[:_limit(section, 6)])
    # Three, on its own key. At four the card grew past the recognition block
    # beside it in the same grid row, which then stretched to match and left a
    # band of empty white under its last row. Both blocks stop at the same line
    # now; `threads` in the block's settings is what moves it.
    threads = list(Thread.objects
                   .filter(community__in=mine)
                   .select_related('community', 'author')
                   .order_by('-created_at')[:_cfg_int(section, 'threads', 3)])
    return {'communities': communities, 'threads': threads}


def birthdays_soon():
    """Whose birthday it is, today or else tomorrow.

    Returns (people, when). Falling through to tomorrow is what stops the tile
    being blank most days — with 1,225 birthdays on file roughly three fall on
    any given date, but plenty of dates have none at all.

    Everybody, not a slice. This capped the hover card at twelve on the grounds
    that nobody wants forty faces in it, which is true of the card and not of
    the count beside it — the header said "6 today" and then listed three. The
    list scrolls instead, so a long day costs height inside the card rather
    than people off the end of it.
    """
    from people.models import Employee
    today = date.today()
    for offset, when in ((0, 'today'), (1, 'tomorrow')):
        key = (today + timedelta(days=offset)).strftime('%m-%d')
        people = list(Employee.objects.filter(birthday=key, is_active=True)
                      .select_related('entity'))
        if people:
            return people, when
    return [], ''


# ── the header's one derived reading: how long you have been signed in ──


def _clocked_in(request):
    """When this person signed in, if it was today.

    The masthead has carried a live wall clock under the label "Clocked in" for
    as long as it has existed, which is a time the reader can read off their own
    taskbar and is not, in any sense, when they clocked in. There is no
    attendance system in the Hub to ask, so the honest nearest thing is the
    session: `last_login` is stamped when the sign-in happens.

    Only today's. A browser left open over midnight keeps the session alive, and
    "clocked in 09:04" against a login two days ago is a wrong number rather
    than a stale one — the caller falls back to the wall clock instead.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None
    since = getattr(user, 'last_login', None)
    # USE_TZ is off, so this is already local; an aware value would only
    # subtract cleanly against now() if both sides agreed, hence the guard.
    if not since or since.tzinfo is not None or since.date() != date.today():
        return None
    return since


def _clocked_secs(request):
    """How long ago the sign-in was, in whole seconds. 0 when there is none."""
    since = _clocked_in(request)
    return int((datetime.now() - since).total_seconds()) if since else 0


def greeting(section, request):
    """The header: who you are, and the three things worth knowing right now.

    It used to carry a name and a clock, both of which the reader already knew.
    """
    from people.models import Employee
    user = getattr(request, 'user', None)
    employee = None
    if user is not None and user.is_authenticated:
        employee = Employee.objects.filter(user=user).first()

    # Enough of today to find what is next and to count the rest.
    events, total_today, _source = today_events(request, 25)

    # The next thing still to come. An all-day entry has no time and cannot be
    # "next", and once the last meeting has passed there is nothing to show
    # rather than yesterday's leftovers.
    now = datetime.now().time()
    upcoming = [e for e in events if getattr(e, 'time', None) and e.time >= now]
    next_event = upcoming[0] if upcoming else None

    people, when = birthdays_soon()

    # AJi's own events inside the next week — the second half of the workspace
    # card. Company events only, and kept apart from the agenda above them:
    # a town hall and a one-to-one are not the same kind of thing, and a single
    # merged list made the reader work out which was which from the title.
    from content.models import CalendarEvent
    today = date.today()
    week = list(CalendarEvent.objects
                .filter(date__gte=today, date__lte=today + timedelta(days=7))
                .exclude(event_type__in=PERSONAL_EVENT_TYPES)
                .order_by('date', 'time')[:8])

    # Today, in order, with the finished ones marked. This is the agenda card
    # that used to sit in the left column of the page; it is a hover on the
    # masthead now, so the first screen is the greeting and the tiles rather
    # than the greeting, the tiles and a list nobody had asked to see yet.
    day = []
    for e in events:
        e.is_over = _is_over(e, datetime.now())
        day.append(e)
    day = day[:6]

    # The greeting word, decided here rather than in JavaScript. It used to be
    # written in by a script on the homepage only, so the block rendered "Good
    # morning," at four in the afternoon anywhere else it was placed — and it
    # flashed the wrong word on every load even on the homepage.
    hour = datetime.now().hour
    part = 'morning' if hour < 12 else 'afternoon' if hour < 18 else 'evening'

    # Employee carries one `full_name` field, so the first name has to be split
    # out here — a template cannot do it without `truncatewords` hanging an
    # ellipsis off the end of it. The masthead is addressed to a person and uses
    # the short form; the strip below it and every other block stay on the full
    # name.
    display = (employee.full_name if employee
               else (user.get_full_name() or user.username) if user and user.is_authenticated
               else '')
    first = display.split()[0] if display.strip() else ''

    return {
        'employee': employee,
        'next_event': next_event,
        'day_events': day,
        'day_count': total_today,
        'birthday_people': people,
        'birthday_when': when,
        'birthday_count': len(people),
        'week_events': week,
        'week_count': len(week),
        'greet_word': f'Good {part},',
        'display_name': display,
        'first_name': first,
        'clocked_in': _clocked_in(request),
        # Seconds elapsed, counted here rather than in the browser. The template
        # could hand the sign-in timestamp to JavaScript and let it subtract,
        # but a naive ISO string is read as *browser* local time — so a laptop
        # still set to another country's zone would show an elapsed figure hours
        # out. The script ticks up from this number instead, which is right
        # wherever it is read.
        'clocked_secs': _clocked_secs(request),
    }


def policy_list(section, request):
    from portal.models import Policy
    return {'policies': list(Policy.objects.all()[:_limit(section, 6)])}


def taqdeer(section, request):
    """The recognition strip for the homepage.

    Newest first, mixing the two kinds so the block has a pulse even in a week
    nobody won an award: the Athar recipients lead, then the latest Kudos.
    """
    from taqdeer.models import (AwardCycle, Certification, Nomination, Praise,
                                TaqdeerSettings)

    # ── the Athar leads ──
    # Nothing caps how many nominations HR may approve in a cycle, so co-
    # recipients are the normal case rather than an edge one. The lead is the
    # *quarter*, not the person: one winner or six, it is the same block with a
    # different number of people in it.
    #
    # The newest cycle that has anybody, not the current calendar quarter — in
    # Q4 the Q3 winners should still lead, with the label saying which quarter
    # they won. Emptying the block for three months between cycles would be
    # tidier and worse.
    lead = list(Nomination.objects
                .filter(status=Nomination.Status.APPROVED)
                .select_related('cycle', 'recipient', 'recipient__employee')
                .order_by('-cycle__year', '-cycle__quarter', 'recipient_name'))
    lead_cycle = lead[0].cycle if lead else None
    # Alphabetical within the cycle, which Meta.ordering already gives: people
    # who won the same award are not ranked against each other.
    athar = [n for n in lead if n.cycle_id == getattr(lead_cycle, 'pk', None)]
    # The limit is the number of rows the block draws, all three kinds counted
    # together. It used to cap each list separately and derive only the praise
    # count from it, so a limit of 4 drew five rows — two awards, two
    # certifications and at least one Kudos — and the setting looked ignored
    # because it was.
    # The block's height is what is being budgeted, not a number of records.
    # An Athar lead is about as tall as two and a half rows, so it spends two of
    # them: six rows with no lead, four underneath one. Without this the block
    # ran two rows past the communities card beside it.
    n = _limit(section, 6)
    s = TaqdeerSettings.get()

    # Awards already in the lead are not repeated as rows underneath it.
    shown = {a.pk for a in athar}
    all_awards = [a for a in lead if a.pk not in shown][:n]

    all_certs = []
    if s.certification_on_homepage:
        all_certs = list(Certification.objects.filter(published=True)
                         .select_related('employee', 'employee__employee')[:n])

    all_praise = list(Praise.objects
                      .filter(hidden_at__isnull=True)
                      .select_related('giver', 'recipient',
                                      'recipient__employee')[:n])

    # Newest first, whatever kind it is.
    #
    # This block filled strictly in order once: awards, then certifications with
    # whatever was left of the limit, then Kudos with whatever was left after
    # that. With five approved Athar recipients and a limit of five there was
    # never anything left, so certifications and Kudos were arithmetically
    # unable to appear — a Kudos sent that morning showed up nowhere.
    #
    # It then took a turn from each kind in rotation, which fixed that but left
    # the order by kind rather than by date, so a Kudos from this morning sat
    # below an award released in the spring. One ordered list now, sorted on
    # when each thing happened, which is what somebody glancing at a block
    # called Recognition is reading it for. The template walks that list, so
    # the three kinds genuinely interleave.
    def when(obj):
        """When this became a recognition. Released, else recorded."""
        return getattr(obj, 'released_at', None) or obj.created_at

    mixed = ([{'kind': 'award', 'obj': o} for o in all_awards]
             + [{'kind': 'cert', 'obj': o} for o in all_certs]
             + [{'kind': 'praise', 'obj': o} for o in all_praise])
    mixed.sort(key=lambda row: when(row['obj']), reverse=True)
    # What the lead has already spent. Counted after the sort so the rows that
    # survive are the newest ones, not whichever happened to be gathered first.
    LEAD_COST = 2
    rows = max(1, n - LEAD_COST) if athar else n
    items = mixed[:rows]

    # Kept beside `items` because the block's own empty state asks whether there
    # is anything at all, and other callers may still count a kind.
    awards = [r['obj'] for r in items if r['kind'] == 'award']
    certs = [r['obj'] for r in items if r['kind'] == 'cert']
    praise = [r['obj'] for r in items if r['kind'] == 'praise']
    return {
        'items': items,
        # The lead, and the three states the template draws it in. Decided here
        # rather than with {% if %} arithmetic in the template, so the numbers
        # live next to the query that produced them.
        'athar': athar,
        'athar_cycle': lead_cycle,
        'athar_solo': athar[0] if len(athar) == 1 else None,
        'athar_few': athar if 2 <= len(athar) <= 3 else [],
        'athar_many': athar if len(athar) > 3 else [],
        'awards': awards,
        'certs': certs,
        'praise': praise,
        'open_cycle': AwardCycle.current(),
        'n_praise': Praise.objects.filter(hidden_at__isnull=True).count(),
    }


def needs_you(section, request):
    """The one block on the homepage that is actually about the person reading it.

    Everything else on the page is the company talking: news, birthdays, who won
    an award. This answers the only question somebody opens an intranet with —
    *is anything waiting on me?* — and it is deliberately the first thing in the
    left column, because a dashboard that makes you hunt for your own work is a
    noticeboard with a login.

    Each source is wrapped: a module that is half-configured, or a model that
    moves under this, must not take the homepage down with it.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'items': [], 'total': 0}

    items = []

    # Job requisitions sitting at a stage this person is the one to clear.
    try:
        from services.models import JobRequisition
        from services.views import _can_act
        pending = (JobRequisition.objects
                   .exclude(status__in=[JobRequisition.Status.APPROVED,
                                        JobRequisition.Status.REJECTED])
                   .select_related('requested_by', 'department'))
        for r in pending:
            if _can_act(user, r):
                items.append({
                    'kind': 'Approval', 'icon': 'check',
                    'title': str(getattr(r, 'job_title', None) or r),
                    'meta': f'Job requisition from '
                            f'{r.requested_by.get_full_name() or r.requested_by.username}',
                    'url': f'/services/job-requisition/{r.pk}/', 'urgent': True})
    except Exception:
        pass

    # Training the person has to act on themselves — HR or their manager sent
    # it back and it sits in their drafts until they do.
    try:
        from training.models import TrainingRequest
        for t in (TrainingRequest.objects
                  .filter(employee=user, stage__in=list(TrainingRequest.EDITABLE_STAGES))
                  .order_by('-updated_at')[:4]):
            items.append({
                'kind': 'Returned', 'icon': 'arrow-bend-up-left',
                'title': str(getattr(t, 'course_title', None) or t),
                'meta': f'Training request · {t.get_stage_display()}',
                'url': f'/training/{t.pk}/', 'urgent': True})
    except Exception:
        pass

    # Meetings waiting on an answer.
    try:
        from services.models import BookingInvite
        for inv in (BookingInvite.objects
                    .filter(user=user, response='pending')
                    .select_related('booking')[:4]):
            b = inv.booking
            items.append({
                'kind': 'Invitation', 'icon': 'calendar',
                'title': str(getattr(b, 'title', None) or b),
                'meta': 'Meeting invitation · awaiting your reply',
                'url': '/services/room-booking/', 'urgent': False})
    except Exception:
        pass

    # An Athar nomination HR sent back for rewording.
    try:
        from taqdeer.models import Nomination
        for n in Nomination.objects.filter(nominated_by=user,
                                           status=Nomination.Status.RETURNED)[:3]:
            items.append({
                'kind': 'Returned', 'icon': 'arrow-bend-up-left',
                'title': n.recipient_name,
                'meta': f'Athar nomination · {n.hr_note or "HR asked for a change"}',
                'url': '/taqdeer/nominate/', 'urgent': False})
    except Exception:
        pass

    items.sort(key=lambda i: (not i['urgent'],))
    n = _limit(section, 5)
    return {'items': items[:n], 'total': len(items), 'more': max(0, len(items) - n)}


#: type value -> provider. A type absent here simply gets no extra context.
PROVIDERS = {
    'news_list': news_list,
    'taqdeer': taqdeer,
    'needs_you': needs_you,
    'news_feed': news_list,
    'event_list': event_list,
    'agenda': agenda,
    'project_reel': project_reel,
    'booth_reel': booth_reel,
    'birthdays': birthdays,
    'community': community,
    'greeting': greeting,
    'policy_list': policy_list,
}


def context_for(section, request):
    """Extra context for one section. Never raises — a page must not 500
    because one block's data source is unhappy."""
    fn = PROVIDERS.get(section.type)
    if fn is None:
        return {}
    try:
        return fn(section, request) or {}
    except Exception:
        return {}
