# assist/views.py
import json
import logging
import requests
from datetime import date

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse, JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

from .models import AssistMessage, AssistSession
from people.models import Employee
from content.models import NewsArticle, CalendarEvent, Project
from portal.models import Policy, OnboardingSection
from exchange.models import Community


# ── Intent detection ──────────────────────────────────────────────────────────

INTENT_KEYWORDS = {
    'employee': ['who is', 'find', 'contact', 'email', 'extension', 'manager', 'reports to', 'phone'],
    'leave': ['leave', 'vacation', 'sick', 'annual', 'holiday', 'time off', 'absent'],
    'salary': ['salary', 'compensation', 'insurance', 'medical', 'pay', 'deduction', 'benefit'],
    'project': ['project', 'portfolio', 'division', 'status', 'site', 'construction'],
    'policy': ['policy', 'sop', 'procedure', 'rule', 'guideline'],
    'news': ['news', 'announcement', 'latest', 'update', 'what happened'],
    'community': ['community', 'exchange', 'discussion', 'forum', 'thread'],
    'calendar': ['calendar', 'event', 'schedule', 'upcoming', 'when is'],
    'skills': ['skill', 'expertise', 'revit', 'autocad', 'bim', 'structural', 'architecture'],
    'extension': ['extension', 'ext ', 'phone number', 'directory'],
    'suggestion': ['suggestion', 'idea', 'feedback', 'anonymous'],
}


def detect_intents(q):
    q_lower = q.lower()
    return [intent for intent, kws in INTENT_KEYWORDS.items() if any(kw in q_lower for kw in kws)]


# ── Context builder ───────────────────────────────────────────────────────────

def build_context(user, question):
    today = date.today()
    intents = detect_intents(question)
    ctx_parts = []

    # Layer 1: Always included
    try:
        emp = user.employee
        ctx_parts.append(f"CURRENT USER: {emp.full_name} | {emp.role or 'Staff'} | {emp.entity.name if emp.entity else 'AJi Group'}")
        if emp.manager:
            ctx_parts.append(f"MANAGER: {emp.manager.full_name}")
    except Exception:
        ctx_parts.append(f"CURRENT USER: {user.get_full_name()} | AJi Group")

    ctx_parts.append(f"TODAY: {today.strftime('%A, %d %B %Y')}")
    ctx_parts.append("COMPANY: AJi Group — leading MENA engineering & architecture consultancy, 60+ years, 1000+ professionals, offices in Jordan, Saudi Arabia, UAE, Oman.")

    # Layer 2: Upcoming events (always)
    events = CalendarEvent.objects.filter(date__gte=today).order_by('date')[:5]
    if events:
        ctx_parts.append("UPCOMING EVENTS: " + " | ".join(
            f"{e.title} ({e.date.strftime('%d %b')}{'@'+e.time.strftime('%H:%M') if e.time else ''})"
            for e in events
        ))

    # Layer 3: Intent-triggered context
    if 'employee' in intents:
        q_lower = question.lower()
        employees = Employee.objects.filter(is_active=True).select_related('entity', 'manager')
        matched = [e for e in employees if
                   any(word in q_lower for word in e.full_name.lower().split() if len(word) > 2)]
        if matched:
            for e in matched[:3]:
                ctx_parts.append(
                    f"EMPLOYEE: {e.full_name} | {e.role or '—'} | {e.entity.name if e.entity else '—'} | "
                    f"Manager: {e.manager.full_name if e.manager else '—'} | "
                    f"Email: {e.email or e.email_auto()} | Ext: {e.extension or '—'}"
                )

    if 'leave' in intents:
        sections = OnboardingSection.objects.filter(tab_key='leave').prefetch_related('items').first()
        if sections:
            items = " | ".join(f"(paragraph {i.order}) {i.label}: {i.content[:100]}" for i in sections.items.all()[:8])
            ctx_parts.append(f"LEAVE POLICY: {items}")

    if 'salary' in intents:
        sections = OnboardingSection.objects.filter(tab_key='comp').prefetch_related('items').first()
        if sections:
            items = " | ".join(f"(paragraph {i.order}) {i.label}: {i.content[:100]}" for i in sections.items.all()[:8])
            ctx_parts.append(f"COMPENSATION: {items}")

    if 'project' in intents:
        q_lower = question.lower()
        projects = Project.objects.all()
        matched_p = [p for p in projects if any(w in q_lower for w in p.title.lower().split() if len(w) > 3)]
        if matched_p:
            for p in matched_p[:3]:
                ctx_parts.append(f"PROJECT: {p.title} | {p.location} | {p.division} | {p.status} | {p.year}")
        else:
            recent = Project.objects.order_by('-year')[:5]
            ctx_parts.append("RECENT PROJECTS: " + " | ".join(f"{p.title} ({p.status})" for p in recent))

    if 'policy' in intents:
        policies = Policy.objects.filter(title__icontains=question.split()[0] if question.split() else '')[:5]
        if not policies.exists():
            policies = Policy.objects.all()[:8]
        ctx_parts.append("POLICIES: " + " | ".join(p.title for p in policies))

    if 'news' in intents:
        news = NewsArticle.objects.filter(published=True).order_by('-date')[:3]
        for n in news:
            ctx_parts.append(f"NEWS: {n.title} ({n.date.strftime('%d %b')}) — {n.body[:150]}")

    if 'community' in intents:
        communities = Community.objects.all()[:6]
        ctx_parts.append("COMMUNITIES: " + " | ".join(f"{c.name} ({c.member_count()} members)" for c in communities))

    if 'extension' in intents:
        from people.models import PhoneExtension
        exts = PhoneExtension.objects.all()[:20]
        q_lower = question.lower()
        matched_e = [e for e in exts if e.name.lower() in q_lower or any(
            w in e.name.lower() for w in q_lower.split() if len(w) > 2
        )]
        if matched_e:
            ctx_parts.append("EXTENSIONS: " + " | ".join(f"{e.name}: ext {e.extension}" for e in matched_e[:5]))

    return "\n".join(ctx_parts), intents


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_TEMPLATE = """You are AJiAssist, the internal AI assistant for AJi Group — a leading MENA engineering and architecture consultancy with over 60 years of legacy across Jordan, Saudi Arabia, UAE and Oman.

You help employees with HR policies, finding colleagues, understanding company structure, locating documents, answering questions about projects, and navigating the AJi Hub intranet.

Guidelines:
- Be concise, professional and helpful
- If you don't know something from the context, say so and direct the user to the right person or section
- Never invent employee data, policy details, or project information
- Respond in the same language as the user's question (Arabic or English)
- Keep answers focused and practical
- If the context includes a page number and/or paragraph number for the information you're citing, mention them right after the relevant sentence, e.g. "(page 4, paragraph 2)" — or "(صفحة 4، فقرة 2)" when answering in Arabic. Only include them when they are actually present in the context; never invent a page or paragraph number.

{context_block}"""


