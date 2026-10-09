# content/views.py
import json

from django.db import models, transaction
from django.db.models import F
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from datetime import date

from django.contrib import messages
from django.urls import reverse
from django.utils.dateparse import parse_date, parse_time
from django.utils.text import slugify
from django.core.paginator import Paginator

from .models import (NewsArticle, CalendarEvent, HRAnnouncement, HRAttachment,
                     HRCategory, NewsLike, NewsComment, NewsAttachment,
                     NewsCategory, Project, WhatsAppChannel)
from core.decorators import content_editor_required, hr_required
from core.comments import apply_delete, apply_edit
from core.mentions import notify_mentions
from core.models import user_is_hr

#: How many updates the People & Culture slider holds. An upper bound rather than
#: a page size: the slider shows four at a time and steps through the rest, but
#: every one of these is rendered into the page, so it cannot be unbounded.
HR_MAX_SHOWN = 24


def _unique_slug(model, title, instance=None):
    """Slugify `title`, adding a numeric suffix until it is unique."""
    base = slugify(title) or 'item'
    slug = base
    n = 2
    while True:
        clash = model.objects.filter(slug=slug)
        if instance is not None and instance.pk:
            clash = clash.exclude(pk=instance.pk)
        if not clash.exists():
            return slug
        slug = f"{base}-{n}"
        n += 1


# ── NEWS ──

@login_required
def news_list(request):
    from datetime import timedelta
    from django.core.paginator import Paginator
    from django.db.models import Count

    cat  = request.GET.get('cat', '')
    sort = request.GET.get('sort', 'new')

    base = NewsArticle.objects.select_related('category')
    # HR sees its own unpublished drafts in the feed so they can be finished;
    # everyone else only ever sees published articles.
    if not user_is_hr(request.user):
        base = base.filter(published=True)

    articles = base
    if cat:
        articles = articles.filter(category__slug=cat)

    articles = articles.annotate(
        n_likes=Count('likes', distinct=True),
        n_comments=Count('comments', distinct=True),
    )
    if sort == 'old':
        articles = articles.order_by('date', 'created_at')
    elif sort == 'popular':
        articles = articles.order_by('-n_likes', '-n_comments', '-date')
    elif sort == 'discussed':
        articles = articles.order_by('-n_comments', '-date')
    else:
        # Pinned articles ride at the top of the default (newest) ordering.
        articles = articles.order_by('-pinned', '-date', '-created_at')

    paginator = Paginator(articles, 8)
    page = paginator.get_page(request.GET.get('page'))
    # Django collapses long runs into Paginator.ELLIPSIS ('…') for us.
    page_range = list(paginator.get_elided_page_range(
        page.number, on_each_side=1, on_ends=1))

    # Querystring for pagination links that preserves the active filters.
    keep = []
    if cat:
        keep.append(f'cat={cat}')
    if sort and sort != 'new':
        keep.append(f'sort={sort}')
    filter_qs = ('&' + '&'.join(keep)) if keep else ''

    # ── sidebar ───────────────────────────────────────────────────────────
    today = date.today()
    week_ago = today - timedelta(days=7)
    month_start = today.replace(day=1)

    trending = list(
        base.annotate(
            n_likes=Count('likes', distinct=True),
            n_comments=Count('comments', distinct=True),
        )
        .filter(date__gte=week_ago)
        .order_by('-n_likes', '-n_comments', '-views', '-date')[:5]
    )
    # Early on there may be nothing published in the last week; fall back to
    # the most-read overall so the panel is never an empty box.
    trending_window = 'this week'
    if not trending:
        trending = list(
            base.annotate(
                n_likes=Count('likes', distinct=True),
                n_comments=Count('comments', distinct=True),
            ).order_by('-views', '-n_likes', '-date')[:5]
        )
        trending_window = 'all time'

    categories = (NewsCategory.objects
                  .annotate(n=Count('newsarticle',
                                    filter=models.Q(newsarticle__published=True)))
                  .order_by('-n', 'name'))

    recent_comments = (NewsComment.objects
                       .select_related('author', 'article')
                       .order_by('-created_at')[:4])

    stats = {
        'total':    base.count(),
        'month':    base.filter(date__gte=month_start).count(),
        'authors':  base.exclude(author_name='')
                        .values('author_name').distinct().count(),
        'comments': NewsComment.objects.count(),
    }

    upcoming = CalendarEvent.objects.filter(date__gte=today).order_by('date', 'time')[:3]

    return render(request, 'content/news_list.html', {
        'page_obj':        page,
        'articles':        page.object_list,
        'paginator':       paginator,
        'page_range':      page_range,
        'filter_qs':       filter_qs,
        'categories':      categories,
        'selected_cat':    cat,
        'selected_sort':   sort,
        'trending':        trending,
        'trending_window': trending_window,
        'recent_comments': recent_comments,
        'stats':           stats,
        'upcoming':        upcoming,
    })


@login_required
def news_detail(request, slug):
    # HR can open its own unpublished drafts in order to finish them.
    if user_is_hr(request.user):
        article = get_object_or_404(NewsArticle, slug=slug)
    else:
        article = get_object_or_404(NewsArticle, slug=slug, published=True)

    if request.method == 'GET':
        # Count the read. F() keeps the increment atomic instead of doing a
        # read-modify-write that would lose concurrent hits.
        NewsArticle.objects.filter(pk=article.pk).update(views=models.F('views') + 1)
        article.views += 1

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'like':
            obj, created = NewsLike.objects.get_or_create(article=article, user=request.user)
            if not created:
                obj.delete()
            # Return JSON only for XHR requests, redirect for plain form posts
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'likes': article.likes.count()})
            return redirect('news_detail', slug=slug)

        elif action == 'comment':
            text = request.POST.get('text', '').strip()
            if text:
                comment = NewsComment.objects.create(
                    article=article, author=request.user, text=text)
                who = request.user.get_full_name() or request.user.username
                notified = notify_mentions(
                    text, request.user,
                    title=f"{who} mentioned you in a comment",
                    link=reverse('news_detail', args=[article.slug]),
                    body=text,
                    case='news.mentioned',
                    values={'actor': who, 'comment': text},
                )
                if notified:
                    comment.mentions.set(notified)
            return redirect('news_detail', slug=slug)

        elif action == 'attach' and user_is_hr(request.user):
            upload = request.FILES.get('file')
            if upload:
                NewsAttachment.objects.create(
                    article=article,
                    file=upload,
                    caption=request.POST.get('caption', '').strip(),
                    uploaded_by=request.user,
                )
                messages.success(request, 'Attachment added.')
            return redirect('news_detail', slug=slug)

        elif action == 'attach_delete' and user_is_hr(request.user):
            att = article.attachments.filter(
                pk=request.POST.get('attachment_id')).first()
            if att:
                att.file.delete(save=False)
                att.delete()
                messages.success(request, 'Attachment removed.')
            return redirect('news_detail', slug=slug)

    liked = article.likes.filter(user=request.user).exists()
    comments = article.comments.select_related('author').prefetch_related('mentions')
    # What to read next, in the rail beside the article. Newest first, and the
    # same category leads where there is one — "more like this" beats "more".
    others = list(NewsArticle.objects.filter(published=True, category=article.category)
                  .exclude(pk=article.pk)
                  .select_related('category')
                  .order_by('-pinned', '-date', '-created_at')[:4]
                  if article.category_id else [])
    if len(others) < 4:
        seen = {a.pk for a in others} | {article.pk}
        others += list(NewsArticle.objects.filter(published=True)
                       .exclude(pk__in=seen)
                       .select_related('category')
                       .order_by('-pinned', '-date', '-created_at')[:4 - len(others)])

    # Topics for the rail, with counts, so it is a way in rather than a list of
    # words. Empty categories are dropped — a topic with nothing under it is a
    # link to an empty page.
    from django.db.models import Count
    rail_categories = [c for c in NewsCategory.objects
                       .annotate(n=Count('newsarticle',
                                         filter=models.Q(newsarticle__published=True)))
                       .order_by('-n', 'name') if c.n]

    attachments = list(article.attachments.all())
    return render(request, 'content/news_detail.html', {
        'article': article,
        'liked': liked,
        'comments': comments,
        'others': others,
        'rail_categories': rail_categories,
        'attachments': attachments,
        # Split here rather than filtered twice in the template. The pictures go
        # into a gallery and the rest into a download list, and `is_image` is a
        # Python property — so a template filtering on it walks the whole list
        # once per section and cannot count either one.
        'images': [a for a in attachments if a.is_image],
        'files': [a for a in attachments if not a.is_image],
    })


