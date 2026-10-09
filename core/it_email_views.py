# core/it_email_views.py
"""The Emails tab in the IT portal.

The whole point of this tab is that changing how the Hub speaks to staff should
not need a developer and a deploy. Every email the system can send is listed,
grouped by the module it belongs to, and everything about one is editable here:
whether it is sent, what its subject says, how it reads, whether it goes out
flagged high importance, and whether a file rides along with it.

The cases themselves come from `core.email_cases` and are code, because a case
only exists if something calls it. What an administrator changes is an
`EmailTemplate` row, created the first time they touch a case, so an untouched
Hub has none of them and every email goes out with the standard wording.

Gated on `user_can_manage_settings` through `it_required`, and every change
writes an ITAuditLog row, exactly like the rest of the portal.
"""
import json

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .email_cases import (ATTACHMENTS, MODULES, by_module, case as get_case,
                          current_text, examples_for, module_counts, resolve)
from .it_views import _tell, it_required
from .models import EmailTemplate, ITAuditLog


def _rows(module='', q=''):
    """The list, with each case's stored overrides attached to it.

    One query for the templates rather than one per case: the unfiltered list is
    thirty-eight rows and would otherwise be thirty-eight round trips to show a
    page whose whole content is switches.
    """
    cases = by_module(module, q)
    stored = {t.code: t for t in EmailTemplate.objects.filter(
        code__in=[c.code for c in cases])}
    out = []
    for c in cases:
        row = stored.get(c.code)
        out.append({
            'case': c,
            'template': row,
            'enabled': row.enabled if row else c.enabled_by_default,
            'customised': bool(row and row.is_customised),
            'high_importance': row.high_importance if row else c.high_importance,
            'attachment': row.attachment if row else c.attach_by_default,
        })
    return out


@it_required
def it_emails(request):
    """Every email the Hub sends, grouped by module and filterable by it."""
    module = request.GET.get('module', '')
    q = (request.GET.get('q') or '').strip()
    rows = _rows(module, q)

    counts = module_counts()
    # Still grouped when a filter is on, so the headings tell the reader which
    # module they are looking at rather than leaving a bare list of switches.
    groups = []
    for value, label in MODULES:
        in_group = [r for r in rows if r['case'].module == value]
        if in_group:
            groups.append({'value': value, 'label': label, 'rows': in_group})

    return render(request, 'core/it/emails.html', {
        'tab': 'emails',
        'groups': groups,
        'modules': [(v, l, counts.get(v, 0)) for v, l in MODULES],
        'module': module,
        'q': q,
        'shown': len(rows),
        'total': len(by_module()),
        'off_count': sum(1 for r in rows if not r['enabled']),
        'edited_count': sum(1 for r in rows if r['customised']),
    })


@it_required
def it_email_detail(request, code):
    """One case: what it is, when it fires, and everything editable about it."""
    found = get_case(code)
    if found is None:
        raise Http404
    row = EmailTemplate.objects.filter(code=code).first()

    # The boxes carry the wording actually in force, not an empty field with the
    # standard hidden behind a placeholder attribute. Changing one word should be
    # changing one word, not retyping the sentence to get at it.
    subject_value, body_value = current_text(code, template=row)

    # The preview fills the braces with real-looking values, because
    # "{award_title}: {period}" tells you nothing about whether a subject line
    # reads well and "The Athar Award: Q1 2026" tells you at a glance.
    examples = examples_for(found)
    shape = resolve(code, examples, template=row)

    return render(request, 'core/it/email_detail.html', {
        'tab': 'emails',
        'case': found,
        'template': row,
        'shape': shape,
        'examples': examples,
        'examples_json': json.dumps(examples),
        'attachment_choices': [(k, ATTACHMENTS[k]) for k in found.attachments],
        'enabled': row.enabled if row else found.enabled_by_default,
        'subject_value': subject_value,
        'body_value': body_value,
        'is_standard': not (row and row.is_customised),
        'high_importance': row.high_importance if row else found.high_importance,
        'attachment': row.attachment if row else found.attach_by_default,
        'my_email': (request.user.email or '').strip(),
    })


def _unless_standard(found, subject, body):
    """Blank out anything that matches the standard, so it stays linked to it.

    Compared after the dash rule has been through both sides. Otherwise a
    standard line and the same line typed back with a dash in it would compare as
    different, and a case would look reworded when nothing had changed about it.
    """
    from .email_cases import no_dashes

    def norm(text):
        lines = [no_dashes(line).strip() for line in (text or '').splitlines()]
        return '\n'.join(line for line in lines if line).strip()

    def same(typed, standard):
        return norm(typed) == norm(standard)

    return ('' if same(subject, found.subject) else subject,
            '' if same(body, found.body) else body)