# ── Views ─────────────────────────────────────────────────────────────────────

def _format_source(s):
    """Normalize a source entry into a display string. AJi-prop may return plain
    strings, or richer objects carrying page/paragraph location metadata —
    render 'p. X, ¶ Y' after the title when that data is present."""
    if isinstance(s, str):
        return s
    if isinstance(s, dict):
        label = s.get('title') or s.get('document') or s.get('name') or s.get('source') or 'Source'
        page = s.get('page') or s.get('page_number') or s.get('page_no')
        para = s.get('paragraph') or s.get('paragraph_number') or s.get('para')
        loc_bits = []
        if page:
            loc_bits.append(f'p. {page}')
        if para:
            loc_bits.append(f'¶ {para}')
        if loc_bits:
            return f"{label} ({', '.join(loc_bits)})"
        return label
    return str(s)

def _get_or_create_session(user):
    """Get the active session for this user, or create one."""
    session = AssistSession.objects.filter(user=user, is_active=True).first()
    if not session:
        session = AssistSession.objects.create(user=user, is_active=True)
    return session


@login_required
def assist_new(request):
    """Close the conversation in progress and begin a fresh one.

    "New conversation" used to be a link back to /assist/, which reloads the
    session already in progress — so it never looked like anything happened and
    the welcome screen, with its suggestions, could not be reached a second
    time. Ending the active session makes _get_or_create_session open a new one.

    An untouched session is left alone: pressing the button twice should not
    leave a trail of empty conversations down the side.
    """
    session = (AssistSession.objects
               .filter(user=request.user, is_active=True).first())
    if session and session.messages.exists():
        session.is_active = False
        session.ended_at = timezone.now()
        session.save(update_fields=['is_active', 'ended_at'])
    return redirect('assist_widget')