# ── NEWS: HR create / edit / delete ──

# Covers are stored and displayed at one fixed ratio, so the framing chosen in
# the editor is exactly what appears on the article — nothing is re-cropped.
COVER_W, COVER_H = 1600, 900


def _apply_cover(request, obj, upload_field='image'):
    """Store a cover image from the crop tool, or fall back to a raw upload.

    Shared by news articles and HR updates so both honour the framing the
    author chose instead of letting CSS crop the picture a second time.
    """
    cropped = request.POST.get('cover_cropped', '')
    if cropped.startswith('data:'):
        import base64
        from django.core.files.base import ContentFile
        try:
            _, payload = cropped.split(',', 1)
            stem = (request.POST.get('cover_filename') or 'cover').rsplit('.', 1)[0]
            getattr(obj, upload_field).save(
                f"{slugify(stem) or 'cover'}.jpg",
                ContentFile(base64.b64decode(payload)), save=False)
            return True
        except (ValueError, TypeError):
            return False
    if request.FILES.get(upload_field):
        setattr(obj, upload_field, request.FILES[upload_field])
        return True
    return False


def _has_cover_input(request, existing):
    """True when the request supplies a cover, or one is already stored."""
    return bool(
        request.POST.get('cover_cropped', '').startswith('data:')
        or request.FILES.get('image')
        or existing
    )


def _save_article_from_post(request, article):
    """Apply the news form fields from `request` onto `article` and save it."""
    article.title = request.POST.get('title', '').strip()
    article.body = request.POST.get('body', '').strip()
    article.excerpt = request.POST.get('excerpt', '').strip()[:400]
    article.author_name = (request.POST.get('author_name', '').strip()
                           or request.user.get_full_name())
    article.published = request.POST.get('published') == 'on'
    article.pinned = request.POST.get('pinned') == 'on'

    cat_id = request.POST.get('category')
    article.category = NewsCategory.objects.filter(pk=cat_id).first() if cat_id else None

    date_raw = request.POST.get('date', '').strip()
    article.date = parse_date(date_raw) or article.date or date.today()

    slug_in = slugify(request.POST.get('slug', '').strip())
    if slug_in:
        article.slug = slug_in
    if not article.slug:
        article.slug = _unique_slug(NewsArticle, article.title, article)

    if request.POST.get('remove_image') == 'on' and article.image:
        article.image.delete(save=False)
        article.image = None

    _apply_cover(request, article)

    article.save()
    return article


def _article_form_errors(request, article):
    """Validate the news form. Returns {field: message}, empty when valid."""
    errors = {}
    if not request.POST.get('title', '').strip():
        errors['title'] = 'Give the article a title.'
    if not request.POST.get('body', '').strip():
        errors['body'] = 'Write something in the body.'

    date_raw = request.POST.get('date', '').strip()
    if date_raw and not parse_date(date_raw):
        errors['date'] = "That date doesn't look right."

    slug_in = slugify(request.POST.get('slug', '').strip())
    if slug_in:
        clash = NewsArticle.objects.filter(slug=slug_in)
        if article.pk:
            clash = clash.exclude(pk=article.pk)
        if clash.exists():
            errors['slug'] = 'Another article already uses that link.'
    return errors


def _article_form_values(request, article):
    """What the form should show: the submitted data when redisplaying after a
    validation error, otherwise the saved article, otherwise new-article
    defaults. Without this a rejected submission would wipe the author's work."""
    if request.method == 'POST':
        return {
            'title': request.POST.get('title', ''),
            'slug': request.POST.get('slug', ''),
            'excerpt': request.POST.get('excerpt', ''),
            'body': request.POST.get('body', ''),
            'author_name': request.POST.get('author_name', ''),
            'date': request.POST.get('date', ''),
            'category': request.POST.get('category', ''),
            'published': request.POST.get('published') == 'on',
            'pinned': request.POST.get('pinned') == 'on',
        }
    if article.pk:
        return {
            'title': article.title,
            'slug': article.slug,
            'excerpt': article.excerpt,
            'body': article.body,
            'author_name': article.author_name,
            'date': article.date.strftime('%Y-%m-%d') if article.date else '',
            'category': str(article.category_id or ''),
            'published': article.published,
            'pinned': article.pinned,
        }
    return {
        'title': '', 'slug': '', 'excerpt': '', 'body': '',
        'author_name': request.user.get_full_name(),
        'date': date.today().strftime('%Y-%m-%d'),
        'category': '', 'published': True, 'pinned': False,
    }


def _render_article_form(request, article, errors):
    return render(request, 'content/news_form.html', {
        'article': article if article.pk else None,
        'form': _article_form_values(request, article),
        'errors': errors,
        'categories': NewsCategory.objects.all(),
        'cover_w': COVER_W,
        'cover_h': COVER_H,
    })


@login_required
@hr_required
def news_create(request):
    article = NewsArticle()
    errors = {}
    if request.method == 'POST':
        errors = _article_form_errors(request, article)
        if not errors:
            _save_article_from_post(request, article)
            for upload in request.FILES.getlist('attachments'):
                NewsAttachment.objects.create(
                    article=article, file=upload, uploaded_by=request.user)
            messages.success(request, 'Article published.' if article.published
                             else 'Draft saved.')
            return redirect('news_detail', slug=article.slug)
    return _render_article_form(request, article, errors)


