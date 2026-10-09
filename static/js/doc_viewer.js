/* static/js/doc_viewer.js — the Hub's document viewer.

   Opens over the page the way the booth lightbox does, and reads PDFs and
   images with the same controls. Nothing here downloads the document: a PDF is
   shown as pictures of its pages, rendered on the server, so a document marked
   view-only never sends its bytes to the browser at all. The Save button
   appears only when the server said this reader may keep a copy — and because
   the server checks that again on the download route, hiding the button is the
   courtesy rather than the control.

   Open it with:
       AJIDoc.open('/policies/doc/12/')

   The endpoint answers with {title, subtitle, kind, pages, page_url,
   download_url, may_download, filename}, where page_url carries a {n}
   placeholder for the page index. */

(function () {
  'use strict';

  var EL = null;          // the overlay, built once on first use
  var DOC = null;         // what the server told us about the open document
  var PAGE = 0;
  var ZOOM = 1;
  var LAST_FOCUS = null;

  function build() {
    if (EL) return EL;
    EL = document.createElement('div');
    EL.className = 'dv';
    EL.setAttribute('role', 'dialog');
    EL.setAttribute('aria-modal', 'true');
    EL.innerHTML =
      '<div class="dv-bar">' +
        '<div class="dv-id"><b data-dv-title></b><i data-dv-sub></i></div>' +
        '<div class="dv-tools">' +
          '<button type="button" class="dv-btn" data-dv="prev" aria-label="Previous page">&lsaquo;</button>' +
          '<span class="dv-pages" data-dv-pages></span>' +
          '<button type="button" class="dv-btn" data-dv="next" aria-label="Next page">&rsaquo;</button>' +
          '<span class="dv-sep"></span>' +
          '<button type="button" class="dv-btn" data-dv="out" aria-label="Zoom out">&minus;</button>' +
          '<button type="button" class="dv-btn" data-dv="fit" aria-label="Fit to width">Fit</button>' +
          '<button type="button" class="dv-btn" data-dv="in" aria-label="Zoom in">+</button>' +
          '<span class="dv-sep"></span>' +
          '<a class="dv-btn dv-save" data-dv-save hidden download>Save</a>' +
          '<span class="dv-lock" data-dv-lock hidden title="This document can be read here but not saved">View only</span>' +
          '<button type="button" class="dv-btn dv-close" data-dv="close" aria-label="Close">&times;</button>' +
        '</div>' +
      '</div>' +
      '<div class="dv-stage" data-dv-stage><div class="dv-load">Opening…</div></div>';
    document.body.appendChild(EL);

    EL.addEventListener('click', function (e) {
      var hit = e.target.closest('[data-dv]');
      if (hit) {
        var what = hit.dataset.dv;
        if (what === 'close') return close();
        if (what === 'prev') return goto(PAGE - 1);
        if (what === 'next') return goto(PAGE + 1);
        if (what === 'in') return zoom(ZOOM + 0.25);
        if (what === 'out') return zoom(ZOOM - 0.25);
        if (what === 'fit') return zoom(1);
        return;
      }
      // Clicking the backdrop closes; clicking the page itself does not.
      if (e.target === EL || e.target.hasAttribute('data-dv-stage')) close();
    });

    document.addEventListener('keydown', function (e) {
      if (!EL.classList.contains('open')) return;
      if (e.key === 'Escape') { close(); return; }
      if (DOC && DOC.pages > 1) {
        if (e.key === 'ArrowRight' || e.key === 'PageDown') { goto(PAGE + 1); e.preventDefault(); }
        if (e.key === 'ArrowLeft' || e.key === 'PageUp') { goto(PAGE - 1); e.preventDefault(); }
      }
    });
    return EL;
  }

  function $(sel) { return EL.querySelector(sel); }

  function pageUrl(n) {
    return DOC.page_url.replace('{n}', n);
  }

  function goto(n) {
    /* Anything with more than one page can be paged: a PDF by page, a workbook
       by sheet. This asked whether the file was a PDF, so a seven-sheet
       workbook had its arrows disabled on sheet one. */
    if (!DOC || DOC.pages <= 1) return;
    if (n < 0 || n >= DOC.pages) return;
    PAGE = n;
    draw();
  }

  function zoom(z) {
    ZOOM = Math.max(0.5, Math.min(z, 3));
    applyZoom();
    $('[data-dv="fit"]').classList.toggle('on', ZOOM === 1);
  }

  function draw() {
    var stage = $('[data-dv-stage]');
    var url = DOC.pages > 1 ? pageUrl(PAGE) : pageUrl(0);
    stage.innerHTML = '<div class="dv-load">Loading…</div>';

    if (DOC.as_html) { drawHtml(stage, url); } else { drawImage(stage, url); }

    /* A workbook's sheets have names, and "Summary" says more than "2 / 7". */
    var label = '';
    if (DOC.pages > 1) {
      var named = DOC.labels && DOC.labels[PAGE];
      label = named ? named + '  ' + (PAGE + 1) + ' / ' + DOC.pages
                    : (PAGE + 1) + ' / ' + DOC.pages;
    }
    $('[data-dv-pages]').textContent = label;
    $('[data-dv="prev"]').disabled = DOC.pages <= 1 || PAGE === 0;
    $('[data-dv="next"]').disabled = DOC.pages <= 1 || PAGE >= DOC.pages - 1;
  }

  function drawImage(stage, url) {
    var img = new Image();
    img.alt = DOC.title + (DOC.pages > 1 ? ' — page ' + (PAGE + 1) : '');
    img.onload = function () {
      stage.innerHTML = '';
      stage.appendChild(img);
      img.style.width = (ZOOM * 100) + '%';
      stage.scrollTop = 0;
    };
    img.onerror = function () {
      stage.innerHTML = '<div class="dv-load dv-err">This page could not be opened.</div>';
    };
    img.src = url;
  }

  /* A sheet or a Word document arrives as markup rather than a picture. The
     server builds it from escaped values with tags of its own — nothing from the
     uploaded file is passed through as markup — which is what makes putting it
     in the page safe, and what keeps the text selectable and findable with the
     browser's own search. */
  function drawHtml(stage, url) {
    fetch(url, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(function (r) { if (!r.ok) throw new Error('page'); return r.text(); })
      .then(function (markup) {
        stage.innerHTML = '<div class="dv-html" data-dv-html>' + markup + '</div>';
        applyZoom();
        stage.scrollTop = 0;
        stage.scrollLeft = 0;
      })
      .catch(function () {
        stage.innerHTML = '<div class="dv-load dv-err">This page could not be opened.</div>';
      });
  }

  /* Zoom scales the text rather than the pixels: these pages are text, and
     doubling a font is legible where doubling a bitmap of one is not. */
  function applyZoom() {
    var box = $('[data-dv-html]');
    if (box) { box.style.fontSize = (ZOOM * 100) + '%'; }
    var img = $('[data-dv-stage] img');
    if (img) { img.style.width = (ZOOM * 100) + '%'; }
  }

  function open(endpoint) {
    build();
    LAST_FOCUS = document.activeElement;
    EL.classList.add('open');
    document.body.classList.add('dv-lock-scroll');
    $('[data-dv-stage]').innerHTML = '<div class="dv-load">Opening…</div>';
    $('[data-dv-title]').textContent = '';
    $('[data-dv-sub]').textContent = '';

    fetch(endpoint, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(function (r) {
        if (r.status === 404 || r.status === 403) throw new Error('denied');
        return r.json();
      })
      .then(function (d) {
        DOC = d; PAGE = 0; ZOOM = 1;
        $('[data-dv-title]').textContent = d.title || d.filename || 'Document';
        $('[data-dv-sub]').textContent = d.subtitle || '';
        var save = $('[data-dv-save]');
        var lock = $('[data-dv-lock]');
        save.hidden = !d.may_download;
        lock.hidden = !!d.may_download;
        if (d.may_download) {
          save.href = d.download_url;
          save.setAttribute('download', d.filename || '');
        } else {
          save.removeAttribute('href');
        }
        if (!d.pages) {
          $('[data-dv-stage]').innerHTML =
            '<div class="dv-load dv-err">This document cannot be previewed.' +
            (d.may_download ? ' Use Save to open it in another application.' : '') +
            '</div>';
          $('[data-dv-pages]').textContent = '';
          return;
        }
        draw();
        $('.dv-close').focus();
      })
      .catch(function () {
        $('[data-dv-stage]').innerHTML =
          '<div class="dv-load dv-err">This document is not available to you.</div>';
      });
  }

  function close() {
    if (!EL) return;
    EL.classList.remove('open');
    document.body.classList.remove('dv-lock-scroll');
    $('[data-dv-stage]').innerHTML = '';
    DOC = null;
    if (LAST_FOCUS && LAST_FOCUS.focus) LAST_FOCUS.focus();
  }

  /* Anything on any page can open a document by carrying the endpoint:
         <a data-doc="/policies/doc/12/">Read</a>                            */
  document.addEventListener('click', function (e) {
    var trigger = e.target.closest('[data-doc]');
    if (!trigger) return;
    e.preventDefault();
    open(trigger.getAttribute('data-doc'));
  });

  window.AJIDoc = {open: open, close: close};
})();
