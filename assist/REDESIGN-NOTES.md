# AJi Assist — what exists today

Written before the UI/UX redesign, so the redesign preserves behaviour rather
than rediscovering it. Everything below was read out of the code, not assumed.

## The two surfaces

| | popup | fullscreen |
|---|---|---|
| markup | `templates/base.html`, `#askWrap` (site-wide) | `templates/assist/widget.html` |
| route | none — always on the page | `/assist/` → `assist_widget` |
| width | `min(560px, 100vw - 36px)`, height `min(620px, …)` | two columns, `1fr 290px` |
| sections | `#askMain`, `#askBody`, `#askChips`, `#askInput`, `#askHistView`, `#askHistList` | `.aw-layout`, `.aw-body`, `.aw-ask`, `.aw-side` |
| functions | `askToggle`, `askSend`, `askShowHistory`, `askShowMain` | `awSend`, `awLoadSession` (inline) |

They are **two independent implementations** of the same thing. Both post to the
same endpoints, so history is shared — that must survive any rewrite.

## Backend contract — do not change without checking both surfaces

- `POST /assist/ask/` — field `question`. Answers **Server-Sent Events**:
  `data: {"chunk": "..."}` per chunk. Not JSON-in-one-go. Any new composer must
  read the stream or the assistant appears to hang.
  - Note in the code: Waitress buffers `StreamingHttpResponse` fully before
    sending, so streaming behaves differently in production than in `runserver`.
- `GET /assist/session/<pk>/` → `{messages: [{id, q, a, ts}], name}`
- `GET /assist/history/` → last 20 `{id, question, answer, created_at}`
- `POST /assist/history/clear/` → `{ok, removed}`; already handles XHR
- Exports, all per session: `…/export/docx`, `…/export/pdf`, `…/export/xlsx`,
  and per message `…/export/table.xlsx`

## Models

- `AssistSession` — user, name, started_at, ended_at, is_active. One active
  session per user via `_get_or_create_session`. `auto_name()` titles it.
- `AssistMessage` — user, session, question, answer, `context_snapshot`, created_at

## Capabilities that genuinely exist

The sidebar in the redesign should describe **these** and nothing more. They come
from `INTENT_KEYWORDS`, which decides what context is fetched for a question:

employee · leave · salary · project · policy · news · community · calendar ·
skills · extension · suggestion

`build_context()` layers: the signed-in employee and their manager first, then
whatever the detected intents pull in. `_format_source()` renders the citations
shown under an answer — keep them.

Quick prompts are **editable data**, not markup: `GlobalSetting`
`assist_quick_prompts`, a JSON list of `{label, prompt}`. The redesign's start
screen should read that list rather than hard-coding suggestions.

## Watch out for

- The template variable is `assist_msgs`, deliberately not `messages` — that
  name shadows Django's messages framework and the site toasts start trying to
  render `AssistMessage` rows.
- The model runs at `localhost:8001` (`qwen2.5:14b`) and was **not running**
  during this work, so answers could not be exercised end to end. Test with it
  up before judging the streaming UI.
- `{# … #}` comments in Django templates are single-line only. `manage.py check`
  now fails on multi-line ones (`core.E001`).

## The redesign — done

The two implementations are one. `window.AJiAssist.send(question, {body, input,
chips, empty})` in `base.html` drives both surfaces; the page and the panel pass
their own elements. Messages carry the same `.ask-msg` classes either side, so
one stylesheet dresses both and `attachMsgToolbar` works in each.

- **`/assist/`** — the same shell as Messages, deliberately: the geometry is
  copied from `.ch-shell.mode-page`, so both come out at the same size, radius,
  margin and border, and the assistant reads as a module of the Hub rather than
  a separate product. Conversations on the left and nothing else — the earlier
  capability groups were describing the software to the person using it.
  Conversation at an 820px measure; composer pinned; a welcome state that reads
  the quick prompts out of `GlobalSetting`, so the page suggests what the panel
  suggests.
- **Word / PDF / Excel** — one segmented control, each format named and
  carrying its own document mark. Three grey glyphs are three identical grey
  glyphs and nobody reads which is which; three yellow slabs shout the least
  important thing on the screen.

## The exported files

They used to come out in Word's defaults: Calibri headings and the *Light Grid
Accent 1* table style, which is Microsoft's blue. Nothing about them said AJi.

