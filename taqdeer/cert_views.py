"""Certifications: HR records a credential, and the announcement writes itself.

Separate from the Athar award entirely. There is no cycle, no nomination and no
approval — somebody passed an exam, and the Hub says so in the same words every
time. The whole point is that HR types two fields and never designs anything.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import NotificationEvent as Event
from core.notify import notify

from .models import Certification, TaqdeerSettings
from core.models import user_is_hr

from .views import _hr_only


@login_required
def manage(request):
    """Every certification on record, and the form to add one."""
    _hr_only(request)
    from .forms import CertificationForm

    if request.method == 'POST':
        form = CertificationForm(request.POST, request.FILES)
        if form.is_valid():
            cert = form.save(commit=False)
            cert.added_by = request.user
            cert.save()
            messages.success(
                request, f'{cert.name} recorded for '
                         f'{cert.employee.get_full_name() or cert.employee.username}.')
            # The person it is about should hear it from the Hub, not from the
            # homepage by accident.
            if cert.published:
                notify(cert.employee, Event.GENERAL,
                       title=f'Your {cert.name} certification is on AJi Taqdeer',
                       body=cert.announcement(),
                       link='/taqdeer/', actor=request.user,
                       case='recognition.certification_recorded',
                       values={'name': (cert.employee.get_full_name()
                                        or cert.employee.username),
                               'certification': cert.name,
                               'announcement': cert.announcement(),
                               'actor': (request.user.get_full_name()
                                         or request.user.username)})
            return redirect('taqdeer_certifications')
        messages.error(request, 'That could not be saved — see the form.')
    else:
        form = CertificationForm(initial={'obtained_on': timezone.now().date()})

    rows = (Certification.objects
            .select_related('employee', 'employee__employee', 'added_by')
            .all())
    settings = TaqdeerSettings.get()
    return render(request, 'taqdeer/certifications.html', {
        'rows': rows, 'form': form, 'settings': settings,
        'n_published': sum(1 for r in rows if r.published),
        'is_hr': True,
    })


@login_required
@require_POST
def update(request, pk):
    """Publish, unpublish, or remove one."""
    _hr_only(request)
    cert = get_object_or_404(Certification, pk=pk)
    action = request.POST.get('action')

    if action == 'toggle':
        cert.published = not cert.published
        cert.save(update_fields=['published'])
        messages.success(
            request,
            f'{cert.name} is {"published" if cert.published else "hidden"}.')
    elif action == 'delete':
        who, what = cert.employee, cert.name
        cert.delete()
        messages.success(
            request, f'{what} removed for {who.get_full_name() or who.username}.')

    return redirect('taqdeer_certifications')


@login_required
def image(request, pk):
    """The announcement as a picture, drawn fresh from the current record.

    Not stored: the card is a function of the name, the certification and the
    date, so regenerating costs nothing and a correction to any of the three is
    reflected the next time somebody opens it.
    """
    cert = get_object_or_404(
        Certification.objects.select_related('employee'), pk=pk)
    if not cert.published and not user_is_hr(request.user):
        raise Http404

    # Served from the stored card, redrawn if it is stale or the file has gone.
    # Drawing one costs about a second, so a feed of them was a second each,
    # every load.
    #
    # Wrapped because this URL is an <img> in the recognition feed: a card that
    # cannot be drawn should be a missing picture on the page, not a 500 with a
    # stack trace where a photograph belongs. The reason still reaches the log.
    try:
        field = cert.card_file()
    except Exception as exc:                                     # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception(
            '[taqdeer] could not draw the card for certification %s: %s',
            cert.pk, exc)
        raise Http404('The card could not be drawn.')

    who = ''.join(ch for ch in (cert.employee.get_full_name()
                                or cert.employee.username)
                  if ch.isalnum() or ch in ' -_').strip()
    safe = ''.join(ch for ch in cert.name if ch.isalnum() or ch in ' -_').strip()
    name = f'{who} - {safe}.png'.replace('  ', ' ')
    disposition = 'inline' if request.GET.get('view') else 'attachment'
    response = FileResponse(field.open('rb'), content_type='image/png')
    response['Content-Disposition'] = f'{disposition}; filename="{name}"'
    # The URL carries no version, so let the browser keep it only briefly; the
    # file itself is replaced in place when the record changes.
    response['Cache-Control'] = 'private, max-age=300'
    return response