@login_required
def assist_widget(request):
    """AJiAssist with room to work: capabilities and history beside the thread.

    Same assistant as the panel in the corner — same endpoints, same sessions,
    the same rendering — with the space to read a long answer, a table, or a
    conversation from last week.
    """
    session = _get_or_create_session(request.user)

    # An earlier conversation can be opened in place, so a reader is not sent
    # back to the corner panel to find one.
    wanted = request.GET.get('s')
    if wanted:
        chosen = (AssistSession.objects
                  .filter(pk=wanted, user=request.user).first())
        if chosen:
            session = chosen

    sessions = [s for s in AssistSession.objects
                .filter(user=request.user)
                .prefetch_related('messages')
                .order_by('-started_at')[:30]
                if s.messages.exists()]

    # The conversation on screen belongs in the list even before anything has
    # been asked in it. Listing only sessions with messages meant starting a new
    # one looked like it had done nothing: the thread cleared but the sidebar
    # was unchanged, with the previous conversation still the one highlighted.
    if session.pk and not any(s.pk == session.pk for s in sessions):
        sessions.insert(0, session)

    return render(request, 'assist/widget.html', {
        'session': session,
        # Deliberately NOT called "messages": that name would shadow Django's
        # messages framework and the site-wide toasts would try to render
        # AssistMessage rows.
        'assist_msgs': session.messages.order_by('created_at'),
        'sessions': sessions,
        'viewing_past': not session.is_active,
    })