All three now open the same way — the logo, a yellow rule, the title, the date
— and use the three brand colours and nothing else. A question is a yellow band;
a table is a yellow header with black text on a black grid; body text is black.
Hierarchy is weight, size and space, because there is no fourth colour to spend.

Fonts are the two the kit allows. Draft B has **no Arabic at all** (577 glyphs,
none of them Arabic script), so anything containing Arabic is set wholly in IBM
Plex Sans Arabic, which covers both scripts — rather than switching font
mid-sentence.

`assist/build_brand_fonts.py` exists because the brand fonts ship as .otf with
PostScript (CFF) outlines and ReportLab refuses those outright. It converts the
curves to TrueType and writes `static/fonts/*.ttf`, reading the .otf files the
site already serves as webfonts, so it is reproducible from the repository
alone. The output is committed; nothing is converted per request. Word and Excel
only *name* a font, so Draft B has to be installed on the machine opening those
two — the PDF embeds it and always looks right.

If a converted font goes missing the PDF export raises rather than quietly
falling back to Helvetica, which the brand kit asks for explicitly.
- **The panel** — 600px wide, light header, a labelled *Full screen* control.
  Hidden on `/assist/` itself, the way it already is on Messages: it would show
  the very conversation filling the screen behind it.

Both halves of a conversation carry a bubble: the question black on the right,
the answer white on the left, flattened corner on the sender's side — the same
pair the chat uses. The answer went without one at first, on the argument that
an answer is not a card; what that actually produced was two halves of a
conversation looking like different kinds of thing, with no way to see at a
glance where one reply stopped and the next began. The answer bubble is wide and
never clips, and an answer containing a table takes the full measure, so a long
reply is still a page of text rather than a receipt.

Headings are real `<h2>`–`<h6>` so the stylesheet controls the hierarchy. The
renderer used to emit them as inline-styled `<div>`s, which no sheet could
override.

Fixed while building, each found by testing rather than reading:

- The toolbar is inserted *after* a message, not inside it (so "Copy" is not
  itself copied). The first hover rule assumed a child, so it never appeared;
  the duplicate guard looked inside too, so re-rendering stacked a second row.
- A failure reached the reader as `ConnectionResetError(10054, …)`. The
  exception goes to the log now and the reader gets a sentence.
- The backend takes 20s to give up when the model is down. The dots say what
  they are waiting for at 4s and again at 12s.
- On mobile the conversation collapsed to 0px — the sidebar becomes a fixed
  drawer, and a two-track grid then handed the conversation the 0 track.
- The drawer opened and shut on the same tap: the button that opens it sits
  inside the element whose click closes it.
- `.content` caps every page at `--shell-w` and pads it 102px vertically, which
  pushed the composer below the fold. Full-bleed on this page only.
- `window.AJiAssist` is defined ~680 lines below where `{% block content %}` is
  printed, so the page's own script could not call it while parsing. The first
  call threw and took the two lines after it — the scroll to the end and the
  focus — with it. The page waits for `DOMContentLoaded` now.
- *New conversation* linked back to `/assist/`, which reloads the **active**
  session, so the welcome state and its suggestions could never be reached.
  There is an `assist_new` view now: it closes the current session if anything
  was asked in it, then redirects.
- The mobile drawer is `position:absolute` but `.asst` was not positioned, so it
  measured itself against the viewport and hung out of the rounded shell.
- The CMS edit bar is fixed to the bottom-left of every page and *Clear history*
  was underneath it. The footer is right-aligned.
- The markdown renderer had no blockquote, so `>` arrived as literal
  punctuation mid-sentence — the stylesheet had been styling an element that
  was never produced.
- Exported table cells were never run through the markdown stripper, so
  `**General Design**` printed with its asterisks and `<br>` printed as itself.
- Unsupported characters all became `?`. The knowledge base writes "Part‑G"
  with a non-breaking hyphen and "0.75 m" with a narrow no-break space, neither
  of which a display face carries. Unknown characters are now answered by
  Unicode category — a space separator becomes a space, a dash becomes a dash.
- A line break written straight into a Word run lands ahead of that run's
  `w:rPr`, and Word offers to repair the file. The break needs its own run.

Still true: the model at `localhost:8001` was down throughout, so no real answer
has been rendered. Rich answers were verified by pushing markdown through the
same `markdownToHtml` and toolbar the stream uses.
