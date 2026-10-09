"""Releasing a quarter: preview, confirm, and publish.

The last step of the Athar cycle and the only irreversible one, so it is its own
module and its own page. Everything before release is HR talking to itself;
release sends real email to real people and puts names on a page the whole
company reads. It gets a confirmation screen that says exactly how many, and it
refuses to run twice.
"""
import logging
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import NotificationEvent as Event
from core.models import user_is_hr
from core.email_cases import ATTACH_CERTIFICATE, resolve
from core.notify import absolute, notify, render_email

from .models import AwardCycle, Nomination, TaqdeerSettings
from .views import _hr_only

logger = logging.getLogger(__name__)


def build_certificate(nomination, settings=None):
    """Render and attach the PDF. Safe to call again — it overwrites."""
    from . import certificate as cert
    pdf = cert.from_settings(nomination, settings)
    safe = ''.join(ch for ch in nomination.recipient_name
                   if ch.isalnum() or ch in ' -_').strip()
    name = f'Athar-{nomination.cycle.label.replace(" ", "-")}-{safe or nomination.pk}.pdf'
    nomination.certificate.save(name, ContentFile(pdf), save=True)
    return nomination.certificate


def _certificate_attachment(nomination):
    """The winner's certificate, as (filename, bytes, mimetype) for the mailer.

    Read back off the file the release has just written rather than rendered
    again. A second render would be a second document: same wording today, but
    the copy in somebody's inbox and the copy they can open on the Hub would be
    separate artefacts, and only one of them would be reissued if HR corrected
    the spelling of a name.

    Returns None rather than raising if the file is missing. A certificate that
    cannot be attached is worth an email without an attachment; it is not worth
    losing the announcement over.
    """
    field = nomination.certificate
    if not field:
        return None
    try:
        field.open('rb')
        try:
            data = field.read()
        finally:
            field.close()
    except Exception as exc:                                # noqa: BLE001
        logger.error('[taqdeer] could not attach the certificate for %s: %s',
                     nomination.pk, exc)
        return None
    if not data:
        return None
    return [(os.path.basename(field.name), data, 'application/pdf')]


@login_required
def preview(request, pk):
    """The certificate as it will look, in the browser, before anything is sent.

    Rendered fresh rather than served from disk: the point of a preview is to
    show what the current wording and signatory produce, not what an earlier
    save happened to produce.
    """
    n = get_object_or_404(Nomination.objects.select_related('cycle', 'recipient'), pk=pk)
    allowed = (user_is_hr(request.user) or request.user.is_superuser
               or request.user.pk == n.recipient_id)
    if not allowed:
        raise Http404

    from . import certificate as cert
    pdf = cert.from_settings(n)
    response = HttpResponse(pdf, content_type='application/pdf')
    # inline, not attachment: a preview that lands in the downloads folder is
    # not a preview.
    response['Content-Disposition'] = (
        f'inline; filename="preview-{n.cycle.label.replace(" ", "-")}.pdf"')
    return response


@login_required
def release(request, pk):
    """The confirmation screen, and the release itself.

    GET states the consequence in numbers. POST performs it once — a cycle
    already released is refused rather than re-emailed, because the failure
    mode of a double release is two hundred duplicate emails.
    """
    _hr_only(request)
    cycle = get_object_or_404(AwardCycle, pk=pk)
    s = TaqdeerSettings.get()
    approved = list(cycle.nominations
                    .filter(status=Nomination.Status.APPROVED)
                    .select_related('recipient', 'recipient__employee', 'nominated_by'))

    if request.method == 'POST':
        if cycle.stage == AwardCycle.Stage.ISSUED:
            messages.error(request, f'{cycle.label} has already been released.')
            return redirect('taqdeer_athar_period', pk=cycle.pk)
        if cycle.stage != AwardCycle.Stage.APPROVED:
            messages.error(
                request, 'A quarter has to be marked ready to release first.')
            return redirect('taqdeer_athar_period', pk=cycle.pk)
        if not approved:
            messages.error(request, 'Nothing is approved, so there is nothing to release.')
            return redirect('taqdeer_athar_period', pk=cycle.pk)

        sent, failed, without = _do_release(request, cycle, approved, s)

        cycle.stage = AwardCycle.Stage.ISSUED
        cycle.issued_at = timezone.now()
        cycle.released_by = request.user
        cycle.save(update_fields=['stage', 'issued_at', 'released_by'])

        messages.success(
            request,
            f'{cycle.label} released. {len(approved)} '
            f'{"recipient" if len(approved) == 1 else "recipients"}, '
            f'{sent} email{"" if sent == 1 else "s"} sent.')
        if without:
            messages.warning(
                request,
                f'{len(without)} {"person has" if len(without) == 1 else "people have"} '
                f'no email address on record, so they were notified in the Hub only: '
                f'{", ".join(without[:5])}{"…" if len(without) > 5 else ""}')
        return redirect('taqdeer_home')

    return render(request, 'taqdeer/release.html', {
        'cycle': cycle, 'approved': approved, 'settings': s,
        'ready': cycle.stage == AwardCycle.Stage.APPROVED,
        'already': cycle.stage == AwardCycle.Stage.ISSUED,
        'no_email': [n for n in approved if not (n.recipient.email or '').strip()],
        'is_hr': True,
    })