@login_required
@hr_required
def news_edit(request, slug):
    article = get_object_or_404(NewsArticle, slug=slug)
    errors = {}
    if request.method == 'POST':
        errors = _article_form_errors(request, article)
        if not errors:
            _save_article_from_post(request, article)
            for upload in request.FILES.getlist('attachments'):
                NewsAttachment.objects.create(
                    article=article, file=upload, uploaded_by=request.user)
            messages.success(request, 'Article updated.')
            return redirect('news_detail', slug=article.slug)
    return _render_article_form(request, article, errors)


@login_required
@hr_required
def news_delete(request, slug):
    article = get_object_or_404(NewsArticle, slug=slug)
    if request.method == 'POST':
        article.delete()
        messages.success(request, 'Article deleted.')
        return redirect('news_list')
    return render(request, 'content/news_confirm_delete.html', {'article': article})


# ── CALENDAR ──

def _event_payload(ev):
    """Everything the calendar UI needs to render — and for HR, to edit — an event."""
    return {
        'id':          ev.pk,
        't':           ev.title,
        'type':        ev.event_type,
        'type_label':  ev.get_event_type_display(),
        'date':        ev.date.strftime('%Y-%m-%d'),
        'end_date':    ev.end_date.strftime('%Y-%m-%d') if ev.end_date else '',
        'time':        ev.time.strftime('%H:%M') if ev.time else '',
        'end':         ev.end_time.strftime('%H:%M') if ev.end_time else '',
        # What the week view needs to draw a block whose height is its length.
        # Without these an event could only ever be a fixed-size chip, so an
        # hour and four hours looked identical.
        'start_min':   ev.start_minutes,
        'mins':        ev.duration_minutes,
        'range':       ev.time_range,
        'location':    ev.location,
        'description': ev.description,
        'all_day':     ev.all_day,
        'is_public':   ev.is_public,
    }


