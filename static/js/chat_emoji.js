/* chat_emoji.js
   The emoji picker, embedded.
   No CDN and no font download: the Hub runs on an intranet and a strict CSP,
   so an emoji library fetched from the web would simply not load. This is a
   curated set with search keywords, drawn with the system emoji font — which
   is what every other chat app on these machines uses anyway.

   Served as a static file rather than inlined into every page: the chat
   page was sending 174 KB of uncacheable <script> on every navigation.
   Edit this file, not a template. */

(function () {
  'use strict';

  /* group -> "emoji keyword keyword|emoji keyword…". Keywords are what people
     actually type: "thumbs" and "ok" both have to find 👍. */
  const GROUPS = [
    ['Smileys', 'smiley',
     '😀 grin happy smile|😃 smile happy|😄 laugh happy|😁 beam grin|😅 sweat laugh nervous|' +
     '😂 lol laugh cry funny|🤣 rofl laugh funny|🙂 slight smile|😉 wink|😊 blush smile happy|' +
     '😍 love heart eyes|😘 kiss|😗 kiss|🤗 hug|🤩 star struck wow|🤔 think hmm|🤨 raised brow doubt|' +
     '😐 neutral meh|😑 expressionless|🙄 eye roll|😏 smirk|😴 sleep tired|😪 sleepy tired|' +
     '😥 sad|😢 cry sad|😭 sob cry sad|😤 huff|😠 angry mad|😡 rage angry mad|🤯 mind blown|' +
     '😳 flushed embarrassed|🥵 hot sweat|🥶 cold freeze|😬 grimace awkward|🤐 zip quiet|' +
     '🤫 shh quiet|🤥 lie|😷 mask sick|🤒 sick ill|🤕 hurt injured|🥳 party celebrate|' +
     '🥺 pleading please|😇 innocent halo|🤠 cowboy|🤓 nerd glasses|😎 cool sunglasses|' +
     '🙃 upside down|😜 tongue silly|🤪 zany silly|😲 shock surprised|😮 wow surprised open'],

    ['People', 'hand',
     '👍 thumbs up yes good ok like|👎 thumbs down no bad|👌 ok perfect|✌️ peace victory|' +
     '🤞 fingers crossed luck|🤙 call shaka|👏 clap applause bravo|🙌 raise hands praise|' +
     '🙏 pray thanks please|🤝 handshake deal agree|💪 muscle strong|👊 fist bump|✊ fist|' +
     '👋 wave hi hello bye|🤚 hand stop|✋ hand high five|👆 point up|👇 point down|' +
     '👉 point right|👈 point left|☝️ point up one|✍️ write sign|💅 nails|🫡 salute yes sir|' +
     '🤦 facepalm|🤷 shrug dunno|🙋 raise hand ask|🙆 ok gesture|🙅 no gesture stop|' +
     '💁 info desk|🧑‍💻 developer coder|👷 engineer builder site|👨‍🔧 mechanic|👩‍🏫 teacher|' +
     '🕵️ detective search|👮 police|🧠 brain smart|👀 eyes look see|🗣️ speak shout'],

    ['Work', 'briefcase',
     '📁 folder file|📂 folder open|📄 document page paper|📃 page|📑 tabs bookmark|' +
     '📊 chart bar graph data|📈 chart up growth increase|📉 chart down decrease|' +
     '📋 clipboard list|📌 pin|📍 location pin place|📎 clip attach|🖇️ clips|' +
     '📏 ruler measure|📐 triangle ruler set square|✏️ pencil write|🖊️ pen|🖋️ fountain pen|' +
     '🖌️ brush paint|🗂️ dividers|🗃️ card box|🗄️ cabinet file|🗑️ bin trash delete|' +
     '📅 calendar date|📆 calendar|⏰ alarm clock time|⏱️ stopwatch timer|⌛ hourglass wait|' +
     '💼 briefcase work business|🏢 office building|🏗️ construction crane site|🏠 house home|' +
     '🔨 hammer tool|🔧 spanner wrench tool|🪛 screwdriver|⚙️ gear settings cog|🧰 toolbox|' +
     '🪜 ladder|🚧 works barrier|🦺 vest ppe safety|⛑️ helmet hard hat safety|' +
     '💡 idea bulb light|🔑 key access|🔒 lock private secure|🔓 unlock open|🔍 search find|' +
     '💻 laptop computer|🖥️ desktop monitor|🖨️ printer|📱 phone mobile|☎️ telephone|' +
     '📧 email mail|✉️ envelope mail|📤 outbox send|📥 inbox receive|🗓️ planner|' +
     '💰 money cash budget|💵 money dollar|🧾 receipt invoice|🏦 bank|📝 memo note write'],

    ['Signs', 'check',
     '✅ tick check done yes ok approved|☑️ ticked box done|✔️ tick check|❌ cross no wrong reject|' +
     '❎ cross|⭕ circle|⚠️ warning caution careful|❗ exclamation important|❓ question help|' +
     '‼️ double exclamation urgent|💯 hundred perfect|🔥 fire hot great|⭐ star favourite|' +
     '🌟 glow star|✨ sparkles new nice|💥 boom|🎯 target goal bullseye|🏆 trophy win award|' +
     '🥇 gold first|🎉 party celebrate congrats|🎊 confetti celebrate|🎁 gift present|' +
     '❤️ heart love red|🧡 orange heart|💛 yellow heart|💚 green heart|💙 blue heart|' +
     '💜 purple heart|🖤 black heart|🤍 white heart|💔 broken heart|👑 crown king|' +
     '🔔 bell notify|🔕 mute silent|🔗 link chain|♻️ recycle|🆗 ok|🆕 new|🔝 top|' +
     '➡️ arrow right next|⬅️ arrow left back|⬆️ arrow up|⬇️ arrow down|🔄 refresh sync|' +
     '⏳ hourglass waiting|🚀 rocket launch fast|🛑 stop halt|🅿️ parking'],

    ['Things', 'coffee',
     '☕ coffee tea break|🍵 tea green|🥤 drink cup|🧃 juice|🍽️ food plate lunch dinner|' +
     '🍕 pizza|🍔 burger|🌮 taco|🍟 fries chips|🥗 salad healthy|🍎 apple fruit|🍌 banana|' +
     '🍇 grapes|🍉 watermelon|🍰 cake|🎂 birthday cake|🍪 biscuit cookie|🍫 chocolate|' +
     '🚗 car drive|🚕 taxi|🚌 bus|🚚 lorry truck delivery|🚲 bike bicycle|✈️ plane flight travel|' +
     '🚆 train|🛠️ tools|🗺️ map|🧭 compass direction|🏝️ island holiday|🌴 palm tree|' +
     '☀️ sun sunny weather|🌤️ partly cloudy|☁️ cloud cloudy|🌧️ rain wet|⛈️ storm thunder|' +
     '❄️ snow cold|🌙 moon night|🌍 earth world globe|🌊 wave water sea|🌱 seedling grow|' +
     '🌳 tree|🌸 blossom flower|💧 water drop|⚡ electric power fast|🔌 plug power|' +
     '🔋 battery|📷 camera photo|📹 video camera|🎧 headphones audio|🎤 microphone mic|' +
     '🖼️ picture frame image|📺 tv screen|⚽ football|🏀 basketball|🎮 game|🎵 music note'],
  ];

  /* Parsed once: [{emoji, words, group}] */
  const ALL = [];
  const BY_GROUP = {};
  GROUPS.forEach(function (g) {
    const name = g[0];
    BY_GROUP[name] = [];
    g[2].split('|').forEach(function (entry) {
      const bits = entry.trim().split(/\s+/);
      if (!bits.length) return;
      const item = {e: bits[0], w: bits.slice(1).join(' '), g: name};
      ALL.push(item);
      BY_GROUP[name].push(item);
    });
  });

  let sheet = null;      // the popover element, made once and reused
  let onPick = null;     // who asked for it
  let anchor = null;

  function build() {
    if (sheet) return sheet;
    sheet = document.createElement('div');
    sheet.className = 'emo';
    // The picker lives on <body> so it is not clipped by the popup. Marked as a
    // chat layer, or picking an emoji would count as a click outside the panel
    // and shut it.
    sheet.setAttribute('data-chat-layer', '');
    sheet.hidden = true;
    sheet.innerHTML =
      '<div class="emo-top">' +
        '<div class="emo-search"><input type="text" placeholder="Search emoji…" ' +
        'aria-label="Search emoji" autocomplete="off" data-emo-q></div>' +
      '</div>' +
      '<div class="emo-tabs" data-emo-tabs></div>' +
      '<div class="emo-grid" data-emo-grid role="listbox"></div>';
    document.body.appendChild(sheet);

    const tabs = sheet.querySelector('[data-emo-tabs]');
    GROUPS.forEach(function (g, i) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'emo-tab' + (i === 0 ? ' on' : '');
      b.textContent = g[0];
      b.addEventListener('click', function () {
        sheet.querySelectorAll('.emo-tab').forEach(function (t) { t.classList.remove('on'); });
        b.classList.add('on');
        sheet.querySelector('[data-emo-q]').value = '';
        draw(BY_GROUP[g[0]]);
      });
      tabs.appendChild(b);
    });

    sheet.querySelector('[data-emo-q]').addEventListener('input', function () {
      const q = this.value.trim().toLowerCase();
      if (!q) { draw(BY_GROUP[GROUPS[0][0]]); return; }
      // Match the start of any keyword first, so "car" finds 🚗 before 🃏.
      const starts = ALL.filter(function (i) {
        return i.w.split(' ').some(function (w) { return w.indexOf(q) === 0; });
      });
      const rest = ALL.filter(function (i) {
        return starts.indexOf(i) < 0 && i.w.indexOf(q) >= 0;
      });
      draw(starts.concat(rest));
    });

    sheet.addEventListener('click', function (e) {
      const b = e.target.closest('[data-emo]');
      if (!b) return;
      if (onPick) onPick(b.dataset.emo);
      close();
    });

    sheet.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.stopPropagation(); close(); }
    });

    return sheet;
  }

  function draw(items) {
    const grid = sheet.querySelector('[data-emo-grid]');
    if (!items.length) {
      grid.innerHTML = '<div class="emo-none">Nothing matches that.</div>';
      return;
    }
    grid.innerHTML = items.slice(0, 240).map(function (i) {
      return '<button type="button" class="emo-btn" data-emo="' + i.e +
             '" title="' + i.w.split(' ')[0] + '" aria-label="' + i.w.split(' ')[0] +
             '">' + i.e + '</button>';
    }).join('');
  }

  function place() {
    /* Above the anchor when there is room, below when there is not, and never
       off the side — the picker opens from a button in the bottom corner of a
       popup that is itself in the bottom corner. */
    const a = anchor.getBoundingClientRect();
    const w = sheet.offsetWidth, h = sheet.offsetHeight;
    let left = Math.min(Math.max(8, a.left - w + a.width), window.innerWidth - w - 8);
    let top = a.top - h - 8;
    if (top < 8) top = Math.min(a.bottom + 8, window.innerHeight - h - 8);
    sheet.style.left = Math.round(left) + 'px';
    sheet.style.top = Math.round(Math.max(8, top)) + 'px';
  }

  function open(anchorEl, pick) {
    build();
    anchor = anchorEl;
    onPick = pick;
    sheet.querySelector('[data-emo-q]').value = '';
    sheet.querySelectorAll('.emo-tab').forEach(function (t, i) {
      t.classList.toggle('on', i === 0);
    });
    draw(BY_GROUP[GROUPS[0][0]]);
    sheet.hidden = false;
    place();
    sheet.querySelector('[data-emo-q]').focus();
  }

  function close() {
    if (sheet) sheet.hidden = true;
    onPick = null;
    anchor = null;
  }

  document.addEventListener('click', function (e) {
    if (!sheet || sheet.hidden) return;
    if (sheet.contains(e.target) || (anchor && anchor.contains(e.target))) return;
    close();
  });
  window.addEventListener('resize', function () { if (sheet && !sheet.hidden) place(); });

  window.ajiEmoji = {
    open: function (anchorEl, pick) {
      // Toggle: pressing the same button again puts it away.
      if (sheet && !sheet.hidden && anchor === anchorEl) { close(); return; }
      open(anchorEl, pick);
    },
    close: close,
    isOpen: function () { return !!(sheet && !sheet.hidden); },
  };
})();
