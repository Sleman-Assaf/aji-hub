/* photo_crop.js
   One picture cropper, for anywhere on the Hub that takes a photo.

   The profile page has had one of these since the start: pick a file, drag and
   zoom it inside a square, and what gets uploaded is the square rather than the
   original. It was written into that page, so a group picture in the chat had
   the choice of shipping a second copy of it or accepting whatever shape the file
   happened to be — and a 4000×3000 photo dropped into a 64-pixel circle is
   cropped by the browser, from the middle, with nobody's say in it.

   So it lives here, and it answers with a Blob rather than posting anything:

       ajiCrop.open(file, {size: 512}).then(blob => { … })

   Resolves with the cropped square, or with null if the reader backed out.
   Cropper.js does the dragging; this is the dialog around it, the promise, and
   the tidying up afterwards.

   Loads the library on demand. Most visits to the Hub never crop a picture, and
   Cropper is 60 KB that those visits should not pay for. */

(function () {
  'use strict';

  const CSS = '/static/vendor/cropperjs/cropper.min.css';
  const JS = '/static/vendor/cropperjs/cropper.min.js';

  let loading = null;

  /* The library, fetched once. Every later call gets the same promise, so two
     quick presses cannot start two downloads of the same script. */
  function library() {
    if (window.Cropper) return Promise.resolve(window.Cropper);
    if (loading) return loading;
    loading = new Promise((resolve, reject) => {
      if (!document.querySelector(`link[href="${CSS}"]`)) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = CSS;
        document.head.appendChild(link);
      }
      const s = document.createElement('script');
      s.src = JS;
      s.onload = () => resolve(window.Cropper);
      s.onerror = () => { loading = null; reject(new Error('cropper')); };
      document.head.appendChild(s);
    });
    return loading;
  }

  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  /* Crop `file` and resolve with a Blob.

     opts.size    the side of the square that comes out, in pixels (default 512)
     opts.round   draw the crop area as a circle — for a picture that will be
                  shown in a circle, so what you frame is what you get
     opts.title   the dialog's heading */
  function open(file, opts) {
    opts = opts || {};
    const size = opts.size || 512;

    return new Promise(resolve => {
      if (!file || !/^image\//.test(file.type)) { resolve(null); return; }

      const bg = el('div', 'crop-over');
      bg.setAttribute('data-chat-layer', '');       // the chat closes its own
      const box = el('div', 'crop-panel');          // popups on outside clicks
      box.innerHTML =
        '<div class="crop-head"><b>' +
        (opts.title || 'Position the picture') +
        '</b><span>Drag to move, scroll or use the slider to zoom.</span></div>' +
        '<div class="crop-stage' + (opts.round ? ' round' : '') + '"><img alt=""></div>' +
        '<div class="crop-foot">' +
          '<label class="crop-zoom">Zoom' +
            '<input type="range" min="0" max="100" value="0" aria-label="Zoom">' +
          '</label>' +
          '<span class="crop-sp"></span>' +
          '<button type="button" class="btn ghost sm" data-crop-cancel>Cancel</button>' +
          '<button type="button" class="btn y sm" data-crop-ok>Use this picture</button>' +
        '</div>';
      bg.appendChild(box);
      document.body.appendChild(bg);

      const img = box.querySelector('img');
      const zoom = box.querySelector('input[type=range]');
      const okBtn = box.querySelector('[data-crop-ok]');
      let cropper = null, done = false;

      /* The page must not scroll behind the dialog, and whatever the reader was
         on before has to be given back — including its scroll position, which is
         what `position:fixed` on the body would otherwise throw away. */
      const wasOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';

      function finish(blob) {
        if (done) return;
        done = true;
        if (cropper) cropper.destroy();
        bg.remove();
        document.body.style.overflow = wasOverflow;
        document.removeEventListener('keydown', onKey);
        resolve(blob);
      }

      function onKey(e) {
        if (e.key === 'Escape') { e.stopPropagation(); finish(null); }
      }
      document.addEventListener('keydown', onKey);

      bg.addEventListener('click', e => { if (e.target === bg) finish(null); });
      box.querySelector('[data-crop-cancel]').addEventListener('click', () => finish(null));

      okBtn.addEventListener('click', () => {
        if (!cropper) { finish(null); return; }
        okBtn.disabled = true;
        okBtn.textContent = 'Working…';
        const canvas = cropper.getCroppedCanvas({
          width: size, height: size, imageSmoothingQuality: 'high',
        });
        if (!canvas) { finish(null); return; }
        /* JPEG, and a white backdrop under it. A PNG of a photograph is several
           times the size for no visible gain, and JPEG has no transparency — so
           a cropped PNG with a transparent corner would come out with a black
           one unless something is painted underneath first. */
        const flat = document.createElement('canvas');
        flat.width = flat.height = size;
        const ctx = flat.getContext('2d');
        ctx.fillStyle = '#fff';
        ctx.fillRect(0, 0, size, size);
        ctx.drawImage(canvas, 0, 0, size, size);
        flat.toBlob(b => finish(b), 'image/jpeg', 0.9);
      });

      const reader = new FileReader();
      reader.onload = ev => {
        library().then(Cropper => {
          if (done) return;
          img.onload = () => {
            if (done) return;
            cropper = new Cropper(img, {
              aspectRatio: 1,
              viewMode: 1,
              dragMode: 'move',
              autoCropArea: 1,
              guides: false,
              center: false,
              highlight: false,
              background: false,
              cropBoxMovable: false,
              cropBoxResizable: false,
              toggleDragModeOnDblclick: false,
              /* The slider and the wheel are the same control, so moving one has
                 to move the other. The range runs 0–100 over the zoom Cropper
                 allows from its own starting fit, rather than over raw ratios,
                 which differ with every photograph's dimensions. */
              ready() {
                const base = cropper.getImageData().ratio;
                cropper._baseRatio = base;
                zoom.value = 0;
              },
              zoom(e) {
                const base = cropper._baseRatio;
                if (!base) return;
                const at = Math.min(100, Math.max(0,
                  Math.round(((e.detail.ratio / base) - 1) / 2 * 100)));
                if (String(at) !== zoom.value) zoom.value = at;
              },
            });
          };
          img.src = ev.target.result;
        }).catch(() => finish(null));
      };
      reader.onerror = () => finish(null);
      reader.readAsDataURL(file);

      zoom.addEventListener('input', () => {
        if (!cropper || !cropper._baseRatio) return;
        cropper.zoomTo(cropper._baseRatio * (1 + (Number(zoom.value) / 100) * 2));
      });
    });
  }

  window.ajiCrop = {open};
})();