@login_required
def calendar_view(request):
    from datetime import timedelta
    import calendar as cal_mod

    from core.models import UserPreference
    from services.models import MeetingRoom

    today = date.today()

    # Whichever layout this person last chose, still chosen when they come back.
    # A ?view= in the address wins and is remembered; without one, the remembered
    # choice applies. Written only when it actually changes, so ordinary paging
    # through the months is not a write per page load.
    valid = {c for c, _ in UserPreference.CalendarView.choices}
    asked = request.GET.get('view')
    prefs = UserPreference.for_user(request.user)
    if asked in valid:
        cal_view = asked
        if prefs.calendar_view != asked:
            prefs.calendar_view = asked
            prefs.save(update_fields=['calendar_view'])
    else:
        cal_view = prefs.calendar_view or 'month'

    try:
        year  = int(request.GET.get('y', today.year))
        month = int(request.GET.get('m', today.month))
    except ValueError:
        year, month = today.year, today.month

    if month < 1:  month = 12; year -= 1
    if month > 12: month = 1;  year += 1

    # ── MONTH VIEW data ──────────────────────────────────────────────────────
    _, last_day  = cal_mod.monthrange(year, month)
    month_start  = date(year, month, 1)
    month_end    = date(year, month, last_day)
    first_weekday = (month_start.weekday() + 1) % 7   # 0=Sun

    month_events = CalendarEvent.objects.filter(
        date__lte=month_end,
    ).filter(
        models.Q(end_date__gte=month_start) | models.Q(end_date__isnull=True, date__gte=month_start)
    ).order_by('date', 'time')

    event_map = {}   # day_num -> [ev, ...]
    for ev in month_events:
        span_start = max(ev.date, month_start)
        span_end   = min(ev.end_date or ev.date, month_end)
        d = span_start
        while d <= span_end:
            event_map.setdefault(d.day, []).append(ev)
            d += timedelta(days=1)

    # ── YEAR VIEW data ───────────────────────────────────────────────────────
    year_start = date(year, 1, 1)
    year_end   = date(year, 12, 31)

    all_year_events = CalendarEvent.objects.filter(
        date__lte=year_end,
    ).filter(
        models.Q(end_date__gte=year_start) | models.Q(end_date__isnull=True, date__gte=year_start)
    ).order_by('date', 'time')

    # year_event_map: "YYYY-MM-DD" -> [ev, ...]
    year_event_map = {}
    for ev in all_year_events:
        span_start = max(ev.date, year_start)
        span_end   = min(ev.end_date or ev.date, year_end)
        d = span_start
        while d <= span_end:
            key = d.strftime('%Y-%m-%d')
            year_event_map.setdefault(key, []).append(ev)
            d += timedelta(days=1)

    # Build mini-month data for all 12 months (for year view)
    mini_months = []
    for m in range(1, 13):
        _, mdays = cal_mod.monthrange(year, m)
        mstart   = date(year, m, 1)
        fw       = (mstart.weekday() + 1) % 7   # 0=Sun
        mini_months.append({
            'month':       m,
            'name':        mstart.strftime('%B'),
            'first_wd':    fw,
            'last_day':    mdays,
        })

    # Serialize year_event_map for JS
    import json
    year_events_json = json.dumps({
        k: [_event_payload(ev) for ev in evs]
        for k, evs in year_event_map.items()
    })

    # ── WEEK VIEW data ────────────────────────────────────────────────────────
    try:
        anchor_day = int(request.GET.get('d', today.day))
    except ValueError:
        anchor_day = today.day
    anchor = date(year, month, max(1, min(anchor_day, last_day)))
    # Sunday-start week, to match the month grid's column order.
    week_start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
    week_end = week_start + timedelta(days=6)

    week_events = CalendarEvent.objects.filter(
        date__lte=week_end,
    ).filter(
        models.Q(end_date__gte=week_start)
        | models.Q(end_date__isnull=True, date__gte=week_start)
    ).order_by('date', 'time')

    week_map = {}
    for ev in week_events:
        span_start = max(ev.date, week_start)
        span_end = min(ev.end_date or ev.date, week_end)
        d = span_start
        while d <= span_end:
            week_map.setdefault(d, []).append(ev)
            d += timedelta(days=1)

    week_days = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        week_days.append({
            'iso':       d.strftime('%Y-%m-%d'),
            'name':      d.strftime('%a'),
            'num':       d.day,
            'month':     d.month,
            'is_today':  d == today,
            'off_month': d.month != month,
            'events':    [_event_payload(ev) for ev in week_map.get(d, [])],
        })

    prev_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)

    # id -> payload, so the HR edit modal can be filled from a chip click.
    import json as _wjson
    # ── the week's time axis ────────────────────────────────────────────────
    # Bounded by the events themselves, then padded to whole hours and widened to
    # a sensible minimum, so a quiet week is not a sliver and a long day is not
    # clipped.
    _timed = [ev for ev in week_events if ev.start_minutes is not None]
    if _timed:
        _lo = min(ev.start_minutes for ev in _timed)
        _hi = max(ev.start_minutes + (ev.duration_minutes or 30) for ev in _timed)
    else:
        _lo, _hi = 8 * 60, 18 * 60
    wk_from_min = max(0, (_lo // 60) * 60 - 60)
    wk_to_min = min(24 * 60, -(-_hi // 60) * 60 + 60)
    if wk_to_min - wk_from_min < 8 * 60:          # never less than eight hours
        wk_to_min = min(24 * 60, wk_from_min + 8 * 60)
    wk_span_min = wk_to_min - wk_from_min
    wk_hours = [
        {'offset': h * 60 - wk_from_min, 'label': f'{h:02d}:00'}
        for h in range(wk_from_min // 60, wk_to_min // 60 + 1)
        if wk_from_min <= h * 60 <= wk_to_min
    ]

    week_events_json = _wjson.dumps({
        str(ev.pk): _event_payload(ev) for ev in week_events
    })

    # Upcoming events sidebar
    upcoming = CalendarEvent.objects.filter(date__gte=today).order_by('date', 'time')[:6]

    # Serialize mini_months for JS
    import json as _json
    mini_months_json = _json.dumps([
        {'month': mm['month'], 'name': mm['name'], 'first_wd': mm['first_wd'], 'last_day': mm['last_day']}
        for mm in mini_months
    ])

    # Pre-serialize event maps to JSON for safe template embedding
    import json as _json
    event_map_json = _json.dumps({
        str(k): [_event_payload(ev) for ev in evs]
        for k, evs in event_map.items()
    })

    return render(request, 'content/calendar.html', {
        'today':            today,
        'cal_view':         cal_view,
        # month view
        'year':             year,
        'month':            month,
        'month_name':       month_start.strftime('%B %Y'),
        'first_weekday':    first_weekday,
        'last_day':         last_day,
        'event_map':        event_map,
        # year view
        'mini_months':      mini_months,
        'year_events_json': year_events_json,
        'event_map_json':   event_map_json,
        'mini_months_json': mini_months_json,
        # week view
        'week_days':        week_days,
        'week_events_json': week_events_json,
        'wk_from_min':      wk_from_min,
        'wk_span_min':      wk_span_min,
        'wk_hours':         wk_hours,
        'week_start':       week_start,
        'week_end':         week_end,
        'prev_week':        prev_week,
        'next_week':        next_week,
        # shared
        'upcoming':         upcoming,
        'event_types':      CalendarEvent.EventType.choices,
        # For the meeting composer, which any employee may use — as opposed to
        # the event modal beside it, which is HR's and writes to the whole
        # company's calendar.
        'rooms':            MeetingRoom.objects.filter(is_active=True),
        'people_search_url': reverse('exchange_people_search'),
        'prev_y':           year if month > 1 else year - 1,
        'prev_m':           month - 1 if month > 1 else 12,
        'next_y':           year if month < 12 else year + 1,
        'next_m':           month + 1 if month < 12 else 1,
    })


# ── CALENDAR: HR create / edit / delete ──

def _calendar_redirect(request):
    """Send HR back to the month/year they were looking at."""
    nxt = request.POST.get('next') or ''
    # Only ever bounce back into the calendar, never to an arbitrary URL.
    if nxt.startswith('/calendar/'):
        return nxt
    return reverse('calendar')


def _save_event_from_post(request, event):
    """Apply the event form fields from `request` onto `event` and save it."""
    event.title = request.POST.get('title', '').strip()
    event.event_type = (request.POST.get('event_type')
                        or CalendarEvent.EventType.OTHER)
    event.location = request.POST.get('location', '').strip()
    event.description = request.POST.get('description', '').strip()
    event.is_public = request.POST.get('is_public') == 'on'

    event.date = (parse_date(request.POST.get('date') or '')
                  or event.date or date.today())
    event.end_date = parse_date(request.POST.get('end_date') or '') or None
    # A single-day event does not need an end date, and an end before the
    # start is a typo rather than something worth storing.
    if event.end_date and event.end_date <= event.date:
        event.end_date = None

    time_raw = (request.POST.get('time') or '').strip()
    event.time = parse_time(time_raw) if time_raw else None
    # A specific start time and "all day" are mutually exclusive.
    event.all_day = event.time is None and request.POST.get('all_day') != 'off'

    # The end time, which is what makes a duration -- and therefore what lets the
    # week view draw a two-hour meeting as twice the block of a one-hour one.
    end_raw = (request.POST.get('end_time') or '').strip()
    event.end_time = parse_time(end_raw) if end_raw else None
    # An end at or before the start is a typo, and would otherwise be stored and
    # then quietly ignored by duration_minutes. Dropped, so what is saved is what
    # gets drawn.
    if event.end_time and event.time and event.end_time <= event.time:
        event.end_time = None
    if event.time is None:
        event.end_time = None      # nothing to measure from

    if event.created_by_id is None:
        event.created_by = request.user
    event.save()
    return event


@login_required
@hr_required
def event_create(request):
    if request.method == 'POST':
        if not request.POST.get('title', '').strip():
            messages.error(request, 'An event title is required.')
        else:
            ev = _save_event_from_post(request, CalendarEvent())
            messages.success(request, f'"{ev.title}" added to the calendar.')
    return redirect(_calendar_redirect(request))


@login_required
@hr_required
def event_edit(request, pk):
    event = get_object_or_404(CalendarEvent, pk=pk)
    if request.method == 'POST':
        if not request.POST.get('title', '').strip():
            messages.error(request, 'An event title is required.')
        else:
            _save_event_from_post(request, event)
            messages.success(request, 'Event updated.')
    return redirect(_calendar_redirect(request))


@login_required
@hr_required
def event_delete(request, pk):
    event = get_object_or_404(CalendarEvent, pk=pk)
    if request.method == 'POST':
        title = event.title
        event.delete()
        messages.success(request, f'"{title}" removed from the calendar.')
    return redirect(_calendar_redirect(request))


# ── PORTFOLIO ──

#: The portfolio's first screenful, and each press of "Load more". 28 fills the
#: grid at every column count it uses (2, 3 and 4 all divide into it) so the
#: last row is never a lone card sitting under a full one.
PORTFOLIO_FIRST = 28
PORTFOLIO_MORE = 28
PORTFOLIO_MAX = 100


def _portfolio_bounds(request):
    """Offset and limit for a "load more" request, clamped."""
    try:
        offset = max(0, int(request.GET.get('offset') or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(request.GET.get('limit') or PORTFOLIO_MORE)
    except (TypeError, ValueError):
        limit = PORTFOLIO_MORE
    return offset, max(1, min(limit, PORTFOLIO_MAX))


def _project_image(p):
    """The card's picture, or '' when there is not one.

    Empty rather than a stand-in from picsum.photos, which is what this used to
    return for every project without a photograph. That was one external request
    per card at roughly a second each — twenty-eight of them before the first
    screen of the portfolio finished, which is what made the site feel broken.
    A card with no picture is drawn by the browser instead.
    """
    if p.image:
        try:
            return p.image.url
        except (ValueError, OSError):
            pass
    return ''


def _portfolio_queryset(request):
    """The filtered, ordered project list behind the portfolio page.

    Newest first, and the ones with no year after all of them rather than mixed
    in — an undated project is one waiting to be filled in, not one from year
    zero. nulls_last is spelled out because the databases disagree: SQLite sorts
    NULL below every number, so a descending sort happens to put them last,
    while PostgreSQL sorts NULL above and would open the portfolio on 314 blanks.

    Then title, then `pk`. The tiebreaker matters because several projects share
    a title — without it the database may return those in either order per
    query, which across an offset page means one appearing twice and another not
    at all.

    Type is the only filter, plus the search. Division is blank on the whole
    portfolio, and year and status are not recorded for most of it — dropdowns
    that between them could only ever hide projects rather than help find one.
    """
    projects = Project.objects.all()
    ptype = request.GET.get('type', '')
    if ptype:
        projects = projects.filter(type=ptype)
    q = (request.GET.get('q') or '').strip()
    if q:
        projects = projects.filter(_project_search(q))
    return projects.order_by(F('year').desc(nulls_last=True), 'title', 'pk')


def _project_search(q):
    """What a typed word is matched against, in one place.

    The same fields on the portfolio and on the management screen, so a project
    somebody found in one is findable in the other.
    """
    return (models.Q(title__icontains=q) | models.Q(location__icontains=q) |
            models.Q(type__icontains=q) | models.Q(division__icontains=q))


@login_required
def portfolio(request):
    projects = _portfolio_queryset(request)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        offset, limit = _portfolio_bounds(request)
        total = projects.count()
        page = list(projects[offset:offset + limit])
        return JsonResponse({
            'projects': [{
                'pk': p.pk,
                'title': p.title,
                'location': p.location or '',
                'division': p.division or '',
                'type': p.type or '',
                # The same short text the template renders, computed in the one
                # place, so a card built here and one built by Django cannot
                # word-wrap or truncate differently.
                'blurb': p.blurb,
                'image': _project_image(p),
                'placeholder': bool(p.use_placeholder),
            } for p in page],
            'total': total,
            'next_offset': offset + len(page),
            'has_more': offset + len(page) < total,
        })

    first = list(projects[:PORTFOLIO_FIRST])
    total = projects.count()
    return render(request, 'content/portfolio.html', {
        'projects': first,
        # Only the types actually in use, and only ones with a value — a blank
        # entry in a filter list is a row nobody can mean to pick.
        'types': [t for t in Project.objects.values_list('type', flat=True)
                  .distinct().order_by('type') if t],
        'selected': {'type': request.GET.get('type', ''),
                     'q': (request.GET.get('q') or '').strip()},
        # (portfolio context — unrelated to the calendar)
        'total': total,
        'next_offset': len(first),
        'has_more': total > len(first),
        'more_page': PORTFOLIO_MORE,
    })


# ── PORTFOLIO MANAGEMENT ─────────────────────────────────────────────────────
# The twelve projects on the portfolio page could only be edited in Django admin,
# which needs a staff account that neither an Intranet Admin nor a content editor
# has. So the page showed twelve records nobody using the Hub could change.
#
# Gated on user_can_edit_cms rather than @content_editor_required: that decorator
# reads request.user.profile.role, and an account with no UserProfile row -- which
# includes the Intranet Admin -- silently counts as an employee and is refused.
# The same trap already had to be fixed on the DAM and on Policies.
def _portfolio_editor_required(view_func):
    from functools import wraps
    from cms.models import user_can_edit_cms

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not user_can_edit_cms(request.user):
            raise Http404
        return view_func(request, *args, **kwargs)
    return wrapper


#: How many edit forms to put on the manage screen at once. Every project there
#: is a full form — name, location, division, type, year, status, description,
#: a file input — and all 379 of them came to 1.5 MB of HTML before anybody had
#: typed anything. Search is how you reach the rest.
MANAGE_PAGE = 25


@_portfolio_editor_required
def portfolio_manage(request):
    """Add, edit and remove the projects shown on the portfolio page."""
    q = (request.GET.get('q') or '').strip()
    # Newest first, undated last, same as the portfolio itself — the two screens
    # showing the same projects in a different order is its own small confusion.
    everything = Project.objects.all().order_by(
        F('year').desc(nulls_last=True), 'title', 'pk')
    total = everything.count()

    if q:
        matches = everything.filter(_project_search(q))
        found = matches.count()
        projects = list(matches[:MANAGE_PAGE * 2])
    else:
        found = total
        projects = list(everything[:MANAGE_PAGE])

    ctx = {
        'projects': projects,
        'q': q,
        'total': total,
        'found': found,
        'showing': len(projects),
        'more_hidden': max(0, found - len(projects)),
        'statuses': Project.Status.choices,
        # Offered as datalists so the fields stay free text — a new division
        # should not need a code change — while still nudging towards the
        # spellings already in use.
        # Suggestions come from the whole table, not just the page on screen —
        # otherwise the datalist changes depending on what you searched for.
        'divisions': sorted({d for d in Project.objects.values_list('division', flat=True).distinct() if d}),
        'types': sorted({t for t in Project.objects.values_list('type', flat=True).distinct() if t}),
    }

    # Typing in the search box asks for the results on their own. Only the
    # results are replaced, so the box keeps focus and the caret stays where it
    # was — re-rendering the whole page would take the field out from under the
    # person still typing into it.
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render(request, 'content/_portfolio_manage_results.html', ctx)

    return render(request, 'content/portfolio_manage.html', ctx)


# ── Bulk edit by spreadsheet ───────────────────────────────────────────────
#
# Twelve projects is a form; a hundred is not. The template carries the existing
# rows and their ids so the same file both edits what is there and adds what is
# not, and nothing is written until somebody has seen a summary of what will
# change.

#: Column order in the template. `Ref` is the project's id — filled in for rows
#: that already exist, left blank for new ones.
BULK_COLUMNS = ['Ref', 'Name', 'Location', 'Division', 'Type', 'Year',
                'Status', 'Description']
BULK_REQUIRED = ('Name', 'Location')
#: A guard against somebody uploading the wrong spreadsheet entirely.
BULK_MAX_ROWS = 2000


@_portfolio_editor_required
def project_template(request):
    """The spreadsheet to fill in — pre-filled with every project there is."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        messages.error(request, 'openpyxl is not installed, so the template cannot be built.')
        return redirect('portfolio_manage')

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Projects'

    head_font = Font(bold=True, color='FFFFFF')
    head_fill = PatternFill('solid', fgColor='121210')
    need_fill = PatternFill('solid', fgColor='FCD700')
    need_font = Font(bold=True, color='121210')

    for i, name in enumerate(BULK_COLUMNS, start=1):
        cell = sheet.cell(row=1, column=i, value=name)
        # Name and Location are marked in the sheet itself, so the rule is
        # visible while somebody is typing rather than only when it is rejected.
        if name in BULK_REQUIRED:
            cell.value = f'{name} *'
            cell.font, cell.fill = need_font, need_fill
        else:
            cell.font, cell.fill = head_font, head_fill
        cell.alignment = Alignment(vertical='center')

    for r, p in enumerate(Project.objects.all().order_by('-year', 'title'), start=2):
        for c, value in enumerate([p.pk, p.title, p.location, p.division, p.type,
                                   p.year, p.status, p.description], start=1):
            sheet.cell(row=r, column=c, value=value)

    statuses = ','.join(v for v, _ in Project.Status.choices)
    rule = DataValidation(type='list', formula1=f'"{statuses}"', allow_blank=True)
    sheet.add_data_validation(rule)
    rule.add(f'G2:G{BULK_MAX_ROWS}')

    for col, width in zip('ABCDEFGH', (7, 38, 26, 14, 22, 8, 14, 60)):
        sheet.column_dimensions[col].width = width
    sheet.freeze_panes = 'A2'

    notes = book.create_sheet('How to use')
    for r, line in enumerate([
        'Filling this in',
        '',
        'Name and Location are required. A row missing either is reported and skipped;',
        'the rest of the file still goes in.',
        '',
        'Ref is the project this row updates. Leave it blank to add a new project —',
        'do not invent one. Deleting a row here does not delete the project; use the',
        'Delete button on the Projects page for that.',
        '',
        f'Status must be one of: {", ".join(v for v, _ in Project.Status.choices)}.',
        'Year must be between 1900 and 2100.',
        'Anything left blank on an existing project leaves that field as it is.',
        '',
        'Pictures cannot be set from here — add those on the Projects page.',
    ], start=1):
        notes.cell(row=r, column=1, value=line)
    notes.column_dimensions['A'].width = 84
    notes['A1'].font = Font(bold=True, size=13)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="AJi-projects-template.xlsx"'
    book.save(response)
    return response


def _clean_bulk_row(raw, row_no):
    """One spreadsheet row to (values, error). Never raises on bad input."""
    def text(key, limit):
        return str(raw.get(key) or '').strip()[:limit]

    name = text('Name', 300)
    location = text('Location', 300)
    if not name and not location and not any(raw.values()):
        return None, None                       # a blank row is not a mistake
    missing = [f for f, v in (('Name', name), ('Location', location)) if not v]
    if missing:
        return None, f'Row {row_no}: {" and ".join(missing)} is required — skipped.'

    ref = str(raw.get('Ref') or '').strip()
    ref = int(float(ref)) if ref.replace('.', '', 1).isdigit() else None

    year = None
    rawyear = str(raw.get('Year') or '').strip()
    if rawyear:
        try:
            candidate = int(float(rawyear))
            if 1900 <= candidate <= 2100:
                year = candidate
            else:
                return None, f'Row {row_no}: year "{rawyear}" is not between 1900 and 2100 — skipped.'
        except ValueError:
            return None, f'Row {row_no}: year "{rawyear}" is not a number — skipped.'

    status = text('Status', 40)
    if status and status not in dict(Project.Status.choices):
        return None, (f'Row {row_no}: status "{status}" is not one of '
                      f'{", ".join(v for v, _ in Project.Status.choices)} — skipped.')

    return {
        'ref': ref, 'name': name, 'location': location,
        'division': text('Division', 100), 'type': text('Type', 100),
        'year': year, 'status': status, 'description': str(raw.get('Description') or '').strip(),
    }, None


@_portfolio_editor_required
@require_POST
def project_bulk_upload(request):
    """Read the filled-in template and show what it would change."""
    try:
        import openpyxl
    except ImportError:
        messages.error(request, 'openpyxl is not installed, so the upload cannot be read.')
        return redirect('portfolio_manage')

    upload = request.FILES.get('workbook')
    if upload is None:
        messages.error(request, 'Choose a file first.')
        return redirect('portfolio_manage')
    if not upload.name.lower().endswith(('.xlsx', '.xlsm')):
        messages.error(request, 'That is not an Excel file. Upload the .xlsx template.')
        return redirect('portfolio_manage')

    try:
        book = openpyxl.load_workbook(upload, read_only=True, data_only=True)
    except Exception:                                              # noqa: BLE001
        messages.error(request, 'That file could not be read as a spreadsheet.')
        return redirect('portfolio_manage')

    sheet = book['Projects'] if 'Projects' in book.sheetnames else book.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = [str(h or '').replace('*', '').strip() for h in next(rows, ()) or ()]
    if 'Name' not in header:
        messages.error(
            request, 'That sheet has no "Name" column. Download the template and fill that in.')
        return redirect('portfolio_manage')

    known = {pk for pk in Project.objects.values_list('pk', flat=True)}
    plan, errors = [], []
    for n, values in enumerate(rows, start=2):
        if n - 1 > BULK_MAX_ROWS:
            errors.append(f'Stopped at {BULK_MAX_ROWS} rows — the rest of the file was ignored.')
            break
        raw = {header[i]: v for i, v in enumerate(values) if i < len(header)}
        row, error = _clean_bulk_row(raw, n)
        if error:
            errors.append(error)
        elif row:
            # A Ref pointing at a project that no longer exists would silently
            # become a new project, which is not what somebody editing a row
            # meant. It is reported instead.
            if row['ref'] is not None and row['ref'] not in known:
                errors.append(f'Row {n}: no project with Ref {row["ref"]} — skipped.')
                continue
            row['action'] = 'update' if row['ref'] else 'create'
            plan.append(row)

    if not plan and not errors:
        messages.error(request, 'That sheet has no rows in it.')
        return redirect('portfolio_manage')

    return render(request, 'content/portfolio_bulk_preview.html', {
        'plan': plan,
        'errors': errors,
        'creates': sum(1 for r in plan if r['action'] == 'create'),
        'updates': sum(1 for r in plan if r['action'] == 'update'),
        'payload': json.dumps(plan),
    })


@_portfolio_editor_required
@require_POST
def project_bulk_apply(request):
    """Write the previewed changes."""
    try:
        plan = json.loads(request.POST.get('payload') or '[]')
    except ValueError:
        messages.error(request, 'That upload could not be read again — please upload the file once more.')
        return redirect('portfolio_manage')
    if not isinstance(plan, list) or not plan:
        messages.error(request, 'Nothing to apply.')
        return redirect('portfolio_manage')

    created = updated = 0
    with transaction.atomic():
        for row in plan[:BULK_MAX_ROWS]:
            if not isinstance(row, dict):
                continue
            name = str(row.get('name') or '').strip()[:300]
            location = str(row.get('location') or '').strip()[:300]
            if not name or not location:
                continue                      # re-checked here, not just in the preview

            project = None
            if row.get('ref'):
                project = Project.objects.filter(pk=row['ref']).first()
            if project is None:
                project = Project()
                created += 1
            else:
                updated += 1

            project.title = name
            project.location = location
            # Blank means "leave alone" on an existing project, so that somebody
            # correcting one column does not wipe the others.
            for field, key in (('division', 'division'), ('type', 'type')):
                value = str(row.get(key) or '').strip()[:100]
                if value or not project.pk:
                    setattr(project, field, value)
            description = str(row.get('description') or '').strip()
            if description or not project.pk:
                project.description = description
            if row.get('status'):
                project.status = row['status']
            elif not project.pk:
                project.status = Project.Status.DESIGN
            # No year in the sheet means no year. It used to mean "today's",
            # which stamped 321 undated projects with the year they were
            # imported and made the portfolio look like it all happened at once.
            if row.get('year'):
                project.year = int(row['year'])
            elif not project.pk:
                project.year = None
            project.save()

    messages.success(
        request,
        f'{created} project{"" if created == 1 else "s"} added and '
        f'{updated} updated from the spreadsheet.')
    return redirect('portfolio_manage')


@_portfolio_editor_required
def project_save(request):
    """Create or update one project."""
    if request.method != 'POST':
        return redirect('portfolio_manage')

    title = request.POST.get('title', '').strip()[:300]
    location = request.POST.get('location', '').strip()[:300]
    # Both, not just the title: a project with no location cannot be placed on
    # the portfolio page, and half the entries were arriving without one.
    missing = [f for f, v in (('name', title), ('location', location)) if not v]
    if missing:
        messages.error(request, f'A project needs a {" and a ".join(missing)}.')
        return redirect('portfolio_manage')

    pk = request.POST.get('pk', '').strip()
    project = Project.objects.filter(pk=pk).first() if pk else None
    if project is None:
        project = Project()

    project.title = title
    project.location = location
    project.division = request.POST.get('division', '').strip()[:100]
    project.type = request.POST.get('type', '').strip()[:100]
    status = request.POST.get('status', '')
    # An unknown status would render as a blank pill, so it falls back rather
    # than being stored.
    project.status = status if status in dict(Project.Status.choices) else Project.Status.DESIGN
    project.description = request.POST.get('description', '').strip()
    # A four-digit year, or none at all. Clearing the box clears the year —
    # nothing invents one, and "no year recorded" is the honest state for most
    # of this portfolio.
    raw_year = (request.POST.get('year') or '').strip()
    if not raw_year:
        project.year = None
    else:
        try:
            year = int(raw_year)
            project.year = year if 1900 <= year <= 2100 else None
        except (TypeError, ValueError):
            project.year = None
    project.use_placeholder = bool(request.POST.get('use_placeholder'))
    if 'image' in request.FILES:
        project.image = request.FILES['image']
    project.save()
    messages.success(request, f'"{project.title}" {"updated" if pk else "added"}.')
    return redirect('portfolio_manage')


@_portfolio_editor_required
def project_delete(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        title = project.title
        # The image goes with the record rather than being orphaned on disk.
        if project.image:
            project.image.delete(save=False)
        project.delete()
        messages.success(request, f'"{title}" deleted.')
    return redirect('portfolio_manage')


# ── PEOPLE & CULTURE ──

@login_required
def people_culture(request):
    from voice.models import Survey
    from portal.models import WellnessProgram, ChildcarePartner, GrowthCertification
    current_tab = request.GET.get('tab', 'growth')
    # Only live updates reach employees; scheduling is honoured at read time.
    # Paginated rather than capped: the block used to show six at once and take
    # most of the page, and anything past the sixth was simply unreachable.
    # The model orders by ['-pinned', '-date'], which leaves same-day updates in
    # no defined order. That was harmless when the feed showed everything at once;
    # with pages it means an update can land on two pages or on none, because the
    # database is free to return ties differently each query. The primary key is
    # the tiebreaker: newest first, and always the same answer twice.
    # All of them, in one stable order. The block is a slider now, so it renders
    # every update once and moves a track: paging reloaded the page for four cards
    # and then jumped to an anchor, which felt like leaving the page entirely.
    #
    # The order needs its tiebreaker either way. The model orders by
    # ['-pinned', '-date'], which leaves same-day updates undefined, and the
    # database is free to return ties differently each query.
    announcements = [
        a for a in HRAnnouncement.objects
        .select_related('category')
        .order_by('-pinned', '-date', '-pk')
        if a.is_live
    ][:HR_MAX_SHOWN]
    surveys = Survey.objects.filter(active=True, closes_on__gte=date.today())

    # The records themselves. Flattening them into dicts here meant every new
    # field had to be added in two places, and the icon silently never arrived.
    wellness_items = list(WellnessProgram.objects.all())

    childcare_partners = list(ChildcarePartner.objects.values_list('name', flat=True))

    # The records themselves, not just their names: a chip needs its id to be
    # editable on the page, and a list of bare strings cannot carry one.
    certifications = list(GrowthCertification.objects.order_by('order', 'name'))

    hr_team = [
        {'name': 'Lina Sarie', 'role': 'Group HR Manager', 'email': 'Lina_Sarie@ajigroup.com', 'initials': 'LS'},
        {'name': 'Rawan Abbadi', 'role': 'Senior Group HR Supervisor', 'email': 'Rawan_Abbadi@ajigroup.com', 'initials': 'RA'},
        {'name': 'Tuleen Saleh', 'role': 'Junior HR Officer', 'email': 'Tuleen_Saleh@ajigroup.com', 'initials': 'TS'},
    ]

    # Records, not a list here. Their icons used to be chosen by an if/elif chain
    # in the template, so changing one meant editing code.
    from portal.models import ExploreTab
    # Filtered by group. Without it this page listed the Onboarding handbook's
    # tabs alongside its own -- eleven tabs down one side -- because both strips
    # live in the same table.
    tabs = list(ExploreTab.objects.filter(group=ExploreTab.Group.PC, is_active=True))
    # A tab whose key does not match a panel would open nothing, so an unknown
    # ?tab= falls back to the first one rather than showing a blank box.
    tab_keys = [t.key for t in tabs]
    if current_tab not in tab_keys:
        current_tab = tab_keys[0] if tab_keys else 'growth'

    is_hr = user_is_hr(request.user)

    from portal.models import EmployeeHandbookFile
    handbook = EmployeeHandbookFile.current()
    channels = WhatsAppChannel.objects.filter(is_active=True)

    return render(request, 'content/people_culture.html', {
        'handbook': handbook,
        'whatsapp_channels': channels,
        'announcements': announcements,
        'surveys': surveys,
        'tabs': tabs,
        'current_tab': current_tab,
        'wellness_items': wellness_items,
        'childcare_partners': childcare_partners,
        'certifications': certifications,
        'hr_team': hr_team,
        'is_hr': is_hr,
    })


# ── HR INFORMATION: detail + frontend CMS ─────────────────────────────────

@login_required
def hr_detail(request, slug):
    update = get_object_or_404(HRAnnouncement, slug=slug)
    # HR can preview drafts and scheduled items; everyone else only sees live.
    if not update.is_live and not user_is_hr(request.user):
        raise Http404

    if request.method == 'GET':
        HRAnnouncement.objects.filter(pk=update.pk).update(
            views=models.F('views') + 1)
        update.views += 1

    others = [a for a in HRAnnouncement.objects.select_related('category')
              .exclude(pk=update.pk) if a.is_live][:4]

    return render(request, 'content/hr_detail.html', {
        'update': update,
        'attachments': update.attachments.all(),
        'others': others,
    })


@login_required
@hr_required
def hr_manage(request):
    return render(request, 'content/hr_manage.html', {
        'updates': HRAnnouncement.objects.select_related('category').all(),
    })


@login_required
@hr_required
def hr_form(request, pk=None):
    update = get_object_or_404(HRAnnouncement, pk=pk) if pk else None
    errors = {}

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        body = request.POST.get('body', '').strip()
        if not title:
            errors['title'] = 'Give the update a title.'
        if not body:
            errors['body'] = 'Write the update body.'
        # A cover image is required — enforced on create, and on edit only
        # when there is not already one stored.
        if not _has_cover_input(request, update.image if update else None):
            errors['image'] = 'A cover image is required.'

        if not errors:
            if update is None:
                update = HRAnnouncement()
            update.title = title
            update.body = body
            update.excerpt = request.POST.get('excerpt', '').strip()[:400]
            update.author_name = (request.POST.get('author_name', '').strip()
                                  or request.user.get_full_name())
            cat_id = request.POST.get('category')
            update.category = HRCategory.objects.filter(pk=cat_id).first() if cat_id else None
            update.date = parse_date(request.POST.get('date', '') or '') or date.today()
            update.status = request.POST.get('status') or HRAnnouncement.Status.DRAFT
            update.pinned = request.POST.get('pinned') == 'on'
            update.publish_at = request.POST.get('publish_at') or None
            update.archive_at = request.POST.get('archive_at') or None
            _apply_cover(request, update)
            update.save()

            for upload in request.FILES.getlist('attachments'):
                HRAttachment.objects.create(announcement=update, file=upload)

            messages.success(request, f'Update saved as {update.get_status_display().lower()}.')
            return redirect('hr_edit', pk=update.pk)

    form = _hr_form_values(request, update)
    return render(request, 'content/hr_form.html', {
        'update': update,
        'form': form,
        'errors': errors,
        'categories': HRCategory.objects.all(),
        'statuses': HRAnnouncement.Status.choices,
        'attachments': update.attachments.all() if update else [],
        'cover_w': COVER_W,
        'cover_h': COVER_H,
    })


def _hr_form_values(request, update):
    """Keep what the author typed when validation sends the form back."""
    if request.method == 'POST':
        return {
            'title': request.POST.get('title', ''),
            'excerpt': request.POST.get('excerpt', ''),
            'body': request.POST.get('body', ''),
            'author_name': request.POST.get('author_name', ''),
            'category': request.POST.get('category', ''),
            'date': request.POST.get('date', ''),
            'status': request.POST.get('status', ''),
            'pinned': request.POST.get('pinned') == 'on',
            'publish_at': request.POST.get('publish_at', ''),
            'archive_at': request.POST.get('archive_at', ''),
        }
    if update:
        return {
            'title': update.title,
            'excerpt': update.excerpt,
            'body': update.body,
            'author_name': update.author_name,
            'category': str(update.category_id or ''),
            'date': update.date.strftime('%Y-%m-%d') if update.date else '',
            'status': update.status,
            'pinned': update.pinned,
            'publish_at': update.publish_at.strftime('%Y-%m-%dT%H:%M') if update.publish_at else '',
            'archive_at': update.archive_at.strftime('%Y-%m-%dT%H:%M') if update.archive_at else '',
        }
    return {
        'title': '', 'excerpt': '', 'body': '',
        'author_name': request.user.get_full_name(),
        'category': '', 'date': date.today().strftime('%Y-%m-%d'),
        'status': HRAnnouncement.Status.DRAFT, 'pinned': False,
        'publish_at': '', 'archive_at': '',
    }


@login_required
@hr_required
def hr_action(request, pk, action):
    update = get_object_or_404(HRAnnouncement, pk=pk)
    if request.method != 'POST':
        return redirect('hr_manage')

    if action == 'delete':
        update.delete()
        messages.success(request, 'Update deleted.')
        return redirect('hr_manage')
    if action == 'publish':
        update.status = HRAnnouncement.Status.PUBLISHED
        update.save()
        messages.success(request, 'Update published.')
    elif action == 'draft':
        update.status = HRAnnouncement.Status.DRAFT
        update.save()
        messages.success(request, 'Moved back to draft.')
    elif action == 'archive':
        update.status = HRAnnouncement.Status.ARCHIVED
        update.save()
        messages.success(request, 'Update archived.')
    elif action == 'pin':
        update.pinned = not update.pinned
        update.save()
        messages.success(request, 'Pinned.' if update.pinned else 'Unpinned.')
    elif action == 'attach_delete':
        att = update.attachments.filter(pk=request.POST.get('attachment_id')).first()
        if att:
            att.file.delete(save=False)
            att.delete()
            messages.success(request, 'Attachment removed.')
        return redirect('hr_edit', pk=update.pk)

    return redirect(request.POST.get('next') or 'hr_manage')


@login_required
@hr_required
def hr_post(request):
    if request.method == 'POST':
        HRAnnouncement.objects.create(
            tag=request.POST.get('tag', 'Update'),
            title=request.POST.get('title', ''),
            body=request.POST.get('body', ''),
            author_name=request.user.get_full_name(),
            date=date.today(),
            published=True,
        )
        return redirect('people_culture')
    return render(request, 'content/hr_post.html')


@login_required
@require_POST
def news_comment_edit(request, pk):
    """The author fixes their own comment. See core.comments for the rules."""
    comment = get_object_or_404(NewsComment.objects.select_related('article'), pk=pk)
    if apply_edit(request, comment, 'text'):
        who = request.user.get_full_name() or request.user.username
        notified = notify_mentions(
            comment.text, request.user,
            title=f'{who} mentioned you in a comment',
            link=reverse('news_detail', args=[comment.article.slug]),
            body=comment.text,
            case='news.mentioned',
            values={'actor': who, 'comment': comment.text})
        # Set rather than add: somebody edited out of the comment was mentioned
        # in a version that no longer exists.
        comment.mentions.set(notified)
        messages.success(request, 'Comment updated.')
    return redirect('news_detail', slug=comment.article.slug)


@login_required
@require_POST
def news_comment_delete(request, pk):
    comment = get_object_or_404(NewsComment.objects.select_related('article'), pk=pk)
    slug = comment.article.slug
    apply_delete(request, comment)
    messages.success(request, 'Comment deleted.')
    return redirect('news_detail', slug=slug)