@login_required
def ask(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    question = request.POST.get('question', '').strip()
    if not question:
        return JsonResponse({'error': 'No question provided'}, status=400)

    # Answer in the language of the question: Arabic question → Arabic answer,
    # English question → English answer.
    arabic_chars = len([ch for ch in question if '\u0600' <= ch <= '\u06FF'])
    latin_chars = len([ch for ch in question if ch.isascii() and ch.isalpha()])
    is_arabic = arabic_chars > 0 and arabic_chars >= latin_chars
    lang_instruction = ('\n\nأجب باللغة العربية فقط.' if is_arabic
                        else '\n\nAnswer in English only.')

    context_text, intents = build_context(request.user, question)
    system_prompt = SYSTEM_TEMPLATE.format(
        context_block=f"--- COMPANY CONTEXT ---\n{context_text}\n--- END CONTEXT ---"
    )

    # Save the question first (answer filled in after streaming)
    session = _get_or_create_session(request.user)
    msg = AssistMessage.objects.create(
        session  = session,
        user=request.user,
        question=question,
        context_snapshot={'intents': intents, 'context_length': len(context_text)},
    )

    def stream_response():
        full_answer = []
        sources     = []

        try:
            ajiprop_base = getattr(settings, 'AJIPROP_API_BASE', '').strip()
            use_rag      = bool(ajiprop_base)

            if use_rag:
                # ── Route to AJi-prop RAG — non-streaming to avoid Waitress buffering ──
                # Waitress buffers StreamingHttpResponse fully before sending to client,
                # so we call AJi-prop with stream=False and get the complete answer at once.
                session = requests.Session()
                session.trust_env = not getattr(settings, 'AJIPROP_TRUST_PROXY', False)

                resp = session.post(
                    f"{ajiprop_base}/api/hub-assist/",
                    json={
                        # send the question EXACTLY as typed — appending any
                        # instruction text changes the embedding and breaks
                        # the KB retrieval ("not found" answers)
                        'question': question,
                        'language': 'ar' if is_arabic else 'en',
                        'top_k':    getattr(settings, 'AJIPROP_TOP_K', 5),
                        'stream':   False,
                        'include_citations': True,  # ask for page/paragraph metadata when available
                    },
                    timeout=getattr(settings, 'AJIPROP_TIMEOUT', 90),
                )
                resp.raise_for_status()
                data = resp.json()

                answer  = data.get('answer', '').strip()
                sources = [_format_source(s) for s in data.get('sources', [])]
                logger.info(f'[hub_assist] answer_len={len(answer)} sources={sources} keys={list(data.keys())}')
                # DEBUG: print the raw API answer to the console
                print(f'[hub_assist][DEBUG] question: {question}', flush=True)
                print(f'[hub_assist][DEBUG] raw answer: {answer}', flush=True)
                print(f'[hub_assist][DEBUG] full response: {json.dumps(data, ensure_ascii=False)}', flush=True)

                if answer:
                    full_answer.append(answer)
                    yield f"data: {json.dumps({'chunk': answer})}\n\n"
                elif 'error' in data:
                    err = f"AJi-prop error: {data['error']}"
                    full_answer.append(err)
                    yield f"data: {json.dumps({'chunk': err})}\n\n"
                else:
                    no_ans = f"Knowledge Base returned no answer. (model={data.get('model','?')}, chunks={data.get('chunks_used',0)})"
                    full_answer.append(no_ans)
                    yield f"data: {json.dumps({'chunk': no_ans})}\n\n"

            else:
                # ── Fallback: AJiBrain (Ollama direct) ────────────────────
                session = requests.Session()
                session.trust_env = not settings.AJIBRAIN_TRUST_PROXY

                resp = session.post(
                    f"{settings.AJIBRAIN_API_BASE}/api/chat/",
                    json={
                        'model': settings.AJIBRAIN_ASSIST_MODEL,
                        'messages': [
                            {'role': 'system', 'content': system_prompt},
                            {'role': 'user',   'content': question + lang_instruction},
                        ],
                        'stream':     True,
                        'keep_alive': -1,
                    },
                    stream=True,
                    timeout=settings.AJIBRAIN_TIMEOUT,
                )

                for line in resp.iter_lines():
                    if line:
                        try:
                            data  = json.loads(line.decode('utf-8'))
                            chunk = data.get('message', {}).get('content', '')
                            if chunk:
                                full_answer.append(chunk)
                                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                            if data.get('done'):
                                break
                        except Exception:
                            continue

                # DEBUG: print the full streamed answer to the console
                print(f'[hub_assist][DEBUG] question: {question}', flush=True)
                print(f'[hub_assist][DEBUG] AJiBrain answer: {"".join(full_answer)}', flush=True)

        except requests.exceptions.ConnectionError as e:
            svc       = 'AJi-prop' if getattr(settings, 'AJIPROP_API_BASE', '') else 'AJiBrain'
            error_msg = f"{svc} is not reachable. Please ensure {svc} is running."
            yield f"data: {json.dumps({'chunk': error_msg})}\n\n"
            full_answer.append(error_msg)
        except Exception as e:
            # What went wrong goes to the log, where somebody can act on it. The
            # reader gets a sentence. A dropped connection used to reach the page
            # as ConnectionResetError(10054, 'An existing connection was forcibly
            # closed by the remote host', None, 10054, None), which tells an
            # employee nothing except that the software is broken.
            logger.exception('[assist] answering failed')
            error_msg = ("Something went wrong reaching the knowledge base. "
                         "Please try again — if it keeps happening, tell IT.")
            yield f"data: {json.dumps({'chunk': error_msg})}\n\n"
            full_answer.append(error_msg)
        finally:
            if full_answer:
                msg.answer = ''.join(full_answer)
                msg.save(update_fields=['answer'])
            done_payload = {'done': True, 'msg_id': msg.pk}
            if sources:
                done_payload['sources'] = sources
            yield f"data: {json.dumps(done_payload)}\n\n"

    response = StreamingHttpResponse(stream_response(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@login_required
def session_messages(request, session_pk):
    """GET /assist/session/<pk>/ — load messages of a past session as JSON."""
    session = AssistSession.objects.filter(
        pk=session_pk, user=request.user
    ).prefetch_related('messages').first()
    if not session:
        return JsonResponse({'messages': []})
    data = [{'id': m.pk, 'q': m.question, 'a': m.answer, 'ts': m.created_at.strftime('%H:%M')}
            for m in session.messages.order_by('created_at')]
    return JsonResponse({'messages': data, 'name': session.name or session.auto_name()})


@login_required
def history(request):
    messages = AssistMessage.objects.filter(user=request.user).order_by('-created_at')[:20]
    return JsonResponse({
        'messages': [
            {'id': m.pk, 'question': m.question, 'answer': m.answer, 'created_at': m.created_at.isoformat()}
            for m in messages
        ]
    })


@login_required
def clear_history(request):
    """Clear this reader's AJiAssist history.

    Two bugs lived here. It only acted on DELETE while the only thing that calls
    it is a POST form, so the button cleared nothing — and then it answered
    {"ok": true} regardless, so it reported success for work it had not done.
    And a JsonResponse handed to a plain form post is simply displayed, so the
    reader ended up looking at raw JSON instead of their page.
    """
    if request.method not in ('POST', 'DELETE'):
        return JsonResponse({'error': 'POST required.'}, status=405)

    removed, _ = AssistMessage.objects.filter(user=request.user).delete()

    wants_json = (request.method == 'DELETE'
                  or request.headers.get('X-Requested-With') == 'XMLHttpRequest')
    if wants_json:
        return JsonResponse({'ok': True, 'removed': removed})
    messages.success(request, 'Your AJiAssist history was cleared.'
                     if removed else 'There was nothing to clear.')
    return redirect('assist_widget')
