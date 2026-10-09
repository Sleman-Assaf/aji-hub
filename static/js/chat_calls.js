/* chat_calls.js
   Voice calls and screen sharing, on top of the chat.

   The Hub introduces two browsers to each other and then gets out of the way.
   Every offer, answer and ICE candidate goes through Django — see
   messaging/call_views.py — and once they have been exchanged the audio travels
   straight between the two machines. Nothing about the sound reaches the server.

   Two decisions explain most of the shape of this file.

   **A mesh, not a bridge.** Everybody in a call holds one connection to
   everybody else. That is why a call is capped at eight: with nine people each
   person is sending nine copies of their microphone. Anything larger wants a
   server that mixes the audio, which is a different program from this one.

   **The layout of a connection never changes.** Each connection is negotiated
   once, with room for two things — a microphone and a screen — and the screen's
   slot starts empty. Sharing a screen is then `replaceTrack` into a slot that
   already exists, which needs no renegotiation at all. The obvious alternative,
   adding the screen when somebody presses the button, means renegotiating every
   connection in the call at the same moment, which is where mesh calls fall
   over.

   The camera is deliberately absent. The only video here is a shared screen. */

(function () {
  'use strict';

  const LAYER = document.getElementById('callLayer');
  if (!LAYER || !window.RTCPeerConnection) return;

  /* How often somebody in a call asks the server what has changed. A second is
     affordable because only people actually on a call make this request — the
     poll that watches for messages, which everybody makes, stays at four. */
  const SYNC_MS = 1000;

  const CALL = {
    id: null,          // the call
    conv: null,        // the conversation it is in
    peer: null,        // this browser's address within it
    ice: [],           // STUN/TURN, from the server; empty on one network
    state: 'idle',     // idle | ringing | dialling | live
    after: 0,          // the last signal collected
    muted: false,
    sharing: false,
    mic: null,         // MediaStream
    screen: null,      // MediaStream, while I am sharing
    peers: {},         // peer_id -> connection
    roster: [],        // what the server last said about who is here
    ringing: [],       // who it is still ringing for
    incoming: null,    // the call ringing at me, before I answer it
    startedAt: 0,
    timer: null,
    sync: null,
    /* Bridge mode: a group call carried by the server's media bridge instead
       of a mesh, which is what allows more than eight people. One connection
       instead of one per person: the mic goes up, everybody else's voices
       come back already mixed, and the shared screen arrives as one stream
       whoever is sharing it. */
    bridge: false,     // this call is on the bridge
    bpc: null,         // the one RTCPeerConnection, to the bridge
    bScreenSender: null, // where my screen goes when I share
    bScreen: null,     // MediaStream: the stage, whoever is sharing it
    bAudioEl: null,    // the mixed audio, playing
    bLevel: 0,         // how loud the mix is, for a modest talking indicator
  };
  window.AJI_CALL = CALL;

  /* ── small helpers ───────────────────────────────────────────────────── */

  const $ = sel => LAYER.querySelector(sel);

  function escH(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function csrf() {
    const v = document.cookie.match('(^|;) ?csrftoken=([^;]*)(;|$)');
    return v ? v[2] : '';
  }

  function post(url, body) {
    const fd = new FormData();
    Object.keys(body || {}).forEach(k => {
      if (body[k] !== null && body[k] !== undefined) fd.append(k, body[k]);
    });
    fd.append('csrfmiddlewaretoken', csrf());
    return fetch(url, {method: 'POST', body: fd,
                       headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json().then(d => ({ok: r.ok, d})).catch(() => ({ok: false, d: {}})));
  }

  function icon(name, size, stroke) {
    return (typeof svgIcon === 'function') ? svgIcon(name, size, stroke) : '';
  }

  function say(msg) {
    if (typeof toast === 'function') toast(msg); else console.warn('[call]', msg);
  }

  function avatar(p, size) {
    const s = size || 40;
    const style = `width:${s}px;height:${s}px;font-size:${Math.round(s / 2.8)}px`;
    if (p.photo)
      return `<span class="cl-av" style="${style};padding:0"><img src="${escH(p.photo)}" alt=""></span>`;
    return `<span class="cl-av" style="${style}">${escH(p.initials || '?')}</span>`;
  }

  function mmss(secs) {
    const m = Math.floor(secs / 60), s = secs % 60;
    return m + ':' + String(s).padStart(2, '0');
  }

  /* Microphones and screens are only offered to a page the browser considers
     secure. Over plain HTTP on a machine name they are not merely refused —
     navigator.mediaDevices is not there at all, so the button would appear to do
     nothing. Better to say why. */
  function canCapture() {
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) return true;
    say(window.isSecureContext
      ? 'This browser will not give the Hub a microphone.'
      : 'Calls need a secure connection (https). Ask IT to enable it for the Hub.');
    return false;
  }

  /* ── the ring, and the ringback ───────────────────────────────────────
     Made rather than downloaded: a ringtone is two notes and a rest, and a file
     is another asset to serve, cache and keep. The context is created on demand
     because a browser will not let a page make a sound before it has been
     interacted with, and refuses to create one at all on some. */

  let audioCtx = null, ringTimer = null, ringNodes = [];

  function ctx() {
    if (!audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try { audioCtx = new AC(); } catch (e) { return null; }
    }
    if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
    return audioCtx;
  }

  function blip(freq, at, len, vol) {
    const c = ctx();
    if (!c) return;
    const osc = c.createOscillator(), gain = c.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    // Eased in and out. A square-edged tone clicks, and a click on every ring is
    // the sort of small ugliness people notice without being able to name it.
    gain.gain.setValueAtTime(0, c.currentTime + at);
    gain.gain.linearRampToValueAtTime(vol, c.currentTime + at + 0.04);
    gain.gain.setValueAtTime(vol, c.currentTime + at + len - 0.06);
    gain.gain.linearRampToValueAtTime(0, c.currentTime + at + len);
    osc.connect(gain).connect(c.destination);
    osc.start(c.currentTime + at);
    osc.stop(c.currentTime + at + len + 0.02);
    ringNodes.push(osc);
  }

  function startRing(kind) {
    stopRing();
    const round = kind === 'in'
      ? () => { blip(660, 0, .35, .18); blip(560, .42, .35, .18); }   // somebody calling
      : () => { blip(420, 0, .7, .07); };                             // waiting for them
    round();
    ringTimer = setInterval(round, kind === 'in' ? 2200 : 3200);
  }

  function stopRing() {
    if (ringTimer) { clearInterval(ringTimer); ringTimer = null; }
    ringNodes.forEach(n => { try { n.stop(); } catch (e) {} });
    ringNodes = [];
  }

  /* ── who is talking ───────────────────────────────────────────────────
     A call with nothing to look at gives no sign that it is working. Lighting up
     whoever is speaking is the cheapest way to show that it is — and it answers
     "can you hear me?" without anybody having to ask. */

  function watchLevel(stream, onLevel) {
    const c = ctx();
    if (!c || !stream || !stream.getAudioTracks().length) return () => {};
    let src, analyser, raf = null;
    try {
      src = c.createMediaStreamSource(stream);
      analyser = c.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = .5;
      src.connect(analyser);
    } catch (e) { return () => {}; }
    const buf = new Uint8Array(analyser.frequencyBinCount);
    let last = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const now = performance.now();
      if (now - last < 120) return;      // ten times a second is plenty
      last = now;
      analyser.getByteFrequencyData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      onLevel(Math.sqrt(sum / buf.length) / 255);
    };
    tick();
    return () => {
      if (raf) cancelAnimationFrame(raf);
      try { src.disconnect(); } catch (e) {}
    };
  }

  /* ── connections ──────────────────────────────────────────────────────
     One per other person in the call. Which of the two makes the offer is
     decided by comparing the two addresses, so both sides reach the same answer
     without asking: whoever's is larger offers. That, plus the standard
     "polite peer yields" rule below, is what stops two browsers that joined in
     the same second from offering to each other at once and deadlocking. */

  function micTrack() {
    return CALL.mic ? (CALL.mic.getAudioTracks()[0] || null) : null;
  }

  function screenTrack() {
    return CALL.screen ? (CALL.screen.getVideoTracks()[0] || null) : null;
  }

  /* ── admin call settings (IT ▸ Call settings), sent per call ──────
     CALL.rtc arrives in the join response. Everything here degrades to the
     browser's own defaults when a key is absent or an API is missing, so an
     old response or an odd browser costs nothing but the tuning. */

  function rtcOf(section) { return (CALL.rtc && CALL.rtc[section]) || {}; }

  function pcConfig() {
    const cfg = {iceServers: CALL.ice};
    // 'relay' forces every connection through the TURN server — the admin's
    // lever when direct UDP between machines is not allowed at all.
    if (rtcOf('transport').ice_policy === 'relay') cfg.iceTransportPolicy = 'relay';
    return cfg;
  }

  /* Pin a transceiver to the admin's codec. Setting preferences on our side
     before offering decides both directions: the answerer can only pick from
     what the offer lists first. Helper codecs (rtx/red/fec, comfort noise)
     ride along — stripping those would disable loss recovery, not choose a
     codec. */
  function preferCodecs(t, kind) {
    try {
      const want = kind === 'audio' ? rtcOf('audio').codec : rtcOf('screen').codec;
      if (!want || want === 'auto' || !t || !t.setCodecPreferences) return;
      if (!window.RTCRtpReceiver || !RTCRtpReceiver.getCapabilities) return;
      const codecs = RTCRtpReceiver.getCapabilities(kind).codecs;
      const mt = (kind + '/' + want).toLowerCase();
      const chosen = codecs.filter(c => c.mimeType.toLowerCase() === mt);
      if (!chosen.length) return;              // browser lacks it: leave be
      const helpers = ['rtx', 'red', 'ulpfec', 'flexfec-03', 'cn', 'telephone-event']
        .map(x => (kind + '/' + x).toLowerCase());
      t.setCodecPreferences(chosen.concat(
        codecs.filter(c => helpers.includes(c.mimeType.toLowerCase()))));
    } catch (e) { /* tuning, never load-bearing */ }
  }

  /* Write the admin's Opus parameters into an SDP we are about to hand the
     other side. An fmtp line states what WE want to receive, so it steers the
     other end's encoder — each side tunes its inbound leg by tuning what it
     sends out. */
  function tuneOpus(sdp) {
    const a = rtcOf('audio');
    const m = sdp.match(/a=rtpmap:(\d+) opus\/48000[^\r\n]*/);
    if (!m) return sdp;
    const pt = m[1];
    const extras = [];
    if (a.opus_max_kbps > 0) extras.push('maxaveragebitrate=' + (a.opus_max_kbps * 1000));
    extras.push('useinbandfec=' + (a.opus_fec === false ? 0 : 1));
    if (a.opus_dtx) extras.push('usedtx=1');
    const fmtp = new RegExp('a=fmtp:' + pt + ' ([^\r\n]*)');
    const hit = sdp.match(fmtp);
    if (hit) {
      const keep = hit[1].split(';').map(s => s.trim()).filter(s =>
        s && !/^(maxaveragebitrate|useinbandfec|usedtx)=/.test(s));
      return sdp.replace(fmtp, 'a=fmtp:' + pt + ' ' + keep.concat(extras).join(';'));
    }
    return sdp.replace(m[0], m[0] + '\r\na=fmtp:' + pt + ' ' + extras.join(';'));
  }

  function tunedLocal(pc) {
    const d = pc.localDescription;
    return JSON.stringify({type: d.type, sdp: tuneOpus(d.sdp)});
  }

  /* Cap a screen sender's bitrate to the admin's ceiling. */
  function capScreenSender(sender) {
    const kbps = rtcOf('screen').max_kbps;
    if (!sender || !kbps || !sender.getParameters) return;
    try {
      const p = sender.getParameters();
      p.encodings = (p.encodings && p.encodings.length) ? p.encodings : [{}];
      p.encodings.forEach(e => { e.maxBitrate = kbps * 1000; });
      sender.setParameters(p).catch(() => {});
    } catch (e) { /* tuning */ }
  }

  function micConstraints() {
    const a = rtcOf('audio');
    return {
      echoCancellation: a.echo_cancellation !== false,
      noiseSuppression: a.noise_suppression !== false,
      autoGainControl: a.auto_gain !== false,
    };
  }

  /* The mic is opened before the join round trip (the permission prompt
     should not race the ringing), so the admin's processing flags are laid
     onto the live track once the join response brings them. */
  function retuneMic() {
    if (!CALL.mic) return;
    CALL.mic.getAudioTracks().forEach(t => {
      if (t.applyConstraints) t.applyConstraints(micConstraints()).catch(() => {});
    });
  }

  function connect(id) {
    const polite = CALL.peer < id;              // the smaller address yields
    const pc = new RTCPeerConnection(pcConfig());
    const st = {
      id, pc, polite,
      makingOffer: false, ignoreOffer: false,
      stream: new MediaStream(),
      audioEl: null, audioSender: null, videoSender: null,
      stopLevel: null, level: 0,
    };
    CALL.peers[id] = st;

    pc.onicecandidate = e => {
      if (e.candidate) signal(id, 'ice', JSON.stringify(e.candidate));
    };

    pc.ontrack = e => {
      st.stream.addTrack(e.track);
      if (e.track.kind === 'audio') {
        // Played through an element rather than the audio graph: it is the
        // element that makes the browser actually pull the stream, and without
        // one attached the level meter reads silence even when the audio is fine.
        if (!st.audioEl) {
          st.audioEl = document.createElement('audio');
          st.audioEl.autoplay = true;
          st.audioEl.playsInline = true;
          LAYER.appendChild(st.audioEl);
        }
        st.audioEl.srcObject = st.stream;
        st.audioEl.play().catch(() => {});
        if (st.stopLevel) st.stopLevel();
        st.stopLevel = watchLevel(st.stream, v => { st.level = v; paintLevels(); });
      } else {
        drawStage();
      }
    };

    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'failed') {
        // Almost always two machines with no route between them — somebody on
        // the VPN, with no TURN server configured. Saying so beats a silent call.
        say('Could not connect to everyone on this call.');
      }
      drawPeople();
    };

    pc.onnegotiationneeded = () => {
      if (polite) return;                    // the offer is the other side's job
      queue(async () => {
        try {
          st.makingOffer = true;
          await pc.setLocalDescription();
          signal(id, 'offer', tunedLocal(pc));
        } finally { st.makingOffer = false; }
      });
    };

    /* The offering side lays out the connection: one slot for a microphone, one
       for a screen, both there from the start whether or not anything is in them
       yet. The answering side does not add anything — it fills in the slots the
       offer described, in fillSlots. */
    if (!polite) {
      const a = pc.addTransceiver(micTrack() || 'audio', {direction: 'sendrecv'});
      const v = pc.addTransceiver(screenTrack() || 'video', {direction: 'sendrecv'});
      preferCodecs(a, 'audio');
      preferCodecs(v, 'video');
      st.audioSender = a.sender;
      st.videoSender = v.sender;
    }
    return st;
  }

  /* Put my microphone and, if I am sharing, my screen into the slots this
     connection has. Called by the answering side once the offer has arrived and
     the slots exist, and again whenever what I am sending changes. */
  function fillSlots(st) {
    const trs = st.pc.getTransceivers();
    const of = kind => trs.find(t =>
      (t.receiver && t.receiver.track && t.receiver.track.kind === kind) ||
      (t.sender && t.sender.track && t.sender.track.kind === kind));
    const a = of('audio'), v = of('video');
    if (a) {
      st.audioSender = a.sender;
      if (a.direction !== 'sendrecv') a.direction = 'sendrecv';
      if (a.sender.track !== micTrack()) a.sender.replaceTrack(micTrack());
    }
    if (v) {
      st.videoSender = v.sender;
      if (v.direction !== 'sendrecv') v.direction = 'sendrecv';
      if (v.sender.track !== screenTrack()) v.sender.replaceTrack(screenTrack());
    }
  }

  function dropPeer(id) {
    const st = CALL.peers[id];
    if (!st) return;
    delete CALL.peers[id];
    if (st.stopLevel) st.stopLevel();
    try { st.pc.close(); } catch (e) {}
    if (st.audioEl) { st.audioEl.srcObject = null; st.audioEl.remove(); }
    drawPeople();
    drawStage();
  }

  function signal(to, kind, payload) {
    if (!CALL.id) return;
    post(`/messaging/call/${CALL.id}/signal/`,
         {peer: CALL.peer, to, kind, payload: payload || ''}).catch(() => {});
  }

  /* Descriptions and candidates have to be applied in the order they were sent,
     and every step of applying one is asynchronous. One chain for the whole call
     keeps that order without a queue per connection. */
  let chain = Promise.resolve();

  function queue(fn) {
    chain = chain.then(fn).catch(e => console.warn('[call]', e));
    return chain;
  }

  function handle(s) {
    if (CALL.bridge) return;   // no mesh: notes between peers mean nothing here
    let st = CALL.peers[s.from];
    if (s.kind === 'bye') { dropPeer(s.from); return; }
    if (!st) st = connect(s.from);
    const pc = st.pc;

    if (s.kind === 'ice') {
      let cand;
      try { cand = JSON.parse(s.payload); } catch (e) { return; }
      queue(() => pc.addIceCandidate(cand).catch(e => {
        // A candidate that arrives before the description it belongs to is
        // expected while an offer and an answer cross; it is not an error worth
        // showing anybody.
        if (!st.ignoreOffer) console.debug('[call] candidate', e);
      }));
      return;
    }

    let desc;
    try { desc = JSON.parse(s.payload); } catch (e) { return; }
    queue(async () => {
      /* Both sides offering at once. The impolite one carries on and ignores
         what came in; the polite one abandons its own offer — setRemoteDescription
         rolls it back on the way — and answers instead. Exactly one of the two
         yields, so the connection completes rather than both sides waiting. */
      const collision = desc.type === 'offer' &&
                        (st.makingOffer || pc.signalingState !== 'stable');
      st.ignoreOffer = !st.polite && collision;
      if (st.ignoreOffer) return;
      await pc.setRemoteDescription(desc);
      if (desc.type === 'offer') {
        fillSlots(st);
        await pc.setLocalDescription();
        signal(s.from, 'answer', tunedLocal(pc));
      }
    });
  }

  /* The roster is the truth about who is in the call. Connections are made and
     dropped to match it rather than in response to anybody's announcement, so a
     browser that vanished without saying goodbye is cleaned up all the same —
     the server stops listing it, and this notices. */
  function reconcile(peers) {
    if (CALL.bridge) return;              // one connection, and it is not to a peer
    const here = new Set(peers.filter(p => !p.is_me).map(p => p.peer_id));
    here.forEach(id => { if (!CALL.peers[id]) connect(id); });
    Object.keys(CALL.peers).forEach(id => { if (!here.has(id)) dropPeer(id); });
  }

  /* ── the bridge connection ────────────────────────────────────────── */

  function gatherComplete(pc) {
    // Non-trickle: the offer carries its candidates, so signalling is one
    // round trip through Django instead of a conversation.
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise(resolve => {
      const check = () => {
        if (pc.iceGatheringState === 'complete') {
          pc.removeEventListener('icegatheringstatechange', check);
          resolve();
        }
      };
      pc.addEventListener('icegatheringstatechange', check);
      setTimeout(resolve, 3000);          // a stuck gatherer is not a stuck call
    });
  }

  /* One connection to the media bridge, laid out once and never renegotiated:
     the microphone slot answers with everybody's voices mixed, the send-only
     video slot is where a screen goes when sharing starts (replaceTrack, no
     new offer), and the receive-only slot is the stage — black until somebody
     shares, so a share starting is a picture changing, not a negotiation. */
  async function bridgeConnect() {
    const pc = new RTCPeerConnection(pcConfig());
    CALL.bpc = pc;

    const aT = pc.addTransceiver(micTrack() || 'audio', {direction: 'sendrecv'});
    const vOut = pc.addTransceiver(screenTrack() || 'video', {direction: 'sendonly'});
    const vIn = pc.addTransceiver('video', {direction: 'recvonly'});
    preferCodecs(aT, 'audio');
    preferCodecs(vOut, 'video');
    preferCodecs(vIn, 'video');
    CALL.bScreenSender = vOut.sender;

    pc.ontrack = e => {
      if (e.track.kind === 'audio') {
        const stream = new MediaStream([e.track]);
        if (!CALL.bAudioEl) {
          CALL.bAudioEl = document.createElement('audio');
          CALL.bAudioEl.autoplay = true;
          CALL.bAudioEl.playsInline = true;
          LAYER.appendChild(CALL.bAudioEl);
        }
        CALL.bAudioEl.srcObject = stream;
        CALL.bAudioEl.play().catch(() => {});
        watchLevel(stream, v => { CALL.bLevel = v; paintLevels(); });
      } else {
        CALL.bScreen = new MediaStream([e.track]);
        drawStage();
      }
    };

    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'failed')
        say('Lost the connection to the call server.');
      drawPeople();
    };

    await pc.setLocalDescription(await pc.createOffer());
    await gatherComplete(pc);
    const {ok, d} = await post(`/messaging/call/${CALL.id}/bridge/offer/`,
                               {peer: CALL.peer, sdp: tuneOpus(pc.localDescription.sdp)});
    if (!ok || !d.sdp) {
      say((d && d.error) || 'Could not reach the call server.');
      return;
    }
    // The answer is what steers OUR encoder, so the admin's Opus settings
    // are written into it before it is applied.
    await pc.setRemoteDescription({type: 'answer', sdp: tuneOpus(d.sdp)});
  }

  /* ── starting, answering, ending ──────────────────────────────────── */

  async function getMic() {
    if (CALL.mic) return CALL.mic;
    CALL.mic = await navigator.mediaDevices.getUserMedia({
      audio: micConstraints(),
      video: false,
    });
    return CALL.mic;
  }

  function micError(e) {
    const name = (e && e.name) || '';
    if (name === 'NotAllowedError')
      return 'The Hub needs permission to use your microphone.';
    if (name === 'NotFoundError')
      return 'No microphone found on this computer.';
    if (name === 'NotReadableError')
      return 'Your microphone is in use by another program.';
    return 'Could not start the microphone.';
  }

  /* Start a call here, or answer the one already going on. One path for both,
     because they are the same request — the server decides which it was. */
  async function join(convId, dialling) {
    if (CALL.id) { say('You are already on a call.'); return; }
    if (!canCapture()) return;
    ctx();                                  // while a click is still in hand
    setState(dialling ? 'dialling' : 'live');
    try {
      await getMic();
    } catch (e) {
      setState('idle');
      say(micError(e));
      return;
    }
    const {ok, d} = await post(`/messaging/${convId}/call/join/`, {});
    if (!ok || !d.ok) {
      teardown();
      say((d && d.error) || 'Could not start the call.');
      return;
    }
    CALL.id = d.call.id;
    CALL.conv = convId;
    CALL.peer = d.peer_id;
    CALL.ice = d.ice_servers || [];
    CALL.rtc = d.rtc || {};
    retuneMic();
    CALL.after = 0;
    CALL.startedAt = Date.now();
    CALL.muted = false;
    CALL.bridge = !!d.bridge;
    applyCall(d.call);
    if (CALL.bridge) bridgeConnect().catch(() => say('Could not reach the call server.'));
    setState(d.call.peers.length > 1 ? 'live' : 'dialling');
    if (CALL.state === 'dialling') startRing('out');
    LAYER.dataset.open = '1';
    CALL.sync = setInterval(sync, SYNC_MS);
    CALL.timer = setInterval(drawClock, 500);
    drawAll();
  }

  function hangUp() {
    const id = CALL.id, conv = CALL.conv, peer = CALL.peer;
    stopShare(true);
    // Down at once, and the server told afterwards. A hang-up that waits for a
    // round trip before the dock goes away feels like a button that did not
    // work, and pressing it twice is what people do next.
    teardown();
    if (id) post(`/messaging/call/${id}/leave/`, {peer})
      // The line in the chat is written by whichever request ends the call, and
      // this may be it — so the transcript is refreshed once the server has
      // answered rather than on the way out, which would race it.
      .then(() => { if (window.chatRefreshConv) window.chatRefreshConv(conv); })
      .catch(() => {});
  }

  /* ── adding people mid-call ────────────────────────────────────────
     Any call grows from inside: pick a colleague, their phone rings on its
     own forty-five-second clock, and joining a direct call turns it into a
     group call. The server enforces the rest (capacity, tickets into the
     conversation); this is just the picker. */

  let addTimer = null;

  function addPanel() { return LAYER.querySelector('.cl-add'); }

  function toggleAdd(open) {
    const box = addPanel();
    if (!box) return;
    const show = open !== undefined ? open : box.hidden;
    box.hidden = !show;
    LAYER.querySelector('[data-cl-add]').classList.toggle('on', show);
    if (show) {
      const inp = box.querySelector('.cl-add-in');
      inp.value = '';
      inp.focus();
      loadInvitees('');
    }
  }

  function loadInvitees(q) {
    if (!CALL.id) return;
    fetch(`/messaging/call/${CALL.id}/invitees/?peer=${encodeURIComponent(CALL.peer)}` +
          (q ? `&q=${encodeURIComponent(q)}` : ''),
          {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json())
      .then(d => drawInvitees((d && d.people) || []))
      .catch(() => {});
  }

  function drawInvitees(people) {
    const list = addPanel() && addPanel().querySelector('.cl-add-list');
    if (!list) return;
    if (!people.length) {
      list.innerHTML = '<div class="cl-add-none">Nobody found.</div>';
      return;
    }
    list.innerHTML = people.map(p =>
      `<button type="button" class="cl-add-row" data-cl-inv="${p.id}"
               ${p.ringing ? 'disabled' : ''}>
         ${avatar(p, 28)}
         <span class="nm">${escH(p.name)}</span>
         <span class="tag">${p.ringing ? 'ringing…' : icon('phone', 13)}</span>
       </button>`).join('');
  }

  async function invite(userId, row) {
    if (row) { row.disabled = true; row.querySelector('.tag').textContent = '…'; }
    const {ok, d} = await post(`/messaging/call/${CALL.id}/invite/`,
                               {peer: CALL.peer, user: userId});
    if (!ok || !d.ok) {
      if (row) row.disabled = false;
      say((d && d.error) || 'Could not add them.');
      loadInvitees(addPanel().querySelector('.cl-add-in').value.trim());
      return;
    }
    // The roster answer already shows them under "ringing…"; the picker rows
    // refresh to match, and the panel stays open — "we can add more than one".
    applyCall(d.call);
    drawAll();
    loadInvitees(addPanel().querySelector('.cl-add-in').value.trim());
  }

  function teardown() {
    toggleAdd(false);
    const wasIn = CALL.conv;
    stopRing();
    if (CALL.sync) { clearInterval(CALL.sync); CALL.sync = null; }
    if (CALL.timer) { clearInterval(CALL.timer); CALL.timer = null; }
    Object.keys(CALL.peers).forEach(dropPeer);
    if (CALL.bpc) { try { CALL.bpc.close(); } catch (e) {} CALL.bpc = null; }
    if (CALL.bAudioEl) { CALL.bAudioEl.srcObject = null; CALL.bAudioEl.remove(); CALL.bAudioEl = null; }
    CALL.bScreen = null;
    CALL.bScreenSender = null;
    CALL.bridge = false;
    CALL.bLevel = 0;
    if (CALL.mic) { CALL.mic.getTracks().forEach(t => t.stop()); CALL.mic = null; }
    if (CALL.screen) { CALL.screen.getTracks().forEach(t => t.stop()); CALL.screen = null; }
    CALL.id = CALL.conv = CALL.peer = null;
    CALL.peers = {};
    CALL.roster = [];
    CALL.ringing = [];
    CALL.muted = CALL.sharing = false;
    CALL.after = 0;
    LAYER.dataset.open = '0';
    setState('idle');
    drawAll();
    // A call leaves a line in the conversation, written under the caller's name
    // — and the chat's poll ignores messages from you, so the person who made
    // the call would be the only one who could not see that it happened.
    if (wasIn && window.chatRefreshConv) window.chatRefreshConv(wasIn);
  }

  function setState(s) {
    CALL.state = s;
    LAYER.dataset.state = s;
  }

  /* ── the loop ─────────────────────────────────────────────────────── */

  function applyCall(call) {
    CALL.roster = call.peers || [];
    CALL.ringing = call.ringing || [];
    const others = CALL.roster.filter(p => !p.is_me);
    if (CALL.state === 'dialling' && others.length) { setState('live'); stopRing(); }
    reconcile(CALL.roster);
  }

  function sync() {
    if (!CALL.id) return;
    fetch(`/messaging/call/${CALL.id}/sync/?peer=${CALL.peer}&after=${CALL.after}`,
          {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(r => r.json())
      .then(d => {
        if (d.ended) {
          const note = d.summary || 'Call ended';
          teardown();
          say(note);
          return;
        }
        const notes = d.signals || [];
        if (notes.length) {
          // The cursor moves before anything is applied. Advancing it afterwards
          // would hand the same offer back on every sync if applying it threw.
          CALL.after = Math.max(CALL.after, ...notes.map(s => s.id));
          notes.forEach(handle);
        }
        // Somebody else started sharing, which takes it from me. The server is
        // the one that decides; this is where I find out and stop.
        if (CALL.sharing && d.me && !d.me.sharing) stopShare(true);
        if (d.me) CALL.muted = d.me.muted;
        applyCall(d.call);
        drawAll();
      })
      .catch(() => {});
  }

  /* ── mute and screen sharing ──────────────────────────────────────── */

  function toggleMute() {
    if (!CALL.id) return;
    CALL.muted = !CALL.muted;
    // The track is disabled here, not merely announced: a mute that only told
    // other people you were muted would be a lie.
    if (CALL.mic) CALL.mic.getAudioTracks().forEach(t => { t.enabled = !CALL.muted; });
    post(`/messaging/call/${CALL.id}/state/`,
         {peer: CALL.peer, muted: CALL.muted ? 1 : 0}).catch(() => {});
    drawAll();
  }

  async function toggleShare() {
    if (!CALL.id) return;
    if (CALL.sharing) { stopShare(); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      say('This browser cannot share a screen.');
      return;
    }
    let stream;
    try {
      // Capped rather than left to the browser: a mesh sends the screen once
      // per person in the call, and a slide or a drawing at fifteen frames is
      // indistinguishable from one at sixty at a fraction of the traffic. The
      // caps themselves come from IT ▸ Call settings.
      const scr = rtcOf('screen');
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: {frameRate: {max: scr.capture_fps || 15},
                width: {max: scr.capture_width || 1920}, height: {max: 1080}},
        audio: false,
      });
    } catch (e) {
      // Pressing Cancel on the picker is not an error worth a message.
      if (e && e.name !== 'NotAllowedError') say('Could not share the screen.');
      return;
    }
    CALL.screen = stream;
    CALL.sharing = true;
    const track = stream.getVideoTracks()[0];
    // The browser puts its own "Stop sharing" bar on screen, and people use it.
    if (track) track.onended = () => stopShare();
    if (CALL.bridge) {
      if (CALL.bScreenSender) {
        CALL.bScreenSender.replaceTrack(track).catch(() => {});
        capScreenSender(CALL.bScreenSender);
      }
    } else {
      Object.values(CALL.peers).forEach(st => {
        if (st.videoSender) {
          st.videoSender.replaceTrack(track).catch(() => {});
          capScreenSender(st.videoSender);
        }
      });
    }
    post(`/messaging/call/${CALL.id}/state/`, {peer: CALL.peer, sharing: 1}).catch(() => {});
    drawAll();
  }

  function stopShare(quiet) {
    if (!CALL.screen && !CALL.sharing) return;
    if (CALL.screen) CALL.screen.getTracks().forEach(t => t.stop());
    CALL.screen = null;
    CALL.sharing = false;
    if (CALL.bridge) {
      if (CALL.bScreenSender) CALL.bScreenSender.replaceTrack(null).catch(() => {});
    } else {
      Object.values(CALL.peers).forEach(st => {
        if (st.videoSender) st.videoSender.replaceTrack(null).catch(() => {});
      });
    }
    if (!quiet && CALL.id)
      post(`/messaging/call/${CALL.id}/state/`, {peer: CALL.peer, sharing: 0}).catch(() => {});
    drawAll();
  }

  /* ── the incoming call ────────────────────────────────────────────── */

  /* Handed the `call` block from the chat's own poll — see chat_engine.js. Null
     almost every time, which is the point of putting it there rather than giving
     the phone a timer of its own. */
  CALL.ring = function (info) {
    if (!info) {
      if (CALL.incoming) { CALL.incoming = null; stopRing(); drawRing(); }
      return;
    }
    // Already on it, or already looking at it: nothing to do.
    if (CALL.id === info.call_id || (CALL.incoming && CALL.incoming.call_id === info.call_id))
      return;
    if (CALL.id) return;                    // busy on another call
    CALL.incoming = info;
    startRing('in');
    drawRing();
  };

  /* Ring a conversation from outside this file. The header's call button is a
     delegated click handler, which is fine for a button and no use at all to the
     call log's "call them back" — that has a conversation id and no button to
     press. Refuses while a call is already going on, because joining a second
     one would take the microphone from the first. */
  CALL.start = function (convId) {
    if (!convId || CALL.id) return false;
    join(convId, true);
    return true;
  };

  function answer() {
    const info = CALL.incoming;
    if (!info) return;
    CALL.incoming = null;
    stopRing();
    drawRing();
    join(info.conv_id, false);
  }

  function decline() {
    const info = CALL.incoming;
    CALL.incoming = null;
    stopRing();
    drawRing();
    if (info) post(`/messaging/call/${info.call_id}/decline/`, {}).catch(() => {});
  }

  /* ── drawing ──────────────────────────────────────────────────────── */

  function drawAll() { drawRing(); drawPeople(); drawStage(); drawBar(); drawClock(); }

  function drawRing() {
    const box = $('.cl-ring');
    const info = CALL.incoming;
    box.hidden = !info;
    if (!info) return;
    const where = info.is_group ? ` · ${escH(info.where)}` : '';
    box.innerHTML =
      `<div class="cl-ring-in">
         ${avatar(info.from, 52)}
         <div class="cl-ring-who">
           <b>${escH(info.from.name)}</b>
           <span>${info.is_group ? 'Group call' : 'Voice call'}${where}</span>
         </div>
         <div class="cl-ring-acts">
           <button type="button" class="cl-btn no" data-cl-decline
                   title="Decline" aria-label="Decline">${icon('phone-x', 19)}</button>
           <button type="button" class="cl-btn yes" data-cl-answer
                   title="Answer" aria-label="Answer">${icon('phone-call', 19)}</button>
         </div>
       </div>`;
  }

  function drawPeople() {
    const box = $('.cl-people');
    if (!box) return;
    if (!CALL.id) { box.innerHTML = ''; return; }
    const bridgeOk = CALL.bpc && CALL.bpc.connectionState === 'connected';
    const rows = CALL.roster.map(p => {
      const st = p.is_me ? null : CALL.peers[p.peer_id];
      // On the bridge everybody's audio arrives through the one connection,
      // so "connected" is a property of the call, not of each person.
      const conn = p.is_me ? 'ok'
        : CALL.bridge ? (bridgeOk ? 'ok' : 'wait')
        : (st && st.pc.connectionState === 'connected') ? 'ok' : 'wait';
      const muted = p.is_me ? CALL.muted : p.muted;
      return `<div class="cl-p ${conn}" data-peer="${escH(p.peer_id)}">
                ${avatar(p, 34)}
                <span class="cl-p-nm">${escH(p.name)}${p.is_me ? ' <i>(you)</i>' : ''}</span>
                ${p.sharing ? `<span class="cl-p-tag">${icon('monitor-arrow-up', 13)}</span>` : ''}
                ${muted ? `<span class="cl-p-mute">${icon('microphone-slash', 14)}</span>` : ''}
              </div>`;
    });
    // Whoever has not picked up yet, so a call that is going nowhere says so
    // rather than showing an empty room.
    CALL.ringing.forEach(p => {
      rows.push(`<div class="cl-p ring">
                   ${avatar(p, 34)}
                   <span class="cl-p-nm">${escH(p.name)}</span>
                   <span class="cl-p-tag">ringing…</span>
                 </div>`);
    });
    box.innerHTML = rows.join('');
    paintLevels();
  }

  /* Kept apart from drawPeople: this runs ten times a second and touches one
     class, while redrawing the roster that often would throw away the browser's
     work on every frame. */
  function paintLevels() {
    const box = $('.cl-people');
    if (!box || !CALL.id) return;
    // The mix is one stream, so who exactly is talking is not knowable here.
    // The honest approximation: while the mix is loud, light the people who
    // are not muted — in the common case of one speaker that is one person.
    const mixLoud = CALL.bridge && CALL.bLevel > .06;
    box.querySelectorAll('.cl-p').forEach(el => {
      const id = el.dataset.peer;
      if (CALL.bridge) {
        const p = CALL.roster.find(r => r.peer_id === id);
        el.classList.toggle('talking',
          !!(mixLoud && p && !p.is_me && !p.muted));
        return;
      }
      const st = id && CALL.peers[id];
      const loud = st ? st.level > .06 : false;
      el.classList.toggle('talking', loud);
    });
  }

  function drawStage() {
    const stage = $('.cl-stage');
    const video = $('.cl-stage video');
    if (!stage || !video) return;
    const sharer = CALL.roster.find(p => p.sharing);
    if (!CALL.id || !sharer) {
      stage.hidden = true;
      video.srcObject = null;
      LAYER.dataset.stage = '0';
      return;
    }
    stage.hidden = false;
    LAYER.dataset.stage = '1';
    const want = sharer.is_me ? CALL.screen
               : CALL.bridge  ? CALL.bScreen
                              : (CALL.peers[sharer.peer_id] || {}).stream;
    if (want && video.srcObject !== want) {
      video.srcObject = want;
      video.play().catch(() => {});
    }
    // Muted deliberately: a shared screen carries no sound here, and an unmuted
    // element would block autoplay in browsers that require a gesture for it.
    video.muted = true;
    $('.cl-stage-who').textContent = sharer.is_me
      ? 'You are sharing your screen'
      : `${sharer.name} is sharing`;
  }

  function drawBar() {
    const bar = $('.cl-bar');
    if (!bar) return;
    bar.querySelector('[data-cl-mute]').classList.toggle('on', CALL.muted);
    bar.querySelector('[data-cl-mute]').innerHTML =
      icon(CALL.muted ? 'microphone-slash' : 'microphone', 18);
    bar.querySelector('[data-cl-share]').classList.toggle('on', CALL.sharing);
  }

  function drawClock() {
    const el = $('.cl-clock');
    if (!el) return;
    if (!CALL.id) { el.textContent = ''; return; }
    if (CALL.state === 'dialling') {
      const who = CALL.ringing.length
        ? CALL.ringing.map(p => p.name.split(' ')[0]).join(', ') : '';
      el.textContent = who ? `Ringing ${who}…` : 'Ringing…';
      return;
    }
    el.textContent = mmss(Math.floor((Date.now() - CALL.startedAt) / 1000));
  }

  /* ── the buttons ──────────────────────────────────────────────────── */

  LAYER.addEventListener('click', e => {
    const hit = sel => e.target.closest(sel);
    if (hit('[data-cl-answer]')) return answer();
    if (hit('[data-cl-decline]')) return decline();
    if (hit('[data-cl-hangup]')) return hangUp();
    if (hit('[data-cl-mute]')) return toggleMute();
    if (hit('[data-cl-share]')) return toggleShare();
    if (hit('[data-cl-add]')) return toggleAdd();
    const inv = hit('[data-cl-inv]');
    if (inv) return invite(inv.dataset.clInv, inv);
    if (hit('[data-cl-full]')) {
      const v = $('.cl-stage video');
      if (v && v.requestFullscreen) v.requestFullscreen().catch(() => {});
      return;
    }
    if (hit('[data-cl-min]')) {
      LAYER.dataset.min = LAYER.dataset.min === '1' ? '0' : '1';
    }
  });

  /* The call button in the chat's own header, on both shells. Bound here rather
     than in chat_engine.js so that calls stay one file: the engine knows nothing
     about them, and this needs nothing from the engine but which conversation is
     open. */
  LAYER.addEventListener('input', e => {
    if (!e.target.classList.contains('cl-add-in')) return;
    clearTimeout(addTimer);
    const q = e.target.value.trim();
    addTimer = setTimeout(() => loadInvitees(q), 220);
  });

  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-ch-call]');
    if (!btn) return;
    e.preventDefault();
    const conv = window.CHAT && window.CHAT.active;
    if (!conv) { say('Open a chat first.'); return; }
    join(conv, true);
  });

  /* Leaving the page ends the call — the connections live in this document and
     nothing survives a navigation. Worth a word before it happens rather than a
     call that drops because somebody clicked a link. */
  window.addEventListener('beforeunload', e => {
    if (!CALL.id) return;
    e.preventDefault();
    e.returnValue = '';
  });

  // A tab being closed properly is worth telling the server about at once; the
  // heartbeat would work it out in twenty seconds, which is twenty seconds of
  // everybody else looking at somebody who has gone.
  window.addEventListener('pagehide', () => {
    if (!CALL.id || !navigator.sendBeacon) return;
    const fd = new FormData();
    fd.append('peer', CALL.peer);
    fd.append('csrfmiddlewaretoken', csrf());
    navigator.sendBeacon(`/messaging/call/${CALL.id}/leave/`, fd);
  });

  setState('idle');
})();