def _row_for(code, user):
    row, _ = EmailTemplate.objects.get_or_create(code=code)
    row.updated_by = user
    return row


@it_required
@require_POST
def it_email_save(request, code):
    found = get_case(code)
    if found is None:
        raise Http404

    row = _row_for(code, request.user)
    # Text identical to the standard is stored as blank rather than as a copy of
    # it. That is what keeps a case following its default: somebody who opened
    # the editor, changed the importance flag and saved has not thereby frozen
    # today's wording into the database, and a later improvement to the standard
    # still reaches them. Clearing a box does the same thing.
    subject, body = _unless_standard(
        found,
        (request.POST.get('subject') or '').strip(),
        (request.POST.get('body') or '').strip())
    row.subject = subject
    row.body = body
    row.high_importance = bool(request.POST.get('high_importance'))
    # Only an automation this case can actually produce. A value the case does
    # not offer is dropped rather than stored, so a hand-edited form cannot leave
    # a switch set that nothing will ever act on.
    wanted = (request.POST.get('attachment') or '').strip()
    row.attachment = wanted if wanted in found.attachments else ''
    row.enabled = bool(request.POST.get('enabled'))
    row.save()

    ITAuditLog.write(request, ITAuditLog.Action.SETTING,
                     f'Email changed: {found.label}', target=code)
    _tell(request, f'Saved. {found.label} will use this from now on.')
    return redirect('it_email_detail', code=code)


@it_required
@require_POST
def it_email_toggle(request, code):
    """The switch on the list.

    It turns the *email* off, not the notification. Deliberately: somebody
    switching off "email them about a returned nomination" is saying the inbox is
    the wrong place for it, not that the manager should never find out. The bell
    still rings.
    """
    found = get_case(code)
    if found is None:
        raise Http404

    row = _row_for(code, request.user)
    was = row.enabled if row.pk else found.enabled_by_default
    row.enabled = not was
    row.save()

    ITAuditLog.write(
        request, ITAuditLog.Action.SETTING,
        f'Email {"switched on" if row.enabled else "switched off"}: {found.label}',
        target=code)
    _tell(request, f'{found.label}: email is now '
                   f'{"on" if row.enabled else "off"}.')
    return redirect(request.POST.get('back') or reverse('it_emails'))


@it_required
@require_POST
def it_email_reset(request, code):
    """Put the standard wording back by clearing the override rather than by
    copying the default into the row. A case whose wording is stored as a copy
    stops benefiting from any later improvement to the default."""
    found = get_case(code)
    if found is None:
        raise Http404
    EmailTemplate.objects.filter(code=code).update(subject='', body='')
    ITAuditLog.write(request, ITAuditLog.Action.SETTING,
                     f'Email wording reset to standard: {found.label}',
                     target=code)
    _tell(request, 'Back to the standard wording.')
    return redirect('it_email_detail', code=code)


@it_required
@require_POST
def it_email_test(request, code):
    """Send this case to whoever asked for it, with the unsaved edit applied.

    Unsaved on purpose. A preview in a browser cannot tell you what Outlook does
    with a subject line, whether the importance flag actually shows, or whether
    the attachment arrives, and finding that out should not require committing
    the change first.
    """
    from .mailer import test_send

    found = get_case(code)
    if found is None:
        raise Http404
    address = (request.POST.get('to') or request.user.email or '').strip()
    if not address:
        _tell(request, 'No address to send to. Add one to your account first.',
              'error')
        return redirect('it_email_detail', code=code)

    # Built and never saved, so the test shows what is on screen rather than what
    # is in the database.
    draft = EmailTemplate(
        code=code,
        enabled=True,
        subject=(request.POST.get('subject') or '').strip(),
        body=(request.POST.get('body') or '').strip(),
        high_importance=bool(request.POST.get('high_importance')),
    )
    sample = {p: p.replace('_', ' ') for p in found.placeholders}
    sample['name'] = request.user.get_full_name() or request.user.username
    ok, error = test_send(address, code=code, values=sample, template=draft)
    if ok:
        _tell(request, f'Sent to {address}. Check how it reads in your inbox.')
    else:
        _tell(request, f'Could not send: {error}', 'error')
    return redirect('it_email_detail', code=code)
