/* MindForge — minimal, dependency-free interactions.
   No frameworks, no external fonts. GPU-friendly only. */
(function () {
  'use strict';
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- Nav: scrolled state ---- */
  var nav = document.querySelector('.nav');
  function onScroll() {
    if (nav) nav.classList.toggle('scrolled', window.scrollY > 12);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ---- Mobile drawer ---- */
  var toggle = document.getElementById('nav-toggle');
  var drawer = document.getElementById('nav-drawer');
  if (toggle && drawer) {
    function setDrawer(open) {
      toggle.setAttribute('aria-expanded', String(open));
      drawer.classList.toggle('open', open);
      drawer.setAttribute('aria-hidden', String(!open));
    }
    toggle.addEventListener('click', function () {
      setDrawer(toggle.getAttribute('aria-expanded') !== 'true');
    });
    drawer.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function () { setDrawer(false); });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') setDrawer(false);
    });
  }

  /* ---- Copy buttons ---- */
  document.querySelectorAll('.copy-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var sel = btn.getAttribute('data-copy');
      var target = sel && document.querySelector(sel);
      if (!target) return;
      var text = target.innerText;
      var done = function () {
        var label = btn.querySelector('.copy-label');
        var prev = label ? label.textContent : '';
        btn.classList.add('copied');
        if (label) label.textContent = '已复制';
        setTimeout(function () {
          btn.classList.remove('copied');
          if (label) label.textContent = prev || '复制';
        }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () {});
      } else {
        var ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); done(); } catch (e) {}
        document.body.removeChild(ta);
      }
    });
  });

  /* ---- Reveal on scroll ---- */
  var revealEls = document.querySelectorAll('.reveal');
  if (reduceMotion || !('IntersectionObserver' in window)) {
    revealEls.forEach(function (el) { el.classList.add('in'); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('in');
          io.unobserve(entry.target);
        }
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    revealEls.forEach(function (el) { io.observe(el); });
  }

  /* ---- Active nav link on scroll ---- */
  var navLinks = Array.prototype.slice.call(document.querySelectorAll('.nav-links a'));
  var sections = navLinks
    .map(function (a) { return document.querySelector(a.getAttribute('href')); })
    .filter(Boolean);
  if (sections.length && 'IntersectionObserver' in window) {
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var id = entry.target.id;
          navLinks.forEach(function (a) {
            a.classList.toggle('active', a.getAttribute('href') === '#' + id);
          });
        }
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    sections.forEach(function (s) { spy.observe(s); });
  }

  /* ---- Liquid glass: interactive refraction + slow breathing ---- */
  (function () {
    var panel = document.querySelector('.liquid-glass');
    if (!panel) return;
    var refract = panel.querySelector('.lg-refract');
    var disp = document.getElementById('lgDisplace');

    if (!reduceMotion && disp) {
      var base = 38, t0 = Date.now();
      (function breathe() {
        var s = base + Math.sin((Date.now() - t0) / 2600) * 11;
        disp.setAttribute('scale', s.toFixed(1));
        setTimeout(breathe, 130);
      })();
    }

    if (reduceMotion || !refract) return;
    var tx = 0, ty = 0, cx = 0, cy = 0, raf = null;
    function loop() {
      cx += (tx - cx) * 0.09;
      cy += (ty - cy) * 0.09;
      refract.style.transform = 'translate3d(' + cx.toFixed(2) + 'px,' + cy.toFixed(2) + 'px,0) scale(1.06)';
      if (Math.abs(tx - cx) > 0.15 || Math.abs(ty - cy) > 0.15) {
        raf = requestAnimationFrame(loop);
      } else {
        refract.style.transform = 'scale(1.04)';
        raf = null;
      }
    }
    panel.addEventListener('pointermove', function (e) {
      var r = panel.getBoundingClientRect();
      tx = ((e.clientX - r.left) / r.width - 0.5) * 30;
      ty = ((e.clientY - r.top) / r.height - 0.5) * 20;
      if (!raf) raf = requestAnimationFrame(loop);
    });
    panel.addEventListener('pointerleave', function () {
      tx = 0; ty = 0;
      if (!raf) raf = requestAnimationFrame(loop);
    });
  })();

})();