def _do_release(request, cycle, approved, s):
    """Generate, deliver, and record. Returns (sent, failed, names without email)."""
    from core.mailer import send

    sent = failed = 0
    without = []
    now = timezone.now()

    for n in approved:
        # Generated at release rather than reused, so the wording HR settled on
        # is the wording that goes out.
        try:
            build_certificate(n, s)
        except Exception:                                   # noqa: BLE001
            failed += 1
            continue

        n.released_at = now
        n.save(update_fields=['released_at'])

        link = absolute(reverse('taqdeer_certificate', args=[n.pk]))
        # Was `if s.notify_approved or True`, which is true whatever the setting
        # says — the switch had no effect and reading the line suggested it did.
        # This message belongs to the release, not to the approval a week
        # earlier, so it answers to the release switch: turning that off now
        # silences the whole release rather than only its email half.
        if s.notify_release:
            notify(n.recipient, Event.GENERAL,
                   title=f'You have received {s.certificate_title}',
                   body=s.congratulations_message,
                   link=reverse('taqdeer_home'), actor=request.user,
                   case='athar.released_notice',
                   values={'name': n.recipient_name,
                           'award_title': s.certificate_title,
                           'congratulations': s.congratulations_message,
                           'period': cycle.label})

        address = (n.recipient.email or '').strip()
        if not address:
            without.append(n.recipient_name)
            continue
        if not s.notify_release:
            continue

        # The wording, the importance flag and whether the certificate rides
        # along are all the administrator's to set, in the IT portal under
        # Emails. Nothing here decides them.
        shape = resolve('athar.released', {
            'name': n.recipient_name,
            'award_title': s.certificate_title,
            'period': cycle.label,
            'congratulations': s.congratulations_message,
            'recognition_line': s.certificate_recognition_line,
            'citation': n.citation,
        })
        if shape is None or not shape.enabled:
            continue

        files = None
        if shape.attachment == ATTACH_CERTIFICATE:
            files = _certificate_attachment(n)

        html = render_email(
            title=shape.subject, lines=shape.body_lines,
            link=link, link_label=shape.link_label, footer=shape.footer)
        ok, _err = send(address, shape.subject, html, attachments=files,
                        high_importance=shape.high_importance)
        if ok:
            n.emailed_at = now
            n.save(update_fields=['emailed_at'])
            sent += 1
        else:
            failed += 1

    return sent, failed, without


def _as_png(pdf_bytes, dpi=200):
    """The certificate as a picture.

    pypdfium2, not PyMuPDF: the project pins it deliberately — see the note in
    requirements.txt and dam/thumbnails.py — because it ships a self-contained
    wheel and a permissive licence. Same renderer the document library already
    uses for its previews, so this adds no dependency at all.
    """
    import io

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    try:
        image = doc[0].render(scale=dpi / 72).to_pil().convert('RGB')
    finally:
        doc.close()
    out = io.BytesIO()
    image.save(out, 'PNG', optimize=True)
    return out.getvalue()


@login_required
def certificate_image(request, pk):
    """The certificate as a PNG, for putting in a slide or a message.

    A picture rather than a PDF because that is what people actually do with
    these — paste them into a deck, a chat, a printed board. Same artwork; the
    PDF stays the thing you print.
    """
    n = get_object_or_404(Nomination.objects.select_related('cycle', 'recipient'), pk=pk)
    allowed = (user_is_hr(request.user) or request.user.is_superuser
               or request.user.pk in (n.recipient_id, n.nominated_by_id))
    if not allowed:
        raise Http404

    from . import certificate as cert
    png = _as_png(cert.from_settings(n))
    safe = ''.join(ch for ch in n.recipient_name if ch.isalnum() or ch in ' -_').strip()
    name = f'Athar-{n.cycle.label.replace(" ", "-")}-{safe or n.pk}.png'
    disposition = 'inline' if request.GET.get('view') else 'attachment'
    response = HttpResponse(png, content_type='image/png')
    response['Content-Disposition'] = f'{disposition}; filename="{name}"'
    return response


@login_required
def certificate(request, pk):
    """The recipient's own certificate.

    The specification is explicit that the public results feed does not carry
    the certificate and that one employee cannot reach another's. So: the
    recipient, the manager who nominated them, and HR. Nobody else, and a 404
    rather than a 403 so the existence of it is not confirmed either.
    """
    n = get_object_or_404(Nomination, pk=pk)
    allowed = (user_is_hr(request.user) or request.user.is_superuser
               or request.user.pk in (n.recipient_id, n.nominated_by_id))
    if not allowed:
        raise Http404
    if not n.certificate:
        raise Http404('No certificate has been generated yet.')
    return FileResponse(n.certificate.open('rb'), as_attachment=True,
                        filename=n.certificate.name.rsplit('/', 1)[-1])
