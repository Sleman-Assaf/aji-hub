/* static/js/birthday_wish.js - one way to send a birthday wish.
   Loaded by templates/includes/birthday_wish_js.html, which several blocks
   include; the browser fetches this once and the guard below stops a second
   script tag wiring everything up twice. */
if (!window.bdWish) {
(function () {
  var EMOJI = ['🎂','🎉','🎊','🥳','🎈','🌟','💐','🎁','🤩','✨'];
  var PRESETS = [
    'Have a wonderful day 🎂',
    'Hope the year ahead is a brilliant one 🥳',
    'Many happy returns — enjoy your day 🎉'
  ];
  var dlg = null, target = null;

  function csrf() {
    var m = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
    return m ? m[2] : '';
  }

  function build() {
    dlg = document.createElement('div');
    dlg.className = 'bdw';
    dlg.hidden = true;
    dlg.innerHTML =
      '<div class="bdw-box" role="dialog" aria-modal="true" aria-labelledby="bdwWho">' +
        '<button type="button" class="bdw-x" data-close aria-label="Close">&times;</button>' +
        '<div class="bdw-cake">🎂</div>' +
        '<div class="bdw-who" id="bdwWho"></div>' +
        '<div class="bdw-sub">Sent as a direct message on AJi Messenger.</div>' +
        '<div class="bdw-emoji">' +
          EMOJI.map(function (e) {
            return '<button type="button" class="bdw-e" data-emoji="' + e + '">' + e + '</button>';
          }).join('') +
        '</div>' +
        '<textarea class="bdw-msg" rows="3" placeholder="Write your birthday message…"></textarea>' +
        '<div class="bdw-presets">' +
          PRESETS.map(function (p, i) {
            return '<button type="button" class="bdw-p" data-preset="' + i + '">' +
                   p.replace(/\s*[^\w\s,!—-]+$/, '') + '</button>';
          }).join('') +
        '</div>' +
        '<div class="bdw-act">' +
          '<button type="button" class="btn ghost" data-close>Cancel</button>' +
          '<button type="button" class="btn y" data-send>Send</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(dlg);

    var msg = dlg.querySelector('.bdw-msg');

    dlg.addEventListener('click', function (e) {
      if (e.target === dlg || e.target.closest('[data-close]')) { close(); return; }
      var em = e.target.closest('[data-emoji]');
      if (em) { insert(em.dataset.emoji); return; }
      var pr = e.target.closest('[data-preset]');
      if (pr) { msg.value = greet(PRESETS[+pr.dataset.preset]); msg.focus(); return; }
      if (e.target.closest('[data-send]')) send();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && dlg && !dlg.hidden) close();
    });
  }

  /* The emoji goes where the caret is, not always on the end — somebody part
     way through a sentence means it there. */
  function insert(ch) {
    var msg = dlg.querySelector('.bdw-msg');
    var a = msg.selectionStart, b = msg.selectionEnd;
    msg.value = msg.value.slice(0, a) + ch + msg.value.slice(b);
    msg.selectionStart = msg.selectionEnd = a + ch.length;
    msg.focus();
  }

  /* Addressed to the person by their first name, and from whoever is sending
     it rather than on behalf of the company. */
  function greet(rest) {
    var first = (target && target.name || '').trim().split(/\s+/)[0] || 'there';
    return 'Happy birthday, ' + first + '! ' + rest;
  }

  function close() {
    if (!dlg) return;
    dlg.hidden = true;
    target = null;
  }

  function send() {
    var btn = dlg.querySelector('[data-send]');
    var msg = dlg.querySelector('.bdw-msg');
    var text = msg.value.trim();
    if (!text || !target) return;

    btn.disabled = true;
    btn.textContent = 'Sending…';

    /* /messaging/with/<user>/api/ answers with conv_id, then the message goes to
       /messaging/<conv>/send/. kind=birthday is what the chat window reads to
       set off the burst for whoever receives it. */
    fetch('/messaging/with/' + target.user + '/api/', {
      method: 'POST', headers: {'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest'}
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.conv_id) throw new Error('no conversation');
        return fetch('/messaging/' + d.conv_id + '/send/', {
          method: 'POST',
          headers: {'X-CSRFToken': csrf(),
                    'Content-Type': 'application/x-www-form-urlencoded'},
          body: 'kind=birthday&text=' + encodeURIComponent(text)
        });
      })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function () {
        var who = target;
        btn.textContent = 'Sent ✓';
        setTimeout(function () {
          close();
          btn.disabled = false;
          btn.textContent = 'Send';
          /* Mark the button that opened this, so it is obvious at a glance who
             has already been wished — the list stays on screen afterwards. */
          document.querySelectorAll('[data-user="' + who.user + '"]').forEach(function (b) {
            if (b.tagName !== 'BUTTON') return;
            b.disabled = true;
            b.textContent = 'Sent ✓';
            b.classList.add('is-sent');
          });
          if (typeof toast === 'function') toast('Birthday wish sent');
          else if (typeof showToast === 'function') showToast('Birthday wish sent');
        }, 700);
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = 'Send';
        var t = typeof toast === 'function' ? toast
              : typeof showToast === 'function' ? showToast : null;
        if (t) t('Could not send — try AJi Messenger directly');
      });
  }

  /* The one entry point. Both the header strip and the birthdays block call it,
     so the wish is editable wherever it is sent from. */
  window.bdWish = function (userId, name) {
    if (!dlg) build();
    target = {user: userId, name: name || ''};
    dlg.querySelector('.bdw-who').textContent = name || 'Birthday wishes';
    dlg.querySelector('.bdw-msg').value = greet(PRESETS[0]);
    dlg.hidden = false;
    var msg = dlg.querySelector('.bdw-msg');
    setTimeout(function () { msg.focus(); msg.setSelectionRange(msg.value.length, msg.value.length); }, 60);
  };

  /* Any button carrying data-user opens it, so a template only has to render
     the button — no per-page wiring, and it keeps working for rows that arrive
     after the page was built. */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.bd-send[data-user]');
    if (!btn || btn.disabled) return;
    e.preventDefault();
    e.stopPropagation();
    var row = btn.closest('[data-name]');
    window.bdWish(btn.dataset.user, row ? row.dataset.name : '');
  });
})();
}
