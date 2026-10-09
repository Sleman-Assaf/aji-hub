/* chat_engine.js
   The chat engine. One implementation, two shells.
   The popup in the corner and the full page at /messaging/ were separate code:
   the popup was a live JSON client, the page was server-rendered with no
   polling and no attachments, so every fix had to be made twice and one of
   them was always behind.
   This drives whichever shell is on the page. A shell is any element with
   data-chat-shell; the engine finds its parts by data attribute rather than by
   id, and sets data-view on it. Which panes that shows is the shell's own CSS
   business — the popup swaps between list and conversation, the full page
   keeps both on screen.

   Served as a static file rather than inlined into every page: the chat
   page was sending 174 KB of uncacheable <script> on every navigation.
   Edit this file, not a template. */

(function () {
  'use strict';

  const SHELL = document.querySelector('[data-chat-shell]');
  if (!SHELL) return;

  const MODE = SHELL.dataset.mode || 'panel';     // 'panel' | 'page'
  const IS_PAGE = MODE === 'page';
  const MAX_BYTES = Number(SHELL.dataset.maxBytes || 26214400);
  /* quickSet, not quick. The shell carried data-quick, and the per-message
     react button carries data-quick too — so closest('[data-quick]') matched the
     shell from anywhere in the chat, and the reaction handler swallowed every
     click in it, including Reply and Edit. Config attributes on the shell are
     named so they cannot collide with a hook. */
  const QUICK = (SHELL.dataset.quickSet || '👍,❤️,😂,😮,😢,🙏').split(',');

  const CHAT = {
    open: IS_PAGE,          // the full page is always "open"
    view: 'list',
    active: null,
    conv: null,             // the open conversation's own row
    convs: [],
    msgs: [],
    participants: [],
    replyTo: null,
    editing: null,
    lastId: 0,
    tmpSeq: 0,
    groupDraft: [],
  };
  window.CHAT = CHAT;

  /* ── the shell's parts ── */
  const $ = sel => SHELL.querySelector(sel);
  const list = () => $('[data-ch-list]');
  const msgsEl = () => $('[data-ch-msgs]');
  const input = () => $('[data-ch-input]');

  function setView(v) {
    CHAT.view = v;
    SHELL.dataset.view = v;
  }

  /* ── little helpers ── */
  function escH(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  window.escH = window.escH || escH;

  function csrf() {
    const v = document.cookie.match('(^|;) ?csrftoken=([^;]*)(;|$)');
    return v ? v[2] : '';
  }

  /* Requests already in flight, by URL. Pressing "Create group" six times made
     six groups: nothing stopped a second press while the first was still going,
     and because the panel gave no sign of working, that is exactly what
     happened. Any write that is already running is now simply not started
     again. */
  const inFlight = new Set();

  function post(url, body, opts) {
    const once = !(opts && opts.allowConcurrent);
    if (once && inFlight.has(url)) return Promise.resolve({ok: false, d: {busy: true}});
    if (once) inFlight.add(url);

    const fd = body instanceof FormData ? body : new FormData();
    if (!(body instanceof FormData) && body) {
      Object.keys(body).forEach(k => {
        const v = body[k];
        if (Array.isArray(v)) v.forEach(x => fd.append(k, x));
        else if (v !== null && v !== undefined) fd.append(k, v);
      });
    }
    fd.append('csrfmiddlewaretoken', csrf());
    return fetch(url, {method: 'POST', body: fd,
                       headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json().then(d => ({ok: r.ok, d})))
      .finally(() => { if (once) inFlight.delete(url); });
  }

  /* Disable a button for the length of its own request and say it is working, so
     a slow reply reads as "in progress" rather than "nothing happened". */
  function busy(btn, label) {
    if (!btn) return () => {};
    const wasDisabled = btn.disabled, html = btn.innerHTML;
    btn.disabled = true;
    if (label) btn.textContent = label;
    return () => { btn.disabled = wasDisabled; btn.innerHTML = html; };
  }

  function initials(name) {
    const p = (name || '').trim().split(/\s+/).filter(Boolean);
    return p.length >= 2 ? (p[0][0] + p[p.length - 1][0]).toUpperCase()
                         : ((p[0] || '?')[0] || '?').toUpperCase();
  }

  function relTs(iso) {
    if (!iso) return '';
    const d = new Date(iso), now = new Date();
    if (d.toDateString() === now.toDateString())
      return d.toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit'});
    if ((now - d) / 86400000 < 2) return 'Yesterday';
    return d.toLocaleDateString('en-GB', {day: 'numeric', month: 'short'});
  }

  function clock(iso) {
    return new Date(iso).toLocaleTimeString('en-GB',
      {hour: '2-digit', minute: '2-digit'});
  }

  function icon(name, size, stroke) {
    return (typeof svgIcon === 'function') ? svgIcon(name, size, stroke) : '';
  }

  /* One mark, four states, in one place — the list row and the conversation
     header both draw this, and they used to disagree: the header put a dot on
     the avatar *and* a second one beside the words next to it, so somebody
     online was shown to be online twice.

     Offline draws nothing at all. A grey dot on every row is a row of grey dots
     and says only that the feature exists; absence already means absence. */
  const DOT_STATES = {online: 'Active now', busy: 'In a call',
                      presenting: 'Presenting'};

  function dotHtml(state, label) {
    if (!DOT_STATES[state]) return '';
    return `<span class="ch-dot s-${state}" title="${escH(label || DOT_STATES[state])}"></span>`;
  }

  /* ── who is there, kept current ──

     Presence used to be told once and then left: the list learned it when the
     conversation list was fetched, the header learned it when the chat was
     opened, and the people picker never learned it at all. So a green dot could
     outlive somebody going home, and searching for a colleague in order to ring
     them said nothing about whether they were at their desk.

     It rides on the poll now — every four seconds while the chat is in front.
     The browser sends the ids it is actually showing and applies what comes
     back in place, without redrawing anything: a list that rebuilt itself every
     four seconds would flicker under the cursor, and a redraw mid-search would
     throw away what somebody had typed.

     `presenceOf` is the one source for all three surfaces, so they cannot
     disagree about the same person the way they used to. */
  const presenceOf = new Map();          // user id -> {presence, label}

  /* Everybody whose state is on screen: the other side of each direct chat in
     the list, whoever the open conversation is with, and every row of the people
     picker. */
  function watchedIds() {
    const ids = new Set();
    (CHAT.convs || []).forEach(c => { if (c.other_id) ids.add(c.other_id); });
    if (CHAT.conv && CHAT.conv.other_id) ids.add(CHAT.conv.other_id);
    SHELL.querySelectorAll('[data-person]').forEach(el =>
      ids.add(Number(el.dataset.person)));
    return [...ids].filter(Boolean);
  }

  function applyPresence(map) {
    if (!map) return;
    Object.keys(map).forEach(k => {
      const id = Number(k), at = map[k] || {};
      const was = presenceOf.get(id);
      if (was && was.presence === at.presence) return;
      presenceOf.set(id, at);
      paintPresence(id, at);
    });
  }

  /* Move one person's mark everywhere it appears. The row is found by the id it
     already carries rather than by rebuilding the list, so hover, focus and the
     scroll position all survive somebody coming online. */
  function paintPresence(id, at) {
    const state = at.presence || 'offline', label = at.label || '';

    (CHAT.convs || []).forEach(c => {
      if (c.other_id !== id) return;
      c.presence = state;
      c.presence_label = label;
      c.is_online = state !== 'offline';
      const row = list() && list().querySelector(`[data-conv="${c.id}"]`);
      if (row) swapDot(row, afterAvatar(row), state, label);
    });

    SHELL.querySelectorAll(`[data-person="${id}"]`).forEach(row => {
      swapDot(row, afterAvatar(row), state, label);
      const sub = row.querySelector('.ch-prev');
      // The role stays: "Structural Engineer · In a call" says more than either
      // half alone, and losing the role would make the row worse to search with.
      if (sub && sub.dataset.role !== undefined) {
        sub.textContent = statusLine(sub.dataset.role, state, label);
      }
    });

    if (CHAT.conv && CHAT.conv.other_id === id) {
      CHAT.conv.presence = state;
      CHAT.conv.presence_label = label;
      const sub = $('[data-ch-sub]');
      if (sub && !CHAT.conv.is_group) {
        sub.textContent = label || 'Offline';
        sub.classList.toggle('offline', state === 'offline');
      }
      const av = $('[data-ch-av]'), head = av && av.closest('.ch-conv-head');
      if (head) {
        head.dataset.presence = state;
        // Inside the avatar's own wrapper, which is the positioned box the
        // header's dot is placed against. Beside it instead would put the mark
        // in the middle of the header, next to the name.
        swapDot(head, html => av.insertAdjacentHTML('beforeend', html),
                state, label);
      }
    }
  }

  /* One mark per container, put where that container expects it.

     The dot is positioned against different things in different places — the row
     in the list, the avatar's wrapper in the header — so the caller says where it
     goes and this only guarantees there is exactly one. Removing the old one from
     the whole container first is the part that matters: appending without it left
     two dots on somebody who moved from a call to merely online. */
  function swapDot(container, place, state, label) {
    const old = container.querySelector('.ch-dot');
    if (old) old.remove();
    const html = dotHtml(state, label);
    if (html) place(html);
  }

  /* The line under a name in the people list: what they do, and what they are
     doing about it right now.

     Offline adds nothing, for the same reason the dot draws nothing: a page of
     three hundred colleagues with "· Offline" after every one of them is three
     hundred repetitions of a word that carries no information, and it buries the
     two rows that say "In a call". Absence already means absence. */
  function statusLine(role, state, label) {
    const now = (state && state !== 'offline') ? (label || '') : '';
    return [role || '', now].filter(Boolean).join(' · ');
  }

  function afterAvatar(row) {
    return html => {
      const av = row.querySelector('.ch-av');
      if (av) av.insertAdjacentHTML('afterend', html);
      else row.insertAdjacentHTML('afterbegin', html);
    };
  }

  /* Message text, with @usernames drawn as names.
     The text is escaped first and the chip is built here from values that have
     been escaped again — nothing from the message body ever reaches innerHTML
     unescaped, which matters because this is the one place a person's own words
     are turned into markup.
     Longest usernames first: "nael.shunnar" must not be matched and consumed by
     a shorter "nael" belonging to somebody else. */
  function textHtml(s) {
    let out = escH(s);
    const known = (CHAT.participants || []).concat(CHAT.me ? [CHAT.me] : [])
      .filter(p => p && p.username)
      .sort((a, b) => b.username.length - a.username.length);
    known.forEach(p => {
      const safe = p.username.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp('(^|\\s)@' + safe + '(?![\\w.\\-])', 'gi');
      const mine = CHAT.me && p.username === CHAT.me.username;
      out = out.replace(re, (all, pre) =>
        pre + `<span class="ch-at${mine ? ' me' : ''}">@${escH(p.name)}</span>`);
    });
    return out;
  }

  function avatarHtml(o, size) {
    const s = size || 40;
    const style = `width:${s}px;height:${s}px;font-size:${Math.round(s / 2.9)}px`;
    if (o.photo)
      return `<span class="ch-av" style="${style};padding:0"><img src="${escH(o.photo)}" alt=""></span>`;
    return `<span class="ch-av${o.is_group ? ' grp' : ''}" style="${style}">` +
           (o.is_group ? icon('users-three', Math.round(s * 0.5), 1.7)
                       : escH(o.initials || initials(o.with_name || o.name))) + '</span>';
  }

  /* ══ the conversation list ══════════════════════════════════════════════ */

  /* One request at a time. Opening the page fired this once at startup and again
     from the first poll, so the list was fetched twice within 150ms for the same
     answer. A second caller now waits on the first instead of starting another. */
  let convsInFlight = null, listLoadedAt = 0;

  function loadConvs(then) {
    if (convsInFlight) {
      return then ? convsInFlight.then(then) : convsInFlight;
    }
    listLoadedAt = Date.now();
    convsInFlight = fetch('/messaging/api/conversations/',
                          {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json())
      .then(d => {
        CHAT.convs = d.conversations || [];
        /* What the poll has learned since this request was sent wins over what
           it carries. The list is the slower of the two, so a reply that was in
           flight while somebody joined a call would otherwise put the older
           answer back on screen and hold it there until the next poll. */
        CHAT.convs.forEach(c => {
          const at = c.other_id && presenceOf.get(c.other_id);
          if (at) {
            c.presence = at.presence;
            c.presence_label = at.label;
            c.is_online = at.presence !== 'offline';
          } else if (c.other_id) {
            presenceOf.set(c.other_id, {presence: c.presence, label: c.presence_label});
          }
        });
        badge();
        renderList();
      })
      .catch(() => renderList())
      .finally(() => { convsInFlight = null; });
    return then ? convsInFlight.then(then) : convsInFlight;
  }

  /* Some things change without a message arriving to announce them: somebody
     comes online, joins a call, starts sharing a screen. Nothing in the poll
     reports those, so the list would sit on whatever it was told last — a green
     dot beside somebody who left an hour ago.
     Not on every poll: the list is the heavier of the two queries and it runs
     for everybody signed in. Every twenty seconds is often enough for presence
     and cheap enough to leave on. */
  const LIST_STALE_MS = 20000;

  function refreshListIfStale() {
    if (Date.now() - listLoadedAt > LIST_STALE_MS) loadConvs();
  }

  function badge() {
    const total = CHAT.convs.reduce((s, c) => s + (c.unread || 0), 0);
    setBadge(total);
  }

  function setBadge(total) {
    // The tab count comes from here too, so the number in brackets and the badge
    // on the button can never disagree.
    if (window.ajiTitle) window.ajiTitle.set('chat', total);
    const b = document.getElementById('chatBadge');
    if (!b) return;
    if (total > 0) { b.textContent = total > 9 ? '9+' : total; b.style.display = 'flex'; }
    else b.style.display = 'none';
  }

  let listSig = '';

  function renderList(force) {
    const el = list();
    if (!el) return;
    const q = ($('[data-ch-search]') || {}).value || '';
    /* Rebuilding the list is what made the panel feel unsteady: the poll calls
       this several times a minute, innerHTML throws away every row, and anything
       mid-hover or mid-scroll jumped. Redraw only when the content actually
       differs. */
    /* c.presence, not c.is_online. The boolean cannot tell a call from a shared
       screen, so somebody moving between those two states changed the dot the
       list would draw and not the signature that decides whether to draw it. */
    const sig = JSON.stringify([q, CHAT.active, CHAT.convs.map(c =>
      [c.id, c.unread, c.pinned, c.last_ts, c.last_message, c.with_name, c.presence])]);
    if (!force && sig === listSig) return;
    listSig = sig;
    let rows = CHAT.convs;
    if (q.trim()) {
      const needle = q.trim().toLowerCase();
      rows = rows.filter(c => (c.with_name || '').toLowerCase().includes(needle)
                           || (c.last_message || '').toLowerCase().includes(needle));
    }
    if (!rows.length) {
      el.innerHTML = '<div class="ch-empty">' + icon('chat', 32, 1.6) +
        '<div>' + (q.trim() ? 'No chats match that.'
                            : 'No chats yet.<br>Start one with anyone at AJi.') +
        '</div></div>';
      return;
    }
    el.innerHTML = rows.map(c => `
      <div class="ch-row${CHAT.active === c.id ? ' active' : ''}${c.unread ? ' unread' : ''}"
           data-conv="${c.id}" tabindex="0" role="button">
        ${avatarHtml(c, 44)}
        ${dotHtml(c.presence, c.presence_label)}
        <div class="ch-meta">
          <div class="ch-nm">${c.pinned ? '<span class="ch-pinned" title="Pinned">' + icon('push-pin', 11) + '</span>' : ''}<span>${escH(c.with_name)}</span></div>
          <div class="ch-prev">${c.last_icon ? `<i class="ch-prev-ic">${icon(c.last_icon, 13)}</i>` : ''}${escH((c.last_message || 'No messages yet').slice(0, 60))}</div>
        </div>
        <div class="ch-right">
          <span class="ch-ts">${relTs(c.last_ts)}</span>
          ${c.unread ? `<span class="ch-unread">${c.unread}</span>` : ''}
          <button type="button" class="ch-rowmenu" data-menu="${c.id}"
                  aria-label="Chat options for ${escH(c.with_name)}">${icon('dots-three', 16)}</button>
        </div>
      </div>`).join('');
  }

  /* One delegated handler rather than an onclick per row: the list is redrawn
     on every poll, and inline handlers would be rebuilt with it. */
  SHELL.addEventListener('click', e => {
    const menu = e.target.closest('[data-menu]');
    if (menu) { e.stopPropagation(); rowMenu(menu, Number(menu.dataset.menu)); return; }
    const row = e.target.closest('[data-conv]');
    if (row) { openConv(Number(row.dataset.conv)); return; }
  });
  SHELL.addEventListener('keydown', e => {
    const row = e.target.closest('[data-conv]');
    if (row && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      openConv(Number(row.dataset.conv));
    }
  });

  const searchBox = $('[data-ch-search]');
  if (searchBox) searchBox.addEventListener('input', renderList);

  /* ── the per-chat menu: pin and delete ── */
  function rowMenu(anchor, convId) {
    const conv = CHAT.convs.find(c => c.id === convId)
              || (CHAT.conv && CHAT.conv.id === convId ? CHAT.conv : {});
    const items = [
      {label: conv.pinned ? 'Unpin chat' : 'Pin chat', ic: 'push-pin',
       run: () => post(`/messaging/${convId}/pin/`).then(() => loadConvs())},
    ];
    if (conv.is_group) {
      items.push({label: 'Group info', ic: 'users-three', run: () => {
        if (CHAT.active !== convId) openConv(convId);
        setTimeout(openInfo, CHAT.active === convId ? 0 : 400);
      }});
    }
    /* This conversation's calls only. Offered here as well as from the header
       button because "when did I last speak to this person" is asked about one
       chat, and finding it in a log of every call there has ever been is work.
       The pane exists on the full page and not in the popup, so the item does
       too — a menu entry that opens nothing is worse than no entry. */
    if (IS_PAGE) {
      items.push({label: 'Call history', ic: 'phone-call',
                  run: () => openCalls(convId)});
    }
    items.push({label: 'Delete chat', ic: 'trash', danger: true, run: () => {
      if (!window.confirm('Delete this chat?\n\nIt leaves your list and its '
        + 'history is hidden from you. The other person keeps their copy, and '
        + 'the chat comes back if they message you again.')) return;
      post(`/messaging/${convId}/delete/`).then(() => {
        if (CHAT.active === convId) { CHAT.active = null; setView('list'); }
        loadConvs();
      });
    }});
    popMenu(anchor, items);
  }

  let openPop = null, popAnchor = null;

  /* Pressing the button that opened a menu closes it again.
     It used not to. Both callers stop the click reaching the document — they
     have to, or the menu would be shut by the very click that asked for it —
     so the second press ran closePop() and then built an identical menu on the
     next line. Nothing on screen changed, which reads as a button that does
     nothing rather than one that toggles. */
  function popMenu(anchor, items) {
    const again = openPop && popAnchor === anchor;
    closePop();
    if (again) return;
    const m = document.createElement('div');
    m.className = 'ch-menu';
    m.setAttribute('data-chat-layer', '');
    items.forEach(it => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ch-menu-i' + (it.danger ? ' danger' : '');
      b.innerHTML = icon(it.ic, 14) + '<span>' + escH(it.label) + '</span>';
      b.addEventListener('click', ev => { ev.stopPropagation(); closePop(); it.run(); });
      m.appendChild(b);
    });
    document.body.appendChild(m);
    const r = anchor.getBoundingClientRect();
    m.style.left = Math.round(Math.min(r.left, window.innerWidth - m.offsetWidth - 8)) + 'px';
    m.style.top = Math.round(Math.min(r.bottom + 4,
                    window.innerHeight - m.offsetHeight - 8)) + 'px';
    openPop = m;
    popAnchor = anchor;
    if (anchor.hasAttribute('aria-label')) anchor.setAttribute('aria-expanded', 'true');
  }
  function closePop() {
    if (popAnchor && popAnchor.hasAttribute('aria-expanded'))
      popAnchor.setAttribute('aria-expanded', 'false');
    if (openPop) { openPop.remove(); openPop = null; }
    popAnchor = null;
  }
  document.addEventListener('click', closePop);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closePop(); });

  /* ══ one conversation ═══════════════════════════════════════════════════ */

  function openConv(convId) {
    CHAT.active = convId;
    CHAT.replyTo = null;
    CHAT.editing = null;
    // The previous chat's messages are not this one's, and leaving them in place
    // matters: the merge below keeps anything sent while the fetch is in flight,
    // and it must not be able to pull the last conversation's log across.
    CHAT.msgs = [];
    drawReplyStrip();
    setView('conv');
    renderList();

    const known = CHAT.convs.find(c => c.id === convId);
    if (known) drawHeader(known);
    // A shimmer shaped like messages, not the word "Loading…" — it makes the
    // wait feel like the chat arriving rather than the page stalling.
    msgsEl().innerHTML = '<div class="ch-skel"><i></i><i></i><i></i><i></i></div>';

    fetch(`/messaging/${convId}/api/messages/`,
          {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json())
      .then(d => {
        if (CHAT.active !== convId) return;   // the reader moved on while we waited
        CHAT.conv = d.conversation;
        CHAT.participants = d.participants || [];
        CHAT.me = d.me || CHAT.me || null;
        /* Merge, do not replace. Somebody who opens a chat and types straight
           away can have their message stored and drawn before this snapshot
           arrives; overwriting the list with the snapshot made the message
           vanish from the screen although it had been sent. */
        const incoming = d.messages || [];
        const known = new Set(incoming.map(m => m.id));
        const sentMeanwhile = CHAT.msgs.filter(m => m.id && !known.has(m.id));
        CHAT.msgs = incoming.concat(sentMeanwhile).sort((a, b) => a.id - b.id);
        drawHeader(d.conversation);
        renderMsgs();
        // Opening the chat is where most people meet the wish: the popup is
        // usually shut when it lands, so celebrating only on arrival would mean
        // almost nobody ever saw it.
        maybeCelebrate(CHAT.msgs);
        const row = CHAT.convs.find(c => c.id === convId);
        if (row) { row.unread = 0; badge(); renderList(); }
        if (IS_PAGE && history.replaceState)
          history.replaceState(null, '', '/messaging/?c=' + convId);
      })
      .catch(() => {
        msgsEl().innerHTML = '<div class="ch-empty">Could not load this chat.</div>';
      });

    if (!IS_PAGE || window.innerWidth > 900) {
      const i = input();
      if (i) setTimeout(() => i.focus(), 40);
    }
  }

  function drawHeader(conv) {
    const av = $('[data-ch-av]');
    if (av) av.innerHTML = avatarHtml(conv, IS_PAGE ? 40 : 36);
    const t = $('[data-ch-title]');
    // Wrapped, not set as text. `.ch-nm` is a flex container so the list can
    // put a pin beside the name, and text-overflow does nothing to a bare text
    // node inside one — which is why a name too long for the header was being
    // cut off mid-letter with no ellipsis to show for it.
    if (t) t.innerHTML = '<span>' + escH(conv.with_name || '') + '</span>';
    const s = $('[data-ch-sub]');
    const state = conv.is_group ? '' : (conv.presence || 'offline');
    if (s) {
      /* What the group is for, when somebody has said, and the member count
         otherwise. A description that can only be seen by opening Group info is
         a description nobody reads — and "3 members" is a fact anybody can get
         from the member list they are one press away from. */
      if (conv.is_group) {
        s.textContent = conv.description
          || (conv.members + ' member' + (conv.members === 1 ? '' : 's'));
        s.title = conv.description ? conv.description : '';
      } else {
        s.textContent = conv.presence_label || 'Offline';
        s.title = '';
      }
      s.classList.toggle('offline', state === 'offline');
    }
    /* The mark goes beside the avatar, and only there. Said on the header
       rather than inside it, because the avatar is redrawn from scratch on
       every header update and a class put on that would be thrown away. */
    const head = av && av.closest('.ch-conv-head');
    if (head) {
      head.dataset.presence = state;
      const old = head.querySelector('.ch-dot');
      if (old) old.remove();
      if (av && DOT_STATES[state])
        av.insertAdjacentHTML('beforeend', dotHtml(state, conv.presence_label));
    }
  }

  /* Today, Yesterday, then the date. Written out — "27 July 2026", not
     "Mon 27 Jul" — because an abbreviation saves nothing on a line that has a
     whole column to itself, and reads like a log entry. */
  function dayLabel(d) {
    const now = new Date();
    if (d.toDateString() === now.toDateString()) return 'Today';
    const y = new Date(now);
    y.setDate(y.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return 'Yesterday';
    const sameYear = d.getFullYear() === now.getFullYear();
    return d.toLocaleDateString('en-GB', sameYear
      ? {day: 'numeric', month: 'long'}
      : {day: 'numeric', month: 'long', year: 'numeric'});
  }

  function reactionsHtml(m) {
    if (!m.reactions || !m.reactions.length) return '';
    return '<span class="ch-reacts">' + m.reactions.map(r =>
      `<button type="button" class="ch-react${r.mine ? ' mine' : ''}" data-react="${m.id}"` +
      ` data-emoji="${escH(r.emoji)}" title="${r.mine ? 'Remove your reaction' : 'React'}">` +
      `${escH(r.emoji)}${r.count > 1 ? '<i>' + r.count + '</i>' : ''}</button>`).join('') +
      '</span>';
  }

  /* Every file on a message. Pictures go into a grid together and everything
     else is listed under it, so four screenshots read as one set rather than as
     four separate things stacked down the bubble. The grid tightens as the
     count grows — one big, two or three side by side, four or more in threes —
     which is how a set of photos is normally laid out and keeps a bubble with
     nine images the same size as a bubble with three. */
  function attachListHtml(files) {
    if (!files || !files.length) return '';
    const imgs = files.filter(f => f.is_image && f.url);
    const docs = files.filter(f => !(f.is_image && f.url));
    let out = '';
    if (imgs.length) {
      const cols = imgs.length === 1 ? 1 : imgs.length <= 3 ? 2 : 3;
      out += `<span class="ch-gal c${cols}">` + imgs.map(f =>
        `<a class="ch-att-img" href="${escH(f.url)}" target="_blank" rel="noopener">` +
        `<img src="${escH(f.url)}" alt="${escH(f.name || '')}" loading="lazy"></a>`
      ).join('') + '</span>';
    }
    out += docs.map(f =>
      `<a class="ch-att-file"${f.url ? ` href="${escH(f.url)}" download` : ''}>` +
      icon('file', 18, 1.8) +
      `<span><b>${escH(f.name || 'File')}</b>` +
      (f.size ? `<i>${escH(f.size)}</i>` : '') + '</span>' +
      (f.url ? icon('download-simple', 15) : '') + '</a>'
    ).join('');
    return out;
  }

  function attachHtml(m) {
    // `files` is what the server sends now. `file` is what it sent before, and
    // every message already in a conversation still has only that.
    const files = (m.files && m.files.length) ? m.files
      : (m.file ? [{url: m.file, name: m.filename, is_image: m.is_image,
                    size: m.filesize}] : []);
    return attachListHtml(files);
  }

  function quoteHtml(m) {
    if (!m.reply) return '';
    return `<span class="ch-quote" data-goto="${m.reply.id}">` +
           `<b>${escH(m.reply.sender)}</b>` +
           `<i>${escH(m.reply.text || (m.reply.is_image ? 'Photo' : ''))}</i></span>`;
  }

  function ticks(m) {
    if (!m.is_me) return '';
    return `<span class="ch-tick${m.seen ? ' seen' : ''}">${m.seen ? '✓✓' : '✓'}</span>`;
  }

  /* ── the five minutes you have to correct something ──

     The server decides this and enforces it; `edit_until` is the deadline it
     sends with every message of your own. The browser reads it so that the
     button can retire itself, rather than staying on screen until somebody
     presses it and is told the message is too old — which would make a rule
     look like a fault.

     A deadline, not a countdown: a tab left open for an hour would be an hour
     wrong about "seconds remaining", and right about "until 14:32". */

  function editLeftMs(m) {
    if (!m || !m.edit_until) return 0;
    const until = new Date(m.edit_until).getTime();
    return isNaN(until) ? 0 : until - Date.now();
  }

  function canEditNow(m) {
    return !!(m && m.can_edit && editLeftMs(m) > 0);
  }

  /* m:ss, and only counting seconds under a minute. "4:07 left" while you are
     typing is a clock; "247 seconds" is arithmetic. */
  function editLeftLabel(ms) {
    const s = Math.max(0, Math.ceil(ms / 1000));
    return s < 60 ? s + ' sec left'
                  : Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0') + ' left';
  }

  function bubbleHtml(m, prev, showSep) {
    if (m.system) {
      // A call carries a mark, so it reads as something that happened rather
      // than as a line of prose in the middle of the conversation. The kind
      // comes from the server: matching on the wording here would break the
      // first time anybody translated it.
      const mark = m.kind === 'call' ? icon('phone-call', 13)
                 : m.kind === 'call_missed' ? icon('phone-x', 13) : '';
      const cls = 'ch-sys' + (m.kind ? ' ' + m.kind.replace('_', '-') : '');
      return `<div class="${cls}" data-mid="${m.id}">${mark}` +
             `<span>${escH(m.text)}</span></div>`;
    }
    const grouped = prev && !prev.system && prev.sender_id === m.sender_id && !showSep;
    const showWho = CHAT.conv && CHAT.conv.is_group && !m.is_me && !grouped;
    return `<div class="ch-bubble ${m.is_me ? 'me' : 'them'}${grouped ? ' grp' : ''}" data-mid="${m.id}">` +
      (showWho ? `<span class="ch-who">${escH(m.sender)}</span>` : '') +
      quoteHtml(m) + attachHtml(m) +
      (m.text ? `<span class="ch-txt">${textHtml(m.text)}</span>` : '') +
      `<span class="ts">${m.edited_at ? '<i class="ch-edited">edited</i>' : ''}${clock(m.sent_at)}${ticks(m)}</span>` +
      reactionsHtml(m) +
      '<span class="ch-acts">' +
        `<button type="button" class="ch-act" data-quick="${m.id}" title="React" aria-label="React">${icon('smiley', 15)}</button>` +
        `<button type="button" class="ch-act" data-reply="${m.id}" title="Reply" aria-label="Reply">${icon('arrow-bend-up-left', 15)}</button>` +
        (canEditNow(m) ? `<button type="button" class="ch-act" data-edit="${m.id}"` +
          ` title="Edit · ${editLeftLabel(editLeftMs(m))}" aria-label="Edit">${icon('pencil-simple', 15)}</button>` : '') +
        `<button type="button" class="ch-act danger" data-hide="${m.id}" title="Delete for me" aria-label="Delete for me">${icon('trash', 15)}</button>` +
      '</span>' +
    '</div>';
  }

  /* ── Birthday wishes ──────────────────────────────────────────────────
     A wish marked `birthday` by the sender sets off a burst inside the chat
     window it arrives in — the popup or the full page, whichever is showing.
     Recorded once celebrated, because opening the same conversation again
     should not throw confetti at somebody every time they read it. */

  const CELEBRATED = 'aji.chatCelebrated';
  //: A wish older than a day is history, not news.
  const CELEBRATE_WITHIN_MS = 86400000;

  function celebratedIds() {
    try { return new Set(JSON.parse(localStorage.getItem(CELEBRATED) || '[]')); }
    catch (e) { return new Set(); }
  }

  function markCelebrated(id) {
    try {
      const seen = celebratedIds();
      seen.add(id);
      // Trimmed, or this grows for the life of the browser profile.
      localStorage.setItem(CELEBRATED, JSON.stringify([...seen].slice(-80)));
    } catch (e) { /* private mode, or a full quota — the burst is not worth an error */ }
  }

  function burst() {
    const log = msgsEl();
    const host = (log && log.parentElement) || SHELL;
    if (!host) return;
    if (window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    // The layer is positioned against the host, so the host has to be a
    // containing block — the pane is not guaranteed to be one already.
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';

    const colours = ['#FCD700', '#FFE74D', '#B89400', '#121210', '#E24B4A', '#2461E6'];
    const layer = document.createElement('div');
    layer.className = 'ch-burst';
    let html = '';
    for (let i = 0; i < 36; i++) {
      const left = (Math.random() * 100).toFixed(1);
      const dx = (Math.random() * 140 - 70).toFixed(0);
      const rot = (Math.random() * 720 - 360).toFixed(0);
      const dur = (1100 + Math.random() * 800).toFixed(0);
      const delay = (Math.random() * 260).toFixed(0);
      const round = Math.random() > 0.6 ? '50%' : '2px';
      html += '<i style="left:' + left + '%;background:' + colours[i % colours.length] +
              ';border-radius:' + round + ';animation-duration:' + dur +
              'ms;animation-delay:' + delay + 'ms;--dx:' + dx + 'px;--rot:' + rot + 'deg"></i>';
    }
    layer.innerHTML = html;
    host.appendChild(layer);
    setTimeout(() => layer.remove(), 2400);
  }

  function maybeCelebrate(list) {
    const wishes = (list || []).filter(m => m.kind === 'birthday' && !m.is_me);
    const wish = wishes[wishes.length - 1];
    if (!wish) return;
    if (Date.now() - new Date(wish.sent_at).getTime() > CELEBRATE_WITHIN_MS) return;
    const seen = celebratedIds();
    if (seen.has(wish.id)) return;
    markCelebrated(wish.id);
    burst();
  }

  function renderMsgs() {
    const el = msgsEl();
    if (!el) return;
    if (!CHAT.msgs.length) {
      el.innerHTML = '<div class="ch-empty">' + icon('chat', 34, 1.4) +
                     '<div>No messages yet.<br>Say hello.</div></div>';
      return;
    }
    let lastDay = '';
    el.innerHTML = CHAT.msgs.map((m, i) => {
      const d = new Date(m.sent_at), key = d.toDateString();
      const sep = key !== lastDay
        ? `<div class="ch-date-sep"><span>${dayLabel(d)}</span></div>` : '';
      lastDay = key;
      return sep + bubbleHtml(m, CHAT.msgs[i - 1], !!sep);
    }).join('');
    pinBottom(el);
    CHAT.msgs.forEach(m => { CHAT.lastId = Math.max(CHAT.lastId, m.id); });
  }

  /* Open a chat and you should be looking at the newest message.

     Setting scrollTop once was not enough, and the reason is the pictures. An
     <img> with no dimensions is zero pixels tall until it arrives, so the log
     was scrolled to the bottom of a version of itself that had not finished
     existing; each photo that loaded afterwards pushed the newest message
     further down, and a conversation with two screenshots in it opened halfway
     up. So the bottom is held: once after laying out, again as each image
     lands, and once more on the frame after — and only while the reader has not
     scrolled away, because yanking somebody back down while they are reading
     something further up would be worse than the bug. */
  function pinBottom(el) {
    if (!el) return;
    /* behavior:'instant', and it has to be spelled out. The log sets
       scroll-behavior:smooth so that jumping to a quoted message glides rather
       than teleports — but that rule also applies to this, and assigning
       scrollTop was being turned into an animation from the top of the
       conversation. Opening a chat is not a journey to the newest message; you
       should simply be looking at it. */
    const stick = () => el.scrollTo({top: el.scrollHeight, behavior: 'instant'});
    stick();
    requestAnimationFrame(stick);

    const imgs = [...el.querySelectorAll('img')].filter(i => !i.complete);
    if (!imgs.length) return;

    let held = true, left = imgs.length;
    /* Let go when the reader scrolls up, not when anything scrolls. Watching for
       a scroll event on its own does not work: setting scrollTop fires one, so
       the hold released on the very line that established it and the pictures
       pushed the newest message out of sight exactly as before. Distance from
       the bottom is the thing that actually distinguishes the reader having
       moved from us having moved. */
    const onScroll = () => {
      if (el.scrollHeight - el.scrollTop - el.clientHeight > 80) stop();
    };
    function stop() {
      held = false;
      el.removeEventListener('scroll', onScroll);
    }
    el.addEventListener('scroll', onScroll, {passive: true});

    imgs.forEach(img => {
      const done = () => {
        if (held) stick();
        if (--left === 0 && held) stop();
      };
      img.addEventListener('load', done, {once: true});
      img.addEventListener('error', done, {once: true});
    });
  }

  /* ── per-message actions ── */
  /* Scoped to the message log. closest() walks all the way to <html>, so a hook
     name that also appears on an ancestor silently captures every click below
     it — which is exactly what data-quick on the shell did. Requiring the match
     to sit inside the log rules the whole class of mistake out. */
  SHELL.addEventListener('click', e => {
    const log = e.target.closest('[data-ch-msgs]');
    if (!log) return;
    const within = sel => {
      const el = e.target.closest(sel);
      return el && log.contains(el) ? el : null;
    };
    const q = within('[data-quick]');
    if (q) { e.stopPropagation(); quickReact(q, Number(q.dataset.quick)); return; }
    const rp = within('[data-reply]');
    if (rp) { startReply(Number(rp.dataset.reply)); return; }
    const ed = within('[data-edit]');
    if (ed) { startEdit(Number(ed.dataset.edit)); return; }
    const hd = within('[data-hide]');
    if (hd) { hideMessage(Number(hd.dataset.hide)); return; }
    const rx = within('[data-react]');
    if (rx) { react(Number(rx.dataset.react), rx.dataset.emoji); return; }
    const go = within('[data-goto]');
    if (go) { jumpTo(Number(go.dataset.goto)); return; }
  });

  function hideMessage(mid) {
    if (!window.confirm('Delete this message from your view?\n\n'
        + 'It stays in the other person\'s copy of the conversation.')) return;
    post(`/messaging/msg/${mid}/hide/`).then(({ok}) => {
      if (!ok) return;
      CHAT.msgs = CHAT.msgs.filter(m => m.id !== mid);
      renderMsgs();
      loadConvs();
    });
  }

  function quickReact(anchor, mid) {
    // Toggles, like the menu above and for the same reason.
    const again = openPop && popAnchor === anchor;
    closePop();
    if (again) return;
    const bar = document.createElement('div');
    bar.className = 'ch-quickbar';
    bar.setAttribute('data-chat-layer', '');
    bar.innerHTML = QUICK.map(x =>
      `<button type="button" data-q="${escH(x)}">${escH(x)}</button>`).join('') +
      `<button type="button" class="more" data-more aria-label="More emoji">${icon('plus', 14)}</button>`;
    document.body.appendChild(bar);
    const r = anchor.getBoundingClientRect();
    bar.style.left = Math.round(Math.min(Math.max(8, r.left - bar.offsetWidth / 2),
                       window.innerWidth - bar.offsetWidth - 8)) + 'px';
    bar.style.top = Math.round(Math.max(8, r.top - bar.offsetHeight - 6)) + 'px';
    openPop = bar;
    popAnchor = anchor;
    bar.addEventListener('click', ev => {
      const b = ev.target.closest('[data-q]');
      if (b) { ev.stopPropagation(); closePop(); react(mid, b.dataset.q); return; }
      if (ev.target.closest('[data-more]')) {
        ev.stopPropagation();
        const at = anchor;
        closePop();
        window.ajiEmoji.open(at, emoji => react(mid, emoji));
      }
    });
  }

  function react(mid, emoji) {
    post(`/messaging/msg/${mid}/react/`, {emoji})
      .then(({ok, d}) => {
        if (!ok) return;
        const m = CHAT.msgs.find(x => x.id === mid);
        if (!m) return;
        m.reactions = d.reactions || [];
        redrawBubble(m);
      });
  }

  function redrawBubble(m) {
    const node = msgsEl().querySelector(`[data-mid="${m.id}"]`);
    if (!node) return;
    const i = CHAT.msgs.indexOf(m);
    const wrap = document.createElement('div');
    wrap.innerHTML = bubbleHtml(m, CHAT.msgs[i - 1], node.previousElementSibling
                                && node.previousElementSibling.classList.contains('ch-date-sep'));
    node.replaceWith(wrap.firstElementChild);
  }

  function startReply(mid) {
    const m = CHAT.msgs.find(x => x.id === mid);
    if (!m) return;
    CHAT.editing = null;
    CHAT.replyTo = m;
    drawReplyStrip();
    input().focus();
  }

  function startEdit(mid) {
    const m = CHAT.msgs.find(x => x.id === mid);
    if (!m) return;
    if (!canEditNow(m)) { sweepEdits(); return; }   // the button was already stale
    CHAT.replyTo = null;
    CHAT.editing = m;
    input().value = m.text || '';
    resizeInput();
    syncSend();
    drawReplyStrip();
    input().focus();
  }

  /* Once a second: retire any Edit button whose five minutes are up, keep the
     remaining time on the ones still live, and run the clock in the strip while
     somebody is actually editing.

     The sweep is what makes the rule visible. Without it a chat left open shows
     an Edit button on a message from an hour ago — the server would refuse it,
     so the only thing the button does is mislead. Only the buttons on screen are
     touched, and only when something about them changes. */
  function sweepEdits() {
    const log = msgsEl();
    if (log) {
      log.querySelectorAll('[data-edit]').forEach(btn => {
        const m = CHAT.msgs.find(x => x.id === Number(btn.dataset.edit));
        const left = editLeftMs(m);
        if (!m || left <= 0) { btn.remove(); return; }
        const title = 'Edit · ' + editLeftLabel(left);
        if (btn.title !== title) btn.title = title;
      });
    }

    if (!CHAT.editing) return;
    const left = editLeftMs(CHAT.editing);
    if (left <= 0) {
      /* Mid-sentence, and the deadline has passed. Saving would be refused, so
         the edit is put down here — but what they typed is not thrown away
         silently: the strip says what happened and the text stays in the box, so
         it can be sent as a new message instead. */
      CHAT.editing = null;
      stripNote('The five minutes are up. That message can no longer be edited —'
                + ' send this as a new message instead.');
      syncSend();
      return;
    }
    const clockEl = $('[data-ch-editleft]');
    if (clockEl) clockEl.textContent = editLeftLabel(left);
  }

  setInterval(sweepEdits, 1000);

  /* A line in the strip where the reply and edit banners go, for something that
     needs saying once. It clears itself: a notice that has to be dismissed is a
     second thing to do about a message you have already given up on. */
  let stripNoteTimer = null;

  function stripNote(text) {
    const strip = $('[data-ch-strip]');
    if (!strip) return;
    strip.hidden = false;
    strip.innerHTML = '<span class="ch-strip-b note">' + icon('info', 13) +
      '<b>' + escH(text) + '</b></span>' +
      '<button type="button" onclick="chatCancelStrip()" aria-label="Dismiss">&times;</button>';
    clearTimeout(stripNoteTimer);
    stripNoteTimer = setTimeout(() => {
      if (!CHAT.editing && !CHAT.replyTo) drawReplyStrip();
    }, 7000);
  }

  function cancelStrip() {
    CHAT.replyTo = null;
    if (CHAT.editing) { input().value = ''; resizeInput(); syncSend(); }
    CHAT.editing = null;
    drawReplyStrip();
  }
  window.chatCancelStrip = cancelStrip;

  function drawReplyStrip() {
    const strip = $('[data-ch-strip]');
    if (!strip) return;
    const m = CHAT.replyTo;
    if (CHAT.editing) {
      strip.hidden = false;
      /* The clock goes here rather than on the message, because this is where
         somebody is looking while they retype the sentence — and because the
         window closing mid-edit is the one case where the deadline stops being
         trivia and starts being something they need to know about. */
      strip.innerHTML = '<span class="ch-strip-b">' + icon('pencil-simple', 13) +
        '<b>Editing your message</b><i>' + escH((CHAT.editing.text || '').slice(0, 90)) +
        '</i></span>' +
        '<span class="ch-editleft" data-ch-editleft>' +
        escH(editLeftLabel(editLeftMs(CHAT.editing))) + '</span>' +
        '<button type="button" onclick="chatCancelStrip()" aria-label="Stop editing">&times;</button>';
      return;
    }
    if (!m) { strip.hidden = true; strip.innerHTML = ''; return; }
    strip.hidden = false;
    strip.innerHTML = '<span class="ch-strip-b">' + icon('arrow-bend-up-left', 13) +
      '<b>' + escH(m.sender) + '</b><i>' +
      escH((m.text || (m.is_image ? 'Photo' : 'Attachment')).slice(0, 90)) +
      '</i></span><button type="button" onclick="chatCancelStrip()" aria-label="Cancel reply">&times;</button>';
  }

  /* An image attachment opens over the page rather than in a new tab, which is
     what it did before and is better: leaving the chat to look at a photo means
     losing your place in it. */
  SHELL.addEventListener('click', e => {
    const a = e.target.closest('[data-ch-msgs] .ch-att-img');
    if (!a || !a.querySelector('img')) return;
    e.preventDefault();
    /* The set is this message's pictures and nothing else. Somebody who opens
       the third of four photos wants to see the other three — not to be dropped
       into every image in the conversation with no idea where they are in it. */
    const gal = a.closest('.ch-gal');
    const links = gal ? [...gal.querySelectorAll('.ch-att-img')] : [a];
    const shots = links.map(l => ({
      src: l.getAttribute('href') || l.querySelector('img').src,
      name: l.querySelector('img').getAttribute('alt') || '',
    }));
    lightbox(shots, Math.max(0, links.indexOf(a)));
  });

  /* Open the picture over the page, leave it with one press of anything — the
     ×, Escape, or a click on the backdrop. Somebody looking at a screenshot
     should not have to hunt for the way out. */
  /* The picture viewer. One photo, or a set from one message that you can move
     through — arrows, the keyboard, or by dragging with a finger or the mouse.

     The drag follows the pointer exactly, because a swipe that does not move
     with your finger feels broken. It commits on either distance or speed: a
     slow deliberate drag past a quarter of the width, or a quick flick, both
     count. Requiring distance alone would mean a confident flick does nothing,
     which is the more common gesture of the two.

     At the first and last picture the drag still moves, at a third of the
     distance. Things in the world resist rather than stop dead, and a set that
     simply refuses to move gives no sign whether it is the end or a bug. */
  const LB_COMMIT = 0.25;             // of the width
  const LB_FLICK = 0.45;              // px per ms

  let lbShots = [], lbAt = 0, lbGeom = 0;

  function lightbox(shots, start) {
    let bg = document.getElementById('chLb');
    if (!bg) bg = buildLightbox();
    lbShots = shots;
    lbAt = Math.min(Math.max(start || 0, 0), shots.length - 1);

    const track = bg.querySelector('[data-lb-track]');
    track.innerHTML = shots.map(s =>
      `<div class="ch-lb-slide"><img src="${escH(s.src)}" alt="${escH(s.name)}"
        draggable="false"></div>`).join('');
    bg.classList.toggle('multi', shots.length > 1);
    bg.classList.add('on');
    document.body.style.overflow = 'hidden';
    /* Measured and placed straight away, then again on the next frame.
       Straight away because a requestAnimationFrame does not run in a tab that
       is not being drawn, and the viewer would open with no caption, no counter
       and every picture stacked at the first one. The second pass is for the
       width, which is only right once the layout has settled. */
    lbGeom = stageWidth(bg);
    lbGo(0, false);
    requestAnimationFrame(() => {
      const w = stageWidth(bg);
      if (w && w !== lbGeom) { lbGeom = w; lbGo(0, false); }
    });
  }

  function stageWidth(bg) {
    const stage = bg.querySelector('.ch-lb-stage');
    return stage ? stage.clientWidth : window.innerWidth;
  }

  /* Move by `step`, or redraw where we are. Pixels rather than percentages: the
     track is as wide as every slide together, so a percentage here would be a
     percentage of that and would need dividing by the count on every read. */
  function lbGo(step, animate) {
    const bg = document.getElementById('chLb');
    if (!bg) return;
    lbAt = Math.min(Math.max(lbAt + (step || 0), 0), lbShots.length - 1);
    const track = bg.querySelector('[data-lb-track]');
    track.style.transition = animate === false ? 'none'
      : 'transform 260ms cubic-bezier(.23,1,.32,1)';
    track.style.transform = `translate3d(${-lbAt * lbGeom}px,0,0)`;
    const shot = lbShots[lbAt] || {};
    bg.querySelector('[data-lb-name]').textContent = shot.name || '';
    bg.querySelector('[data-lb-count]').textContent =
      lbShots.length > 1 ? `${lbAt + 1} / ${lbShots.length}` : '';
    const dl = bg.querySelector('[data-lb-dl]');
    dl.href = shot.src || '';
    if (shot.name) dl.setAttribute('download', shot.name);
    bg.querySelector('[data-lb-prev]').disabled = lbAt === 0;
    bg.querySelector('[data-lb-next]').disabled = lbAt === lbShots.length - 1;
  }

  function buildLightbox() {
    const bg = document.createElement('div');
    bg.id = 'chLb';
    bg.className = 'ch-lb';
    // On <body> to escape the panel's overflow, so it has to declare itself
    // part of the chat or closing it closes the panel underneath.
    bg.setAttribute('data-chat-layer', '');
    bg.innerHTML =
      '<div class="ch-lb-bar">' +
        '<span class="ch-lb-name" data-lb-name></span>' +
        '<span class="ch-lb-count" data-lb-count></span>' +
        '<a class="ch-lb-btn" data-lb-dl download title="Download" aria-label="Download">' +
          icon('download-simple', 17) + '</a>' +
        '<button type="button" class="ch-lb-btn" data-lb-x title="Close" ' +
          'aria-label="Close">' + icon('x', 18) + '</button>' +
      '</div>' +
      '<div class="ch-lb-stage"><div class="ch-lb-track" data-lb-track></div></div>' +
      `<button type="button" class="ch-lb-nav prev" data-lb-prev
        aria-label="Previous photo">${icon('caret-left', 20)}</button>` +
      `<button type="button" class="ch-lb-nav next" data-lb-next
        aria-label="Next photo">${icon('caret-left', 20)}</button>`;
    document.body.appendChild(bg);

    const close = () => {
      bg.classList.remove('on');
      document.body.style.overflow = '';
      bg.querySelector('[data-lb-track]').innerHTML = '';   // stop the downloads
    };
    bg._close = close;

    bg.addEventListener('click', ev => {
      if (ev.target.closest('[data-lb-x]')) { close(); return; }
      if (ev.target.closest('[data-lb-prev]')) { lbGo(-1, true); return; }
      if (ev.target.closest('[data-lb-next]')) { lbGo(1, true); return; }
      if (ev.target.closest('[data-lb-dl]')) return;
      // The picture is the one thing that does not close it, so a click to look
      // more closely is not a click that throws it away. Nor does the click that
      // ends a drag.
      if (ev.target.tagName !== 'IMG' && !bg._dragged) close();
    });

    document.addEventListener('keydown', ev => {
      if (!bg.classList.contains('on')) return;
      if (ev.key === 'Escape') { ev.stopPropagation(); close(); }
      if (ev.key === 'ArrowLeft') { ev.preventDefault(); lbGo(-1, true); }
      if (ev.key === 'ArrowRight') { ev.preventDefault(); lbGo(1, true); }
    });

    window.addEventListener('resize', () => {
      if (!bg.classList.contains('on')) return;
      lbGeom = stageWidth(bg);
      lbGo(0, false);
    });

    // ── dragging ──
    const stage = bg.querySelector('.ch-lb-stage');
    const track = bg.querySelector('[data-lb-track]');
    let down = false, x0 = 0, t0 = 0, dx = 0;

    stage.addEventListener('pointerdown', ev => {
      if (lbShots.length < 2 || ev.button) return;
      down = true; dx = 0; x0 = ev.clientX; t0 = ev.timeStamp;
      bg._dragged = false;
      // So the drag survives the pointer leaving the picture. Not fatal if the
      // browser refuses — the drag still works, it just stops at the edge.
      try { stage.setPointerCapture(ev.pointerId); } catch (err) {}
      track.style.transition = 'none';
    });

    stage.addEventListener('pointermove', ev => {
      if (!down) return;
      dx = ev.clientX - x0;
      if (Math.abs(dx) > 4) bg._dragged = true;
      // Resistance rather than a wall at either end.
      const past = (lbAt === 0 && dx > 0) || (lbAt === lbShots.length - 1 && dx < 0);
      const moved = past ? dx / 3 : dx;
      track.style.transform = `translate3d(${-lbAt * lbGeom + moved}px,0,0)`;
    });

    const release = ev => {
      if (!down) return;
      down = false;
      const speed = Math.abs(dx) / Math.max(1, ev.timeStamp - t0);
      const far = Math.abs(dx) > lbGeom * LB_COMMIT;
      const step = (far || speed > LB_FLICK) ? (dx < 0 ? 1 : -1) : 0;
      lbGo(step, true);
      // Cleared after the click that ends the drag has been and gone.
      setTimeout(() => { bg._dragged = false; }, 0);
    };
    stage.addEventListener('pointerup', release);
    stage.addEventListener('pointercancel', release);

    return bg;
  }

  function jumpTo(mid) {
    const node = msgsEl().querySelector(`[data-mid="${mid}"]`);
    if (!node) return;
    node.scrollIntoView({behavior: 'smooth', block: 'center'});
    node.classList.add('flash');
    setTimeout(() => node.classList.remove('flash'), 1200);
  }

  /* ══ composing ══════════════════════════════════════════════════════════ */

  const fileInput = $('[data-ch-file]');
  const emojiBtn = $('[data-ch-emoji]');

  if (emojiBtn) emojiBtn.addEventListener('click', () => {
    window.ajiEmoji.open(emojiBtn, e => {
      const i = input();
      const at = i.selectionStart == null ? i.value.length : i.selectionStart;
      i.value = i.value.slice(0, at) + e + i.value.slice(i.selectionEnd == null ? at : i.selectionEnd);
      i.focus();
      i.selectionStart = i.selectionEnd = at + e.length;
      resizeInput();
      syncSend();
    });
  });

  /* The composer grows with what is typed, and only scrolls once it has grown
     as far as it is allowed to — about five lines.

     It used to show a scrollbar at the very first character. scrollHeight is the
     content plus its padding and does not include the border, but the box is
     border-box, so assigning it as the height left the text two pixels taller
     than the space it had. Every empty composer on the site had a scrollbar in
     it for the sake of two pixels of border. */
  const COMPOSE_MAX = () => (IS_PAGE ? 160 : 118);

  function resizeInput() {
    const t = input();
    if (!t) return;
    // A box that is not on screen has no height to measure: the conversation
    // pane is display:none until a chat is opened, and asking then produces a
    // two-pixel composer that stays that way until the next keystroke.
    if (!t.offsetParent && t.offsetHeight === 0) return;
    const cs = getComputedStyle(t);
    const border = parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
    t.style.height = 'auto';
    const wanted = t.scrollHeight + border;
    const max = COMPOSE_MAX();
    t.style.height = Math.min(wanted, max) + 'px';
    // No scrollbar until there is genuinely something to scroll. `auto` alone
    // is not enough — sub-pixel line heights leave it flickering in and out on
    // the last line that fits.
    t.style.overflowY = wanted > max ? 'auto' : 'hidden';
  }

  function syncSend() {
    const btn = $('[data-ch-send]');
    if (!btn) return;
    btn.disabled = !((input().value || '').trim() || pending.length);
  }

  /* ══ @mentions ══════════════════════════════════════════════════════════
     Type @ in a group and pick somebody from it. The list is the people in
     this conversation and nobody else — mentioning a colleague who cannot see
     the chat would be a notification about a conversation they are not in, so
     the picker does not offer them and the server would not tell them anyway.

     What goes into the text is @username, which is the Hub's convention
     everywhere else (see core/mentions.py) and what makes the bell, the email
     and the parsing all work without a second implementation. What is *drawn*
     is the person's name — nobody should have to read "ban.haddadin.8043". */

  let mentionBox = null, mentionAt = -1, mentionPick = 0, mentionHits = [];

  function mentionCandidates(term) {
    if (!CHAT.conv || !CHAT.conv.is_group) return [];
    const q = term.toLowerCase();
    return (CHAT.participants || []).filter(p =>
      !q || (p.name || '').toLowerCase().includes(q)
         || (p.username || '').toLowerCase().includes(q)).slice(0, 6);
  }

  /* The @ that starts a mention is one at the very beginning or after a space —
     an address in the middle of a word is an email, not a mention. */
  function mentionToken() {
    const t = input();
    if (!t) return null;
    const upto = t.value.slice(0, t.selectionStart);
    const m = upto.match(/(?:^|\s)@([\w.\-]*)$/);
    return m ? {term: m[1], start: upto.length - m[1].length - 1} : null;
  }

  function closeMentions() {
    if (mentionBox) { mentionBox.remove(); mentionBox = null; }
    mentionAt = -1; mentionHits = [];
  }

  function drawMentions() {
    const tok = mentionToken();
    if (!tok) { closeMentions(); return; }
    mentionHits = mentionCandidates(tok.term);
    if (!mentionHits.length) { closeMentions(); return; }
    mentionAt = tok.start;
    if (!mentionBox) {
      mentionBox = document.createElement('div');
      mentionBox.className = 'ch-mentions';
      mentionBox.setAttribute('data-chat-layer', '');
      document.body.appendChild(mentionBox);
      mentionBox.addEventListener('mousedown', ev => {
        // mousedown, not click: the composer must not lose focus first.
        const row = ev.target.closest('[data-mention]');
        if (!row) return;
        ev.preventDefault();
        insertMention(Number(row.dataset.mention));
      });
    }
    mentionPick = Math.min(mentionPick, mentionHits.length - 1);
    mentionBox.innerHTML = mentionHits.map((p, i) => `
      <button type="button" class="ch-mention${i === mentionPick ? ' on' : ''}"
              data-mention="${i}">
        ${avatarHtml(p, 26)}
        <span><b>${escH(p.name)}</b>${p.role ? `<i>${escH(p.role)}</i>` : ''}</span>
      </button>`).join('');
    const box = input().getBoundingClientRect();
    mentionBox.style.left = Math.round(box.left) + 'px';
    mentionBox.style.width = Math.round(Math.min(box.width, 320)) + 'px';
    mentionBox.style.top =
      Math.round(box.top - mentionBox.offsetHeight - 8) + 'px';
  }

  function insertMention(i) {
    const who = mentionHits[i];
    const t = input();
    if (!who || !t || mentionAt < 0) return;
    const after = t.value.slice(t.selectionStart);
    const before = t.value.slice(0, mentionAt);
    const tag = '@' + who.username + ' ';
    t.value = before + tag + after;
    const caret = before.length + tag.length;
    t.setSelectionRange(caret, caret);
    closeMentions();
    resizeInput();
    syncSend();
    t.focus();
  }

  if (input()) {
    input().addEventListener('input', () => {
      resizeInput(); syncSend(); drawMentions();
    });
    input().addEventListener('blur', () => setTimeout(closeMentions, 120));
    input().addEventListener('keydown', e => {
      if (mentionBox && mentionHits.length) {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          mentionPick = (mentionPick + (e.key === 'ArrowDown' ? 1 : -1)
                         + mentionHits.length) % mentionHits.length;
          drawMentions();
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault(); insertMention(mentionPick); return;
        }
        if (e.key === 'Escape') { e.preventDefault(); closeMentions(); return; }
      }
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
      if (e.key === 'Escape' && (CHAT.replyTo || CHAT.editing)) cancelStrip();
    });
  }

  function readableSize(bytes) {
    return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + ' MB'
                            : Math.max(bytes / 1024, 1).toFixed(0) + ' KB';
  }

  /* ── the attachment tray ──
     A message can carry several files, so what is waiting to be sent is a list
     rather than a single chip. It is held here rather than in the file input's
     own FileList: a FileList is read-only, so removing one of five files would
     mean rebuilding the whole thing through a DataTransfer every time. */
  const MAX_FILES = 10;
  let pending = [];                   // {file, url} — url only for images

  function trayEl() { return $('[data-ch-tray]'); }

  function sameFile(a, b) {
    return a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
  }

  function addFiles(list, opts) {
    const files = [...(list || [])];
    if (!files.length) return;
    let added = 0, skipped = 0;
    for (const f of files) {
      if (pending.length >= MAX_FILES) { skipped++; continue; }
      // Dropping the same folder twice, or pasting twice, should not attach two
      // of everything.
      if (pending.some(p => sameFile(p.file, f))) continue;
      if (!acceptFile(f)) continue;
      pending.push({file: f,
                    url: f.type.indexOf('image/') === 0 ? URL.createObjectURL(f) : null,
                    embed: !!(opts && opts.embed)});
      added++;
    }
    if (skipped)
      alert(`Up to ${MAX_FILES} files on one message. ${skipped} left off.`);
    if (added) drawTray();
    syncSend();
  }

  function removeFile(i) {
    const gone = pending.splice(i, 1)[0];
    if (gone && gone.url) URL.revokeObjectURL(gone.url);
    drawTray();
    syncSend();
  }

  /* `keepUrls` when sending: the bubble that appears immediately is showing
     those object URLs, and revoking one out from under an <img> that has not
     finished decoding leaves a broken picture in the conversation. Send hands
     them on and revokes them once the server's version has replaced the row. */
  function clearFiles(keepUrls) {
    if (!keepUrls) pending.forEach(p => { if (p.url) URL.revokeObjectURL(p.url); });
    pending = [];
    if (fileInput) fileInput.value = '';
    drawTray();
    syncSend();
  }

  function drawTray() {
    const tray = trayEl();
    if (!tray) return;
    tray.hidden = !pending.length;
    if (!pending.length) { tray.innerHTML = ''; return; }
    tray.innerHTML = pending.map((p, i) => {
      const thumb = p.url
        ? `<span class="ch-tile-img"><img src="${p.url}" alt=""></span>`
        : `<span class="ch-tile-ic">${icon('file', 17, 1.7)}</span>`;
      return `<div class="ch-tile${p.url ? ' img' : ''}" title="${escH(p.file.name)}">
                ${thumb}
                <span class="ch-tile-meta">
                  <b>${escH(p.file.name)}</b>
                  <i>${readableSize(p.file.size)}</i>
                </span>
                <button type="button" class="ch-tile-x" data-drop-file="${i}"
                        aria-label="Remove ${escH(p.file.name)}">${icon('x', 12)}</button>
              </div>`;
    }).join('') +
      (pending.length > 1
        ? `<button type="button" class="ch-tile-clear" data-drop-all>Clear all</button>`
        : '');
  }

  function acceptFile(f) {
    if (!f) return false;
    if (!f.size) {
      // A folder dragged in arrives as a zero-byte entry with no type. Saying so
      // beats silently attaching something that cannot be opened.
      alert(`“${f.name || 'That'}” is empty, or is a folder.`);
      return false;
    }
    if (f.size > MAX_BYTES) {
      alert(`“${f.name || 'That file'}” is ${readableSize(f.size)}. The limit is ` +
            `${Math.round(MAX_BYTES / 1048576)} MB.`);
      return false;
    }
    return true;
  }

  if (fileInput) fileInput.addEventListener('change', () => {
    addFiles(fileInput.files);
    fileInput.value = '';             // so picking the same file twice re-fires
  });

  if (trayEl()) trayEl().addEventListener('click', e => {
    if (e.target.closest('[data-drop-all]')) { clearFiles(); return; }
    const x = e.target.closest('[data-drop-file]');
    if (x) removeFile(Number(x.dataset.dropFile));
  });

  window.chatClearAttach = clearFiles;

  /* ── the name of a pasted picture ──
     Ctrl+V used to attach everything as "pasted-2026-08-03-18-10-04.png", which
     is a filename nobody asked for and nobody can search their downloads for.

     What is actually available depends on where the picture came from, so all
     three cases are tried in turn:

       copied in Explorer or Finder → the clipboard carries the real file, and
         its name with it. Use it.
       copied from a web page → no file, but Chrome puts the source markup on
         the clipboard as text/html, and the name is the last part of the img
         src. Recover it from there.
       a screenshot → there is no original. Nothing was ever called anything, so
         the browser's own "image.png" stands, and Django adds a suffix if that
         name is taken.

     Only the last case invents anything, and it invents the least it can. */
  const GENERIC = /^(image|screenshot|clipboard|untitled)(\s*\(\d+\))?\.\w+$/i;

  function nameFromHtml(html) {
    if (!html) return '';
    const m = html.match(/<img[^>]+src\s*=\s*["']([^"']+)["']/i);
    if (!m || m[1].startsWith('data:')) return '';
    try {
      const path = new URL(m[1], location.href).pathname;
      const base = decodeURIComponent(path.split('/').pop() || '');
      return /\.\w{2,5}$/.test(base) ? base : '';
    } catch (err) { return ''; }
  }

  function properName(file, data) {
    if (file.name && !GENERIC.test(file.name)) return file.name;
    const found = nameFromHtml(data.getData && data.getData('text/html'));
    return found || file.name || 'image.png';
  }

  function attachFromPaste(e) {
    if (!CHAT.active) return;
    const data = e.clipboardData;
    if (!data) return;

    let files = data.files && data.files.length ? [...data.files] : [];
    if (!files.length && data.items) {
      files = [...data.items].filter(i => i.kind === 'file')
                             .map(i => i.getAsFile()).filter(Boolean);
    }
    if (!files.length) return;         // plain text: let the textarea have it

    e.preventDefault();
    addFiles(files.map(f => {
      const name = properName(f, data);
      return name === f.name ? f : new File([f], name, {type: f.type});
    }), {embed: true});
    const i = input();
    if (i) i.focus();
  }

  if (input()) input().addEventListener('paste', attachFromPaste);
  // Also when the focus is anywhere in the open conversation, not only in the
  // box — nobody clicks into the message field before pressing Ctrl+V.
  SHELL.addEventListener('paste', e => {
    if (e.target === input()) return;              // already handled above
    if (SHELL.dataset.view !== 'conv') return;
    attachFromPaste(e);
  });

  const sendBtn = $('[data-ch-send]');
  if (sendBtn) sendBtn.addEventListener('click', send);

  function send() {
    const i = input();
    const text = (i.value || '').trim();
    const files = pending.map(p => p.file);
    if (!CHAT.active) return;

    if (CHAT.editing) {
      if (!text) return;
      const m = CHAT.editing;
      post(`/messaging/msg/${m.id}/edit/`, {text}).then(({ok, d}) => {
        if (ok && d.message) {
          Object.assign(m, d.message);
          redrawBubble(m);
          cancelStrip();
          return;
        }
        /* Refused. If the five minutes ran out between pressing send and the
           server reading it, the text stays in the box — the same rule the
           sweep follows — so it can be sent as a new message. Anything else is
           a real failure and is said plainly. */
        CHAT.editing = null;
        if (d && d.expired) { m.can_edit = false; redrawBubble(m); stripNote(d.error); }
        else { cancelStrip(); alert((d && d.error) || 'Could not save that edit.'); }
        syncSend();
      });
      return;
    }

    if (!text && !files.length) return;

    const convAtSend = CHAT.active;
    const replyId = CHAT.replyTo ? CHAT.replyTo.id : '';
    const fd = new FormData();
    fd.append('text', text);
    // Same field name for every file. Django reads them with
    // request.FILES.getlist('attachment'), so one file and nine go the same way.
    files.forEach(f => fd.append('attachment', f));
    if (replyId) fd.append('reply_to', replyId);

    /* The bubble appears at once in a sending state, and the box is only
       cleared after the row exists — so a failure can never lose what was
       typed. */
    const localId = 'tmp' + (++CHAT.tmpSeq);
    const el = msgsEl();
    const emptyState = el.querySelector('.ch-empty');
    if (emptyState) emptyState.remove();
    // The bubble that appears immediately shows the same layout the real one
    // will, using the object URLs already made for the tray — so nothing jumps
    // when the server's version replaces it.
    const preview = attachListHtml(pending.map(p => ({
      url: p.url, name: p.file.name, is_image: !!p.url,
      size: readableSize(p.file.size),
    })));
    const quote = CHAT.replyTo
      ? `<span class="ch-quote"><b>${escH(CHAT.replyTo.sender)}</b><i>${escH((CHAT.replyTo.text || '').slice(0, 90))}</i></span>`
      : '';
    el.insertAdjacentHTML('beforeend',
      `<div class="ch-bubble me sending" id="${localId}">${quote}${preview}` +
      (text ? `<span class="ch-txt">${textHtml(text)}</span>` : '') +
      `<span class="ts">${clock(new Date().toISOString())}<span class="ch-tick">·</span></span></div>`);
    // Instant, for the same reason as pinBottom: you pressed send, the message
    // should be there, not on its way there.
    el.scrollTo({top: el.scrollHeight, behavior: 'instant'});

    i.value = '';
    resizeInput();
    const shownUrls = pending.map(p => p.url).filter(Boolean);
    clearFiles(true);                  // the bubble above is still using them
    cancelStrip();
    const done = () => shownUrls.forEach(u => URL.revokeObjectURL(u));

    post(`/messaging/${convAtSend}/send/`, fd).then(({ok, d}) => {
      const row = document.getElementById(localId);
      if (!row) { done(); return; }
      if (!ok || !d.message) { markFailed(row, text, d && d.error); return; }
      CHAT.msgs.push(d.message);
      CHAT.lastId = Math.max(CHAT.lastId, d.message.id);
      /* Redraw rather than swap the temporary bubble for the real one. Swapping
         skipped the day separator, so the first message in a conversation — and
         the first one after midnight — appeared with no "Today" above it until
         the page was reloaded. */
      row.remove();
      renderMsgs();
      loadConvs();
      done();                          // the real message is on screen now
    }).catch(() => {
      const row = document.getElementById(localId);
      if (row) markFailed(row, text, 'No connection.');
    });
  }

  function markFailed(row, text, reason) {
    row.classList.remove('sending');
    row.classList.add('failed');
    const again = document.createElement('button');
    again.type = 'button';
    again.className = 'ch-retry';
    again.textContent = 'Try again';
    again.addEventListener('click', () => {
      row.remove();
      input().value = text || '';
      resizeInput();
      syncSend();
      send();
    });
    const note = document.createElement('span');
    note.className = 'ch-fail';
    note.textContent = reason || 'Not sent.';
    note.appendChild(again);
    row.appendChild(note);
  }

  /* Drag a file onto the conversation to attach it. */
  const dropZone = $('[data-ch-drop]') || SHELL;
  ['dragenter', 'dragover'].forEach(ev => dropZone.addEventListener(ev, e => {
    if (!CHAT.active) return;
    e.preventDefault();
    dropZone.classList.add('dropping');
  }));
  ['dragleave', 'dragend'].forEach(ev => dropZone.addEventListener(ev, e => {
    if (!dropZone.contains(e.relatedTarget)) dropZone.classList.remove('dropping');
  }));
  dropZone.addEventListener('drop', e => {
    if (!CHAT.active) return;
    e.preventDefault();
    dropZone.classList.remove('dropping');
    // Everything that was dropped, not the first of it. A drop of eight photos
    // used to attach one.
    if (e.dataTransfer.files && e.dataTransfer.files.length)
      addFiles(e.dataTransfer.files, {embed: true});
  });

  /* ══ starting something new ═════════════════════════════════════════════ */

  const newBtn = $('[data-ch-new]');
  if (newBtn) newBtn.addEventListener('click', () => startNew());

  function startNew() {
    setView('new');
    CHAT.groupDraft = [];
    const nameBox = $('[data-ch-groupname]');
    if (nameBox) nameBox.value = '';
    drawDraft();
    peopleSearch('');
    const q = $('[data-ch-peopleq]');
    if (q) { q.value = ''; setTimeout(() => q.focus(), 40); }
  }
  window.chatNew = startNew;

  const peopleQ = $('[data-ch-peopleq]');
  let peopleTimer = null;
  if (peopleQ) peopleQ.addEventListener('input', () => {
    clearTimeout(peopleTimer);
    peopleTimer = setTimeout(() => peopleSearch(peopleQ.value), 180);
  });

  /* Has this person already been picked on whichever screen is asking?
     Both pickers keep a list of chosen people — the new-chat draft and group
     info's basket — and a row that gives no sign of being in one is a row people
     press twice, quietly taking somebody back out again. */
  function chosenAlready(id) {
    if (SHELL.dataset.view === 'info') return INFO.basket.some(p => p.id === id);
    return CHAT.groupDraft.some(p => p.id === id);
  }

  function peopleSearch(q, notIn, target) {
    // The new-chat pane and the group-info pane each have their own list, so
    // the caller says which one it is filling — drawing into the other means
    // results that arrive somewhere the reader cannot see.
    const el = $(target || '[data-ch-people]');
    if (!el) return;
    let url = '/messaging/api/people/?q=' + encodeURIComponent(q || '');
    if (notIn) url += '&not_in=' + notIn;
    fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json())
      .then(d => {
        const users = d.users || [];
        if (!users.length) {
          el.innerHTML = '<div class="ch-empty"><div>Nobody matches that.</div></div>';
          return;
        }
        /* What the poll knows wins over what the search returned, for the same
           reason as in the list: the search may have been in flight while
           somebody picked up a call. Everything the server did send is recorded,
           so the next poll starts from the truth rather than from "offline". */
        users.forEach(u => {
          const at = presenceOf.get(u.id);
          if (at) { u.presence = at.presence; u.presence_label = at.label; }
          else presenceOf.set(u.id, {presence: u.presence, label: u.presence_label});
        });
        el.innerHTML = users.map(u => `
          <div class="ch-row person${chosenAlready(u.id) ? ' picked' : ''}"
               data-person="${u.id}" data-name="${escH(u.name)}"
               tabindex="0" role="button"
               aria-pressed="${chosenAlready(u.id) ? 'true' : 'false'}">
            ${avatarHtml(u, 40)}
            ${dotHtml(u.presence, u.presence_label)}
            <div class="ch-meta">
              <div class="ch-nm">${escH(u.name)}</div>
              <div class="ch-prev" data-role="${escH(u.role || '')}">${
                /* The role, and what they are doing when that is something.
                   Searching for somebody in order to talk to them is the moment
                   it matters whether they are at their desk — and this list said
                   nothing about it at all. The role is kept in an attribute so
                   the poll can rewrite the line without losing it. */
                escH(statusLine(u.role, u.presence, u.presence_label))
              }</div>
            </div>
            <span class="ch-pickmark">${
              icon(chosenAlready(u.id) ? 'check' : 'plus', 14, 2.2)}</span>
          </div>`).join('');
      })
      .catch(() => { el.innerHTML = '<div class="ch-empty"><div>Could not search.</div></div>'; });
  }

  SHELL.addEventListener('click', e => {
    const p = e.target.closest('[data-person]');
    if (!p) return;
    const id = Number(p.dataset.person), name = p.dataset.name;
    /* In group info a press chooses somebody rather than adding them. It used to
       add them there and then, which meant no moment at which you had picked four
       people and could look at the four before committing. */
    if (SHELL.dataset.view === 'info') { basket(id, name); return; }
    if (CHAT.groupDraft.length || (groupMode && groupMode.checked)) {
      toggleDraft(id, name);
      /* Clear the query and show the list again. Leaving the search filled meant
         adding three people took three deletions of somebody else's name, and
         the person just added stayed on screen as though nothing had happened. */
      const q = $('[data-ch-peopleq]');
      if (q) { q.value = ''; peopleSearch(''); q.focus(); }
      return;
    }
    startDirect(id);
  });

  const groupMode = $('[data-ch-groupmode]');
  if (groupMode) groupMode.addEventListener('change', () => {
    SHELL.classList.toggle('group-mode', groupMode.checked);
    if (!groupMode.checked) { CHAT.groupDraft = []; drawDraft(); }
  });

  function toggleDraft(id, name) {
    const at = CHAT.groupDraft.findIndex(p => p.id === id);
    if (at >= 0) CHAT.groupDraft.splice(at, 1);
    else CHAT.groupDraft.push({id, name});
    drawDraft();
  }

  function drawDraft() {
    const el = $('[data-ch-draft]');
    if (!el) return;
    el.innerHTML = CHAT.groupDraft.map(p =>
      `<span class="ch-chip" data-drop-person="${p.id}">${escH(p.name)}` +
      '<button type="button" aria-label="Remove">&times;</button></span>').join('');
    const go = $('[data-ch-groupgo]');
    if (go) go.disabled = !CHAT.groupDraft.length;
    const count = $('[data-ch-draftcount]');
    if (count) count.textContent = CHAT.groupDraft.length
      ? CHAT.groupDraft.length + ' chosen' : '';
  }

  SHELL.addEventListener('click', e => {
    const chip = e.target.closest('[data-drop-person]');
    if (chip && e.target.tagName === 'BUTTON') {
      toggleDraft(Number(chip.dataset.dropPerson), '');
    }
  });

  function startDirect(userId) {
    fetch(`/messaging/with/${userId}/api/`,
          {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json())
      .then(d => loadConvs(() => openConv(d.conv_id)))
      .catch(() => {});
  }

  const groupGo = $('[data-ch-groupgo]');
  if (groupGo) groupGo.addEventListener('click', () => {
    const nameBox = $('[data-ch-groupname]');
    const name = (nameBox.value || '').trim();
    if (!name) { nameBox.focus(); return; }
    const done = busy(groupGo, 'Creating…');
    post('/messaging/groups/new/', {
      name, members: CHAT.groupDraft.map(p => p.id),
    }).then(({ok, d}) => {
      done();
      if (d && d.busy) return;             // a press while the first was running
      if (!ok) { alert(d.error || 'Could not create the group.'); return; }
      CHAT.groupDraft = [];
      if (groupMode) { groupMode.checked = false; SHELL.classList.remove('group-mode'); }
      loadConvs(() => openConv(d.id));
    }).catch(() => { done(); alert('Could not create the group.'); });
  });

  /* ══ the call log ═══════════════════════════════════════════════════════ */

  /* What the telephone did. The conversation already carries a "Voice call ·
     3 min" line where each call happened, which is right for reading a chat and
     useless for the question people actually bring to a phone: did somebody try
     to reach me, and when. That answer is spread across every conversation you
     are in, so it needs a list of its own.

     Full page only — a row here is a direction, an outcome, a date and a length,
     which is four columns the 380-pixel popup does not have. The button that
     opens it exists in the page template and not in the panel one, so this whole
     section simply never runs there. */

  const CALLS = {filter: 'all', conv: null, rows: [], loading: false};

  /* How far the reader has got through the log, kept between visits. The badge
     counts what has happened since; without somewhere to write that down it
     would count every missed call there has ever been and never come down.
     localStorage rather than the server: it holds a reading position for a
     badge, and the cost of losing it is a badge that is briefly wrong about a
     phone call. */
  const SEEN_CALL_KEY = 'aji.chat.seenCall';

  function seenCall() {
    const v = Number(localStorage.getItem(SEEN_CALL_KEY) || 0);
    return Number.isFinite(v) && v > 0 ? v : 0;
  }

  function markCallsSeen(newestId) {
    if (!newestId) return;
    if (newestId > seenCall()) {
      try { localStorage.setItem(SEEN_CALL_KEY, String(newestId)); } catch (e) {}
    }
    badgeMissed(0);
  }

  function openCalls(convId) {
    CALLS.conv = convId || null;
    setView('calls');
    drawCallSub();
    loadCalls();
  }
  window.chatOpenCalls = openCalls;

  const callsBtn = $('[data-ch-calls]');
  if (callsBtn) callsBtn.addEventListener('click', () => openCalls(null));

  SHELL.querySelectorAll('[data-ch-callclose]').forEach(b =>
    b.addEventListener('click', () => {
      // Back to whatever was being read, not to nothing.
      setView(CHAT.active ? 'conv' : 'list');
      renderList(true);
    }));

  SHELL.querySelectorAll('[data-ch-callfilter]').forEach(b =>
    b.addEventListener('click', () => {
      const want = b.dataset.chCallfilter;
      if (CALLS.filter === want) return;
      CALLS.filter = want;
      SHELL.querySelectorAll('[data-ch-callfilter]').forEach(other => {
        const on = other === b;
        other.classList.toggle('on', on);
        other.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      /* Filtered here rather than fetched again. The rows are already in hand and
         each one says whether it was missed, so asking the server the same
         question with one flag changed would be a request to redraw a list that
         has not altered. */
      drawCallSub();
      drawCalls();
    }));

  /* What is being listed, said in the header — because two things narrow this
     list and neither is obvious from the rows themselves. Left saying "every
     call" while the Missed tab was on, it contradicted the control beside it. */
  function drawCallSub() {
    const sub = $('[data-ch-callsub]');
    if (!sub) return;
    const conv = CALLS.conv && (CHAT.convs.find(c => c.id === CALLS.conv)
      || (CHAT.conv && CHAT.conv.id === CALLS.conv ? CHAT.conv : null));
    const what = CALLS.filter === 'missed' ? 'Calls nobody answered' : 'Every call';
    sub.textContent = CALLS.conv
      ? what + ' with ' + (conv ? conv.with_name : 'this chat')
      : what + ', most recent first';
  }

  function loadCalls() {
    const el = $('[data-ch-calllist]');
    if (!el || CALLS.loading) return;
    CALLS.loading = true;
    el.innerHTML = '<div class="ch-skel"><i></i><i></i><i></i><i></i></div>';
    let url = '/messaging/calls/log/';
    if (CALLS.conv) url += '?conv=' + CALLS.conv;
    fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json())
      .then(d => {
        CALLS.rows = d.calls || [];
        drawCalls();
        /* Opening the log is reading it. The badge goes now rather than on the
           next poll, because a count still sitting on the button above a list
           the reader is looking at is the badge saying "there is something you
           have not seen" about the thing on screen. */
        markCallsSeen(d.newest_id || 0);
      })
      .catch(() => {
        el.innerHTML = '<div class="ch-empty"><div>Could not load the call log.</div></div>';
      })
      .finally(() => { CALLS.loading = false; });
  }

  /* How many calls went unanswered, on the button that opens the log. It is the
     only thing in here worth interrupting somebody about: a call they never
     heard is the one event in the chat that leaves nothing on screen. */
  function badgeMissed(n) {
    const b = $('[data-ch-missedcount]');
    if (!b) return;
    b.hidden = !n;
    b.textContent = n > 9 ? '9+' : String(n);
  }

  /* The outcomes, and how each one reads. `mark` overrides the direction arrow;
     `tone` colours that mark and nothing else, because a whole row tinted red is
     an alarm and a missed call is not one. */
  const CALL_OUTCOMES = {
    answered:  {mark: null,           tone: '',       word: ''},
    ongoing:   {mark: 'phone-call',   tone: 'live',   word: 'In progress'},
    missed:    {mark: 'phone-x',      tone: 'missed', word: 'Missed'},
    no_answer: {mark: 'phone-x',      tone: 'missed', word: 'No answer'},
    declined:  {mark: 'phone-x',      tone: 'muted',  word: 'Declined'},
  };

  /* How long it lasted. A clock once there is a minute to show — "4:07" rather
     than "4 min", because the difference between four minutes and four and a
     half is the sort of thing somebody is reading a call log to find out. Under
     a minute it is said in words instead: "0:35" makes a reader work out that
     the nought is minutes, and the line the call left in the conversation
     already says "35 sec". */
  function callLength(secs) {
    const s = Math.max(0, Math.round(secs || 0));
    if (s < 60) return s + ' sec';
    const parts = [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60];
    if (!parts[0]) parts.shift();
    return parts.map((n, i) => i ? String(n).padStart(2, '0') : String(n)).join(':');
  }

  /* The date, at the resolution the reader needs. Today is a time, this week is
     a day and a time, and anything older is a date — because "14:32" is the
     useful part of a call an hour ago and the useless part of one in March. */
  function callWhen(iso) {
    const d = new Date(iso), now = new Date();
    const at = d.toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit'});
    if (d.toDateString() === now.toDateString()) return 'Today, ' + at;
    const y = new Date(now);
    y.setDate(y.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return 'Yesterday, ' + at;
    if ((now - d) / 86400000 < 7)
      return d.toLocaleDateString('en-GB', {weekday: 'long'}) + ', ' + at;
    const sameYear = d.getFullYear() === now.getFullYear();
    return d.toLocaleDateString('en-GB', sameYear
      ? {day: 'numeric', month: 'short'}
      : {day: 'numeric', month: 'short', year: 'numeric'}) + ', ' + at;
  }

  function drawCalls() {
    const el = $('[data-ch-calllist]');
    if (!el) return;
    const rows = CALLS.filter === 'missed'
      ? CALLS.rows.filter(r => r.missed) : CALLS.rows;

    if (!rows.length) {
      el.innerHTML = '<div class="ch-empty">' + icon('phone-call', 32, 1.5) +
        '<div>' + (CALLS.filter === 'missed'
          ? 'No missed calls.'
          : 'No calls yet.<br>Start one from the button in any chat.') +
        '</div></div>';
      return;
    }

    el.innerHTML = rows.map(r => {
      const o = CALL_OUTCOMES[r.outcome] || CALL_OUTCOMES.answered;
      // Which way it went. On a missed call the outcome mark replaces it: two
      // marks side by side on one row is a puzzle, and "missed" is the part
      // somebody is reading the row for.
      const way = o.mark ? icon(o.mark, 15, 1.8)
        : icon(r.direction === 'out' ? 'phone-outgoing' : 'phone-incoming', 15, 1.8);
      const heading = o.word || (r.direction === 'out' ? 'Outgoing' : 'Incoming');
      /* What happened and how long it took goes under the name; when it happened
         goes in a column of its own on the right, the way the conversation list
         already dates its rows.

         All four on one line is what it was, and on a phone that line ran out
         before it reached the length — so the row said a call had come in
         yesterday at 21:02 and stopped before the part anybody was looking for.
         A date has a natural place to live on a row like this, and it is not in
         the middle of a sentence. */
      const bits = [heading];
      if (r.seconds) bits.push(callLength(r.seconds));
      // A group call says who was actually on it, which the group's name does not.
      if (r.is_group && r.people.length) {
        bits.push(r.people.map(p => p.name.split(' ')[0]).slice(0, 3).join(', ')
          + (r.people.length > 3 ? ` +${r.people.length - 3}` : ''));
      }
      return `
      <div class="ch-row call${r.missed ? ' missed' : ''}" data-callrow="${r.conv_id}"
           tabindex="0" role="button">
        <span class="ch-callway t-${o.tone || 'plain'}"
              title="${escH(heading)}">${way}</span>
        ${avatarHtml(r, 34)}
        <div class="ch-meta">
          <div class="ch-nm"><span>${escH(r.with_name)}</span></div>
          <div class="ch-prev">${escH(bits.join(' · '))}</div>
        </div>
        <span class="ch-callwhen" title="${escH(callWhen(r.started_at))}">
          ${escH(relTs(r.started_at))}</span>
        <button type="button" class="ch-callback" data-callback="${r.conv_id}"
                title="Call ${escH(r.with_name)}"
                aria-label="Call ${escH(r.with_name)}">${icon('phone-call', 15, 1.8)}</button>
      </div>`;
    }).join('');
  }

  SHELL.addEventListener('click', e => {
    const back = e.target.closest('[data-callback]');
    if (back) {
      /* Ring them from here. Opening the conversation first is deliberate: a
         call needs somewhere to put its "Voice call · 2 min" line and somewhere
         for the person to be when it connects, and starting one from a list with
         no chat on screen would leave them looking at the log while it rang. */
      e.stopPropagation();
      const convId = Number(back.dataset.callback);
      openConv(convId);
      if (window.AJI_CALL && window.AJI_CALL.start) {
        setTimeout(() => window.AJI_CALL.start(convId), 260);
      }
      return;
    }
    const row = e.target.closest('[data-callrow]');
    if (row) openConv(Number(row.dataset.callrow));
  });
  SHELL.addEventListener('keydown', e => {
    const row = e.target.closest('[data-callrow]');
    if (row && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      openConv(Number(row.dataset.callrow));
    }
  });

  /* ══ group info ═════════════════════════════════════════════════════════ */

  /* Reached from the conversation menu. There used to be a standalone icon in
     the header, shown for every chat because [hidden] loses to the button's own
     display rule — so a one-to-one chat carried a Group info button that could
     not do anything. One menu, and it only lists what applies. */
  /* What the screen is doing: reading the group, or adding to it.

     It used to do both at once — a members list with its own scrollbar, and under
     it a search box with a second scrolling list of everybody at AJi. Two scroll
     areas inside a pane that also scrolled, and a press on a name in the lower one
     added that person immediately. So there was no moment at which you had chosen
     four people and could look at them before committing, and no way to take one
     back except to remove them from the group again afterwards.

     Now: the members are the screen, adding is a thing you go and do, and the
     people you pick sit in a basket until you say Add. */
  const INFO = {adding: false, basket: []};

  function openInfo() {
    if (!CHAT.conv || !CHAT.conv.is_group) return;
    setView('info');
    INFO.adding = false;
    INFO.basket = [];
    drawInfo();
  }

  const convMenuBtn = $('[data-ch-convmenu]');
  if (convMenuBtn) convMenuBtn.addEventListener('click', e => {
    e.stopPropagation();
    if (CHAT.active) rowMenu(convMenuBtn, CHAT.active);
  });

  const infoQ = $('[data-ch-info-q]');
  let infoTimer = null;
  if (infoQ) infoQ.addEventListener('input', () => {
    clearTimeout(infoTimer);
    infoTimer = setTimeout(
      () => peopleSearch(infoQ.value, CHAT.active, '[data-ch-info-people]'), 180);
  });

  function drawInfo() {
    const el = $('[data-ch-info]');
    if (!el || !CHAT.conv) return;
    const conv = CHAT.conv, admin = conv.is_admin;
    const shot = infoPhoto ? infoPhoto.url : conv.photo;

    el.innerHTML =
      /* ── what the group is ── */
      '<div class="ch-gi-top">' +
        `<div class="ch-gi-shot${shot ? ' has' : ''}">` +
          (shot ? `<img src="${escH(shot)}" alt="">`
                : `<span class="ch-gi-ph">${icon('users-three', 30, 1.6)}</span>`) +
          (admin
            /* The camera badge sits on the picture, the way it does on a profile.
               A separate "Change picture" button further down the form would put
               the control somewhere other than the thing it changes. */
            ? '<button type="button" class="ch-gi-shotbtn" data-ch-photo' +
              ' title="Change the group picture"' +
              ' aria-label="Change the group picture">' + icon('camera', 15, 1.7) +
              '</button>' +
              (shot ? '<button type="button" class="ch-gi-shotrm" data-ch-photorm' +
                      ' title="Remove the picture" aria-label="Remove the picture">' +
                      icon('x', 12, 2.2) + '</button>' : '')
            : '') +
        '</div>' +
        '<div class="ch-gi-id">' +
          (admin
            ? `<input type="text" class="ch-gi-name" data-ch-rename maxlength="120"
                 value="${escH(conv.with_name)}" placeholder="Group name"
                 aria-label="Group name">`
            : `<div class="ch-gi-name ro">${escH(conv.with_name)}</div>`) +
          (admin
            ? `<textarea class="ch-gi-about" data-ch-about rows="2" maxlength="280"
                 placeholder="What is this group for? (optional)"
                 aria-label="Group description">${escH(conv.description || '')}</textarea>`
            : (conv.description
                ? `<div class="ch-gi-about ro">${escH(conv.description)}</div>` : '')) +
          `<div class="ch-gi-count">${conv.members} member${conv.members === 1 ? '' : 's'}</div>` +
        '</div>' +
      '</div>' +
      (admin ? '<div class="ch-gi-save">' +
        '<button type="button" class="btn y sm" data-ch-saverename>Save changes</button>' +
        '<span class="ch-gi-hint" data-ch-savehint></span></div>' : '') +

      /* ── who is in it ── */
      '<div class="ch-gi-sec">' +
        '<span>Members</span>' +
        (admin && !INFO.adding
          ? '<button type="button" class="ch-gi-add" data-ch-addopen>' +
            icon('plus', 13, 2.2) + 'Add people</button>'
          : '') +
      '</div>' +
      '<div class="ch-gi-people">' +
      CHAT.participants.map(p => `
        <div class="ch-row static" data-member="${p.id}">
          ${avatarHtml(p, 34)}
          <div class="ch-meta"><div class="ch-nm"><span>${escH(p.name)}</span></div>
            <div class="ch-prev">${escH(p.role || '')}</div></div>
          ${admin ? `<button type="button" class="ch-gi-x" data-kick="${p.id}"
                       title="Remove ${escH(p.name)} from the group"
                       aria-label="Remove ${escH(p.name)} from the group">${icon('x', 14, 2.2)}</button>` : ''}
        </div>`).join('') +
      '</div>' +

      '<div class="ch-gi-foot">' +
        '<button type="button" class="btn ghost sm danger" data-ch-leave>Leave group</button>' +
      '</div>';

    drawAddPanel();
  }

  /* The add-people panel: shown only while adding, and the basket lives in it so
     that what you have chosen and the button that commits it are the same thing
     you are looking at. */
  function drawAddPanel() {
    const pane = $('[data-ch-info-add]');
    if (!pane) return;
    const on = INFO.adding && CHAT.conv && CHAT.conv.is_admin;
    pane.hidden = !on;
    const basket = $('[data-ch-basket]');
    if (basket) {
      basket.innerHTML = INFO.basket.length
        ? INFO.basket.map(p =>
            `<span class="ch-chip" data-unbasket="${p.id}">${escH(p.name)}` +
            `<button type="button" aria-label="Take ${escH(p.name)} back off the list">&times;</button></span>`).join('')
        : '<span class="ch-basket-empty">Nobody chosen yet. '
          + 'Search above and press a name to add them to this list.</span>';
    }
    const go = $('[data-ch-basketgo]');
    if (go) {
      go.disabled = !INFO.basket.length;
      go.textContent = INFO.basket.length
        ? `Add ${INFO.basket.length} to group` : 'Add to group';
    }
    if (!on) return;
    // Redrawn every time the basket changes, so the results have to be redrawn
    // too — a name already in the basket must not still offer a plus.
    peopleSearch(($('[data-ch-info-q]') || {}).value || '', CHAT.active,
                 '[data-ch-info-people]');
  }

  SHELL.addEventListener('click', e => {
    const save = e.target.closest('[data-ch-saverename]');
    if (save) { saveGroup(save); return; }

    if (e.target.closest('[data-ch-addopen]')) {
      INFO.adding = true;
      INFO.basket = [];
      drawInfo();
      const q = $('[data-ch-info-q]');
      if (q) { q.value = ''; setTimeout(() => q.focus(), 40); }
      return;
    }
    if (e.target.closest('[data-ch-addclose]')) {
      /* Backing out discards the basket. Keeping it would leave a set of people
         half-added — chosen, invisible, and waiting to surprise somebody the next
         time this screen was opened. */
      INFO.adding = false;
      INFO.basket = [];
      drawInfo();
      return;
    }
    const off = e.target.closest('[data-unbasket]');
    if (off) { basket(Number(off.dataset.unbasket)); return; }

    const go = e.target.closest('[data-ch-basketgo]');
    if (go) { commitBasket(go); return; }

    if (e.target.closest('[data-ch-photo]')) { pickGroupPhoto(); return; }
    if (e.target.closest('[data-ch-photorm]')) {
      /* Marked, not done. The picture goes when Save goes, with everything else on
         the form — having it disappear from the server the moment × is pressed,
         while the name in the box beside it is still unsaved, would be one screen
         making two different promises. */
      if (infoPhoto) { URL.revokeObjectURL(infoPhoto.url); infoPhoto = null; }
      CHAT.conv = Object.assign({}, CHAT.conv, {photo: ''});
      infoPhotoCleared = true;
      drawInfo();
      hintSave('The picture will be removed when you save.');
      return;
    }

    const kick = e.target.closest('[data-kick]');
    if (kick) {
      const id = Number(kick.dataset.kick);
      const who = CHAT.participants.find(p => p.id === id);
      if (!window.confirm('Remove ' + (who ? who.name : 'this person') + ' from the group?')) return;
      post(`/messaging/${CHAT.active}/group/`, {action: 'remove', user: id})
        .then(({ok, d}) => {
          if (!ok) { alert(d.error || 'Could not remove.'); return; }
          CHAT.participants = CHAT.participants.filter(p => p.id !== id);
          CHAT.conv = d.conversation;
          drawInfo();
          drawHeader(d.conversation);
        });
      return;
    }
    if (e.target.closest('[data-ch-leave]')) {
      if (!window.confirm('Leave this group? You stop receiving its messages.')) return;
      post(`/messaging/${CHAT.active}/group/`, {action: 'leave'}).then(() => {
        CHAT.active = null;
        setView('list');
        loadConvs();
      });
    }
  });

  /* Put somebody in the basket, or take them out again. Pressing a name twice is
     undo — the same gesture both ways, which is how the group draft on the
     new-chat screen already works. */
  function basket(userId, name) {
    const at = INFO.basket.findIndex(p => p.id === userId);
    if (at >= 0) INFO.basket.splice(at, 1);
    else if (name) INFO.basket.push({id: userId, name});
    else return;
    drawAddPanel();
  }

  function commitBasket(btn) {
    if (!INFO.basket.length) return;
    const done = busy(btn, 'Adding…');
    post(`/messaging/${CHAT.active}/group/`,
         {action: 'add', members: INFO.basket.map(p => p.id)})
      .then(({ok, d}) => {
        done();
        if (d && d.busy) return;
        if (!ok) { alert(d.error || 'Could not add them.'); return; }
        INFO.adding = false;
        INFO.basket = [];
        /* Re-read the conversation rather than patch three lists by hand: the
           people added bring a system message with them, and the member count,
           the member list and the message log all have to agree about it. */
        fetch(`/messaging/${CHAT.active}/api/messages/`,
              {headers: {'X-Requested-With': 'XMLHttpRequest'}})
          .then(r => r.json()).then(x => {
            CHAT.conv = x.conversation;
            CHAT.participants = x.participants || [];
            CHAT.msgs = x.messages || [];
            renderMsgs();
            drawHeader(x.conversation);
            drawInfo();
            loadConvs();
          });
      })
      .catch(() => { done(); alert('Could not add them.'); });
  }

  /* ── the group's picture ── */

  /* Chosen and cropped, but not yet uploaded: Save sends it with the name and the
     description in one request. Leaving the screen throws it away, which is what
     "not yet" should mean. */
  let infoPhoto = null;               // {blob, url, name}
  let infoPhotoCleared = false;       // × pressed, waiting for Save

  function pickGroupPhoto() {
    let input = $('[data-ch-groupphoto]');
    if (!input) {
      /* Built once and kept. A file input recreated on every press loses the
         "same file again" case: the browser fires no change event when the value
         has not altered, so re-choosing the picture you just cancelled out of
         would do nothing at all. */
      input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.hidden = true;
      input.setAttribute('data-ch-groupphoto', '');
      SHELL.appendChild(input);
      input.addEventListener('change', () => {
        const file = input.files && input.files[0];
        input.value = '';
        if (!file) return;
        if (!window.ajiCrop) { alert('The picture editor could not be loaded.'); return; }
        /* Square, framed as a circle: a group picture is drawn in a circle
           everywhere it appears, so what you position is what you get. 512 is
           comfortably more than the 64 pixels it is ever shown at, and small
           enough that a group photo is not a two-megabyte upload. */
        window.ajiCrop.open(file, {size: 512, round: true,
                                   title: 'Position the group picture'})
          .then(blob => {
            if (!blob) return;
            if (infoPhoto) URL.revokeObjectURL(infoPhoto.url);
            infoPhoto = {
              blob, url: URL.createObjectURL(blob),
              // Cropped output is always JPEG, so the name has to say so —
              // otherwise a .png upload arrives claiming to be something it is not.
              name: (file.name || 'group').replace(/\.[^.]+$/, '') + '.jpg',
            };
            infoPhotoCleared = false;
            drawInfo();
            hintSave('New picture ready — press Save changes.');
          });
      });
    }
    input.click();
  }

  function hintSave(text) {
    const hint = $('[data-ch-savehint]');
    if (hint) hint.textContent = text || '';
  }

  /* One request for the whole form: the name, what the group is for, and the
     picture if one was chosen. Three saves would be three chances for half an
     edit to land, and the reader pressed one button. */
  function saveGroup(btn) {
    const nameBox = $('[data-ch-rename]');
    const aboutBox = $('[data-ch-about]');
    if (!nameBox) return;
    const name = (nameBox.value || '').trim();
    if (!name) { nameBox.focus(); hintSave('A group needs a name.'); return; }

    const fd = new FormData();
    fd.append('action', 'rename');
    fd.append('name', name);
    fd.append('description', aboutBox ? (aboutBox.value || '').trim() : '');
    if (infoPhoto) fd.append('photo', infoPhoto.blob, infoPhoto.name);
    else if (infoPhotoCleared) fd.append('photo_clear', '1');

    const done = busy(btn, 'Saving…');
    hintSave('');
    post(`/messaging/${CHAT.active}/group/`, fd).then(({ok, d}) => {
      done();
      if (!ok) { alert((d && d.error) || 'Could not save.'); return; }
      if (infoPhoto) { URL.revokeObjectURL(infoPhoto.url); infoPhoto = null; }
      infoPhotoCleared = false;
      CHAT.conv = d.conversation;
      drawHeader(d.conversation);
      drawInfo();
      loadConvs();
      hintSave('Saved.');
      setTimeout(() => hintSave(''), 2600);
    }).catch(() => { done(); alert('Could not save.'); });
  }

  /* ══ navigation between views ═══════════════════════════════════════════ */

  SHELL.querySelectorAll('[data-ch-back]').forEach(b =>
    b.addEventListener('click', () => {
      if (SHELL.dataset.view === 'info') { setView('conv'); return; }
      CHAT.active = IS_PAGE ? CHAT.active : null;
      setView('list');
      renderList(true);
    }));

  /* Close the open conversation. On the full page the Back button is hidden —
     the list is already on screen — which left no way at all to put a chat down
     and go back to nothing. */
  SHELL.querySelectorAll('[data-ch-close]').forEach(b =>
    b.addEventListener('click', () => {
      CHAT.active = null;
      CHAT.conv = null;
      CHAT.msgs = [];
      CHAT.replyTo = null;
      CHAT.editing = null;
      drawReplyStrip();
      setView('list');
      renderList(true);
      if (IS_PAGE && history.replaceState) history.replaceState(null, '', '/messaging/');
    }));

  /* ══ the popup, and the button that grows it into a page ════════════════ */

  window.chatToggle = function () {
    if (IS_PAGE) return;
    CHAT.open = !CHAT.open;
    SHELL.classList.toggle('open', CHAT.open);
    schedulePoll();
    if (CHAT.open) {
      window.__panelOpenedAt = Date.now();
      // Both corner panels sit at the same place and z-index, so only one of
      // them may be open at a time.
      if (typeof askClosePanel === 'function') askClosePanel();
      loadConvs();
    }
  };

  window.chatClosePanel = function () {
    if (IS_PAGE || !CHAT.open) return;
    CHAT.open = false;
    SHELL.classList.remove('open');
    schedulePoll();
  };

  window.chatOpenWith = function (userId, name, photo) {
    if (IS_PAGE) { startDirect(userId); return; }
    if (!CHAT.open) window.chatToggle();
    startDirect(userId);
  };

  /* querySelectorAll, not querySelector. There are two of these — one on the
     list header and one on the conversation header — and binding only the first
     left the one people actually reach for, inside an open chat, doing nothing. */
  SHELL.querySelectorAll('[data-ch-expand]').forEach(b =>
    b.addEventListener('click', () => {
      window.location = CHAT.active ? '/messaging/?c=' + CHAT.active : '/messaging/';
    }));

  /* ══ polling ════════════════════════════════════════════════════════════ */

  /* 4s while the chat is in front, 7s when the popup is shut, 15s while the tab
     is hidden, with a catch-up the moment it comes back. It used to be a flat 4s
     forever, on every page, for every signed-in user.
     The last two are what they are because this poll now also carries the
     telephone — see the `call` block it hands to chat_calls.js. A ring nobody
     hears for twenty seconds is a missed call, and a tab that stops polling
     while it is in the background cannot ring at all, which is precisely the
     tab most people leave the Hub in. Browsers throttle timers in hidden tabs,
     so 15s there is a request, not a promise. */
  const FAST = 4000, IDLE = 7000, HIDDEN = 15000;
  let pollTimer = null, pollEvery = null;

  function schedulePoll() {
    const want = document.hidden ? HIDDEN : ((IS_PAGE || CHAT.open) ? FAST : IDLE);
    if (want === pollEvery) return;
    pollEvery = want;
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (want) pollTimer = setInterval(poll, want);
  }
  window.chatSchedulePoll = schedulePoll;

  /* Redraw a conversation somebody else changed. Used by the call layer when a
     call ends: the "Voice call · 3 min" line is written under the caller's own
     name, and the poll deliberately ignores messages from you — so without this
     the person who made the call is the one who cannot see that it happened. */
  window.chatRefreshConv = function (convId) {
    if (convId && (IS_PAGE || CHAT.open) && CHAT.active === convId) openConv(convId);
    loadConvs();
  };

  /* The first poll of a page load only takes a reading. It runs before the
     conversation has arrived, so CHAT.lastId is still 0 and the server rightly
     answers with every message there is — treating those as newly arrived meant
     reloading the list for nothing and, with a chat open, re-handling messages
     already on screen. */
  let polledOnce = false;

  function poll() {
    let url = '/messaging/api/poll/?after=' + CHAT.lastId;
    if (CHAT.active && (IS_PAGE || CHAT.open)) url += '&conv=' + CHAT.active;
    /* Only while somebody is looking. A shut popup has no dots on screen, and
       asking who is online in order to draw nothing would put a query on every
       signed-in person's poll all day for nothing. */
    if (IS_PAGE || CHAT.open) {
      const watch = watchedIds();
      if (watch.length) url += '&watch=' + watch.join(',');
      /* Only on the page, and only once the reader has opened the log at least
         once. Before that there is no reading position, so there is nothing for a
         badge to count "since" — every old missed call would arrive at once as
         news. The popup has no log and no badge, so it does not ask. */
      const seen = IS_PAGE ? seenCall() : 0;
      if (seen) url += '&seen_call=' + seen;
    }
    fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json())
      .then(d => {
        setBadge(d.total_unread || 0);
        applySeen(d.seen_ids);
        applyTouched(d.touched, d.touched_from);
        applyPresence(d.presence);
        // Absent when the reader has never opened the log, which is not zero —
        // it is "no reading position", and writing zero would be a badge that
        // clears itself the moment a call is missed.
        if (typeof d.missed_calls === 'number'
            && SHELL.dataset.view !== 'calls') badgeMissed(d.missed_calls);

        /* The telephone rides on this request. Calls are their own file and
           know nothing about the chat; all they need is to be told, and giving
           them a timer of their own would be a second recurring request for
           every signed-in person to say "nobody is calling you" all day. */
        if (window.AJI_CALL) window.AJI_CALL.ring(d.call || null);

        const fresh = d.new_messages || [];
        if (!polledOnce) {
          polledOnce = true;
          if (fresh.length) CHAT.lastId = Math.max(CHAT.lastId, ...fresh.map(m => m.id));
          return;
        }
        if (!fresh.length) { refreshListIfStale(); return; }
        CHAT.lastId = Math.max(CHAT.lastId, ...fresh.map(m => m.id));

        let forMe = null;
        fresh.forEach(m => {
          if ((IS_PAGE || CHAT.open) && CHAT.active === m.conv_id) {
            if (!CHAT.msgs.some(x => x.id === m.id)) {
              const el = msgsEl();
              const stuck = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
              const prev = CHAT.msgs[CHAT.msgs.length - 1];
              CHAT.msgs.push(m);
              // A message that starts a new day needs its separator, and the
              // first message in an empty conversation needs the whole thing
              // drawn — appending a lone bubble would skip both.
              const newDay = !prev || new Date(prev.sent_at).toDateString()
                                      !== new Date(m.sent_at).toDateString();
              if (newDay) {
                renderMsgs();
              } else {
                const wrap = document.createElement('div');
                wrap.innerHTML = bubbleHtml(m, prev, false);
                el.appendChild(wrap.firstElementChild);
                if (stuck) el.scrollTo({top: el.scrollHeight, behavior: 'instant'});
              }
              // Landing in the chat you are already looking at.
              maybeCelebrate([m]);
            }
            // Reading it is what marks it read, so say so rather than relying on
            // a re-fetch of the whole conversation to do it as a side effect.
            post(`/messaging/${m.conv_id}/read/`).catch(() => {});
          } else {
            forMe = m;
          }
        });
        /* Every new message changes the list, wherever it landed.
           This used to be set only in the branch above — the one for messages
           belonging to a conversation you are *not* looking at. So a message
           arriving in the open chat was drawn in the thread and the row for
           that same chat kept the previous message and the previous time, until
           something else happened to reload the list or the page was reloaded.
           The one conversation guaranteed to be out of date was the one being
           read. */
        loadConvs();
        if (forMe) notify(forMe);
      })
      .catch(() => {});
  }

  function applySeen(ids) {
    if (!ids || !ids.length) return;
    ids.forEach(id => {
      const m = CHAT.msgs.find(x => x.id === id);
      if (m && !m.seen) {
        m.seen = true;
        const node = msgsEl().querySelector(`[data-mid="${id}"] .ch-tick`);
        if (node) { node.textContent = '✓✓'; node.classList.add('seen'); }
      }
    });
  }

  function applyTouched(touched, from) {
    if (!touched) return;
    const seen = {};
    touched.forEach(t => {
      seen[t.id] = true;
      const m = CHAT.msgs.find(x => x.id === t.id);
      if (!m) return;
      const same = JSON.stringify(m.reactions || []) === JSON.stringify(t.reactions || [])
                && (m.text || '') === (t.text || '')
                && (m.edited_at || null) === (t.edited_at || null);
      if (same) return;
      m.reactions = t.reactions;
      m.text = t.text;
      m.edited_at = t.edited_at;
      redrawBubble(m);
    });
    /* A reaction somebody took back stops appearing in `touched`, so anything we
       are still drawing reactions for that is no longer listed has lost them.
       Only within the window the server looked at, though — outside it, absence
       means "not examined", not "removed". */
    if (from == null) return;
    CHAT.msgs.forEach(m => {
      if (m.id >= from && !seen[m.id] && m.reactions && m.reactions.length) {
        m.reactions = [];
        redrawBubble(m);
      }
    });
  }

  /* The title flashes for everybody; the desktop pop-up only for people who
     asked for one, which the shell carries from their settings. */
  const POPUP_OK = SHELL.dataset.popup === '1';

  /* No title flashing. The count in the tab is kept by setBadge, which is the
     one place that knows how many are unread. */
  function notify(m) {
    if (POPUP_OK && window.Notification && Notification.permission === 'granted') {
      const note = new Notification('AJi Hub — ' + m.sender, {
        body: m.text || 'Sent an attachment',
        icon: '/static/img/logo.png',
        tag: 'aji-chat-' + m.conv_id,       // one per conversation, not per message
      });
      note.onclick = () => {
        window.focus();
        if (IS_PAGE) openConv(m.conv_id);
        else { if (!CHAT.open) window.chatToggle(); openConv(m.conv_id); }
        note.close();
      };
    }
  }

  /* Permission is asked for on the notification settings page, with a button and
     an explanation. It used to be requested silently on the first click anywhere
     on the Hub, which is how you train people to press Block. */

  document.addEventListener('visibilitychange', () => {
    schedulePoll();
    if (!document.hidden) poll();
  });

  /* ══ start ══════════════════════════════════════════════════════════════ */

  /* The header's height, published as --nav-h. Measured rather than guessed with
     a magic number, because it depends on the logo the branding record holds.

     Both surfaces need it. The full page is the screen minus the header; and the
     popup has to stop short of it — capped only against the viewport, on a short
     window the panel grew tall enough to ride up over the navigation, which is
     what made it look as though it had been flung at the top of the screen. */
  const setNavHeight = () => {
    const nav = document.querySelector('.topnav');
    document.documentElement.style.setProperty(
      '--nav-h', Math.round(nav ? nav.getBoundingClientRect().height : 68) + 'px');
  };
  setNavHeight();
  window.addEventListener('resize', setNavHeight);
  if (window.ResizeObserver) {
    const nav = document.querySelector('.topnav');
    if (nav) new ResizeObserver(setNavHeight).observe(nav);
  }

  setView('list');
  schedulePoll();

  /* Both requests at once. Waiting for the list before asking for the messages
     made opening a chat from a link two round trips deep — the conversation
     could not start drawing until a list nobody was looking at yet had arrived. */
  const preset = SHELL.dataset.openId;
  if (preset) openConv(Number(preset));
  loadConvs(() => {
    if (!preset && SHELL.dataset.startNew === '1') startNew();
  });
  poll();
})();
