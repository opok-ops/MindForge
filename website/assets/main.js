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

  /* ==========================================================
     顶栏搜索（取代原来的链接排 / 移动端抽屉）
     索引直接由页面 DOM 构建：板块 → 版本条目 → 代码示例
     ========================================================== */
  (function () {
    var root = document.getElementById('site-search');
    var input = document.getElementById('search-input');
    var panel = document.getElementById('search-panel');
    var list = document.getElementById('search-list');
    var scopeEl = document.getElementById('search-scope');
    var countEl = document.getElementById('search-count');
    var trigger = document.getElementById('search-trigger');
    var clearBtn = document.getElementById('search-clear');
    var scrim = document.getElementById('nav-scrim');
    var kbdMod = document.querySelector('#search-kbd .k-mod');
    if (!root || !input || !panel || !list) return;

    var items = [];
    var results = [];
    var active = -1;
    var currentId = '';
    var typingTimer = null;
    var isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || '');

    function collapse(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); }
    function esc(s) {
      return String(s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    }

    /* ---- 构建索引 ---- */
    function buildIndex() {
      items = [];

      document.querySelectorAll('#main section[id]').forEach(function (sec) {
        var tagEl = sec.querySelector('.tag');
        var h2 = sec.querySelector('h2');
        var lead = sec.querySelector('.section-head p');
        var label = collapse(tagEl ? tagEl.textContent : '') || collapse(h2 ? h2.textContent : '') || sec.id;
        var title = collapse(h2 ? h2.textContent : '');
        var sub = collapse(lead ? lead.textContent : '');
        var text = collapse(sec.textContent);
        items.push({
          type: 'zone', label: label, title: title, sub: sub.slice(0, 96), text: text,
          href: '#' + sec.id, el: sec,
          head: (label + ' ' + title).toLowerCase(),
          hay: (label + ' ' + title + ' ' + text).toLowerCase()
        });
      });

      document.querySelectorAll('#changelog .cl-item').forEach(function (item) {
        var ver = item.querySelector('.cl-ver');
        var date = item.querySelector('.cl-date');
        var kind = item.querySelector('.cl-kind');
        var h4 = item.querySelector('h4');
        var label = collapse(ver ? ver.textContent : '');
        var title = collapse(h4 ? h4.textContent : '');
        var meta = [collapse(kind ? kind.textContent : ''), collapse(date ? date.textContent : '')].filter(Boolean).join(' · ');
        var text = collapse(item.textContent);
        items.push({
          type: 'version', label: label, title: title, sub: meta, text: text,
          href: '#changelog', el: item,
          head: (label + ' ' + title).toLowerCase(),
          hay: (label + ' ' + title + ' ' + text).toLowerCase()
        });
      });

      document.querySelectorAll('#main pre code').forEach(function (code) {
        var sec = code.closest ? code.closest('section[id]') : null;
        var tagEl = sec ? sec.querySelector('.tag') : null;
        var zone = (tagEl && collapse(tagEl.textContent)) || '代码示例';
        var raw = collapse(code.textContent);
        if (!raw) return;
        items.push({
          type: 'code', label: '代码示例', title: '', sub: zone, code: raw,
          href: sec ? '#' + sec.id : '#quickstart', el: sec || null,
          head: zone.toLowerCase(),
          hay: (zone + ' 代码示例 ' + raw).toLowerCase()
        });
      });
    }

    /* ---- 打分：所有查询词都要命中，命中位置越靠前分越高 ---- */
    function score(it, terms) {
      var s = 0;
      for (var i = 0; i < terms.length; i++) {
        var t = terms[i];
        var at = it.hay.indexOf(t);
        if (at < 0) return -1;
        if (it.head.indexOf(t) === 0) s += 130;
        else if (it.head.indexOf(t) >= 0) s += 72;
        if (it.label.toLowerCase().indexOf(t) >= 0) s += 44;
        s += Math.max(0, 22 - Math.min(22, at / 26));
      }
      if (it.type === 'zone') s += 18;
      else if (it.type === 'version') s += 8;
      return s;
    }

    /* ---- 命中高亮（先算区间再转义，避免破坏标签） ---- */
    function hl(text, terms) {
      text = String(text || '');
      if (!terms.length) return esc(text);
      var low = text.toLowerCase(), ranges = [], i, from, idx;
      for (i = 0; i < terms.length; i++) {
        from = 0;
        while ((idx = low.indexOf(terms[i], from)) >= 0) {
          ranges.push([idx, idx + terms[i].length]);
          from = idx + terms[i].length;
          if (ranges.length > 40) break;
        }
      }
      if (!ranges.length) return esc(text);
      ranges.sort(function (a, b) { return a[0] - b[0]; });
      var merged = [], last = null;
      ranges.forEach(function (r) {
        if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
        else { last = [r[0], r[1]]; merged.push(last); }
      });
      var out = '', pos = 0;
      merged.forEach(function (r) {
        out += esc(text.slice(pos, r[0])) + '<mark>' + esc(text.slice(r[0], r[1])) + '</mark>';
        pos = r[1];
      });
      return out + esc(text.slice(pos));
    }

    function codeSnippet(it, terms) {
      return ctxSnippet(it.code, terms, 104);
    }

    /* 命中上下文摘要：定位到第一个命中词附近，保证高亮看得见 */
    function ctxSnippet(text, terms, len) {
      var flat = collapse(text);
      if (!terms.length) return flat.slice(0, len);
      var low = flat.toLowerCase(), at = -1;
      for (var i = 0; i < terms.length && at < 0; i++) at = low.indexOf(terms[i]);
      if (at < 0) return flat.slice(0, len);
      var start = Math.max(0, at - 26);
      return (start > 0 ? '… ' : '') + flat.slice(start, start + len);
    }

    function hitIn(text, terms) {
      var low = String(text || '').toLowerCase();
      for (var i = 0; i < terms.length; i++) if (low.indexOf(terms[i]) >= 0) return true;
      return false;
    }

    function iconFor(type) { return type === 'version' ? '#i-clock' : (type === 'code' ? '#i-terminal' : '#i-book'); }
    function chipFor(type) { return type === 'version' ? '版本' : (type === 'code' ? '代码' : '板块'); }
    function chipCls(type) { return type === 'version' ? ' t-version' : (type === 'code' ? ' t-code' : ''); }

    /* ---- 渲染 ---- */
    function render() {
      var q = collapse(input.value);
      var terms = q ? q.toLowerCase().split(' ').filter(Boolean) : [];

      if (!terms.length) {
        results = items.filter(function (it) { return it.type === 'zone'; });
        var cur = null;
        for (var i = 0; i < results.length; i++) if (results[i].href === '#' + currentId) cur = results[i];
        scopeEl.textContent = cur ? '快速跳转 · 当前 ' + cur.label : '快速跳转';
        countEl.textContent = results.length + ' 个板块';
      } else {
        var scored = [];
        items.forEach(function (it) {
          var s = score(it, terms);
          if (s >= 0) scored.push({ it: it, s: s });
        });
        scored.sort(function (a, b) { return b.s - a.s; });
        results = scored.slice(0, 8).map(function (r) { return r.it; });
        scopeEl.textContent = '搜索结果';
        countEl.textContent = results.length + ' 条';
      }

      if (!results.length) {
        list.innerHTML = '<li class="search-empty">没有匹配 <b>' + esc(q) + '</b> 的内容<br>试试「加密」「召回」「5.6.9」「MCP」</li>';
        active = -1;
        input.removeAttribute('aria-activedescendant');
        return;
      }

      var html = '';
      results.forEach(function (it, i) {
        var main, sub;
        if (it.type === 'code') {
          main = '<span class="si-label">' + esc(it.label) + '<span class="si-chip' + chipCls(it.type) + '">' + chipFor(it.type) + '</span></span>';
          sub = '<code>' + hl(codeSnippet(it, terms), terms) + '</code>';
        } else {
          main = '<span class="si-label">' + hl(it.label, terms) + '<span class="si-chip' + chipCls(it.type) + '">' + chipFor(it.type) + '</span></span>';
          var cand;                                  // 命中在哪就摘要哪一段
          if (!terms.length || hitIn(it.title, terms)) cand = it.title;
          else if (hitIn(it.sub, terms)) cand = it.sub;
          else cand = ctxSnippet(it.text, terms, 92);
          sub = hl(cand, terms);
        }
        html += '<li class="search-item' + (it.href === '#' + currentId ? ' is-current' : '') + '"'
          + ' role="option" id="search-opt-' + i + '" aria-selected="false" data-idx="' + i + '"'
          + ' style="--i:' + i + '">'
          + '<span class="si-icon"><svg class="icon" aria-hidden="true"><use href="' + iconFor(it.type) + '"/></svg></span>'
          + '<span class="si-main">' + main + '<span class="si-sub">' + sub + '</span></span>'
          + '<span class="si-go"><svg class="icon" aria-hidden="true"><use href="#i-arrow"/></svg></span>'
          + '</li>';
      });
      list.innerHTML = html;
      setActive(-1);
    }

    function setActive(i) {
      var lis = list.querySelectorAll('.search-item');
      if (!lis.length) { active = -1; return; }
      if (i < 0) {                                 // 清除选中
        active = -1;
        Array.prototype.forEach.call(lis, function (li) {
          li.classList.remove('is-active');
          li.setAttribute('aria-selected', 'false');
        });
        input.removeAttribute('aria-activedescendant');
        return;
      }
      if (i >= lis.length) i = 0;
      active = i;
      Array.prototype.forEach.call(lis, function (li, k) {
        var on = k === active;
        li.classList.toggle('is-active', on);
        li.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      var cur = lis[active];
      if (cur) {
        if (cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
        input.setAttribute('aria-activedescendant', cur.id);
      }
    }

    /* ---- 打开 / 关闭 ---- */
    function setOpen(open) {
      root.classList.toggle('is-open', open);
      panel.hidden = !open;
      input.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (trigger) trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (scrim) scrim.classList.toggle('show', open);
      if (open) {
        render();
      } else {
        active = -1;
        root.classList.remove('open');
        input.removeAttribute('aria-activedescendant');
      }
    }
    function isOpen() { return root.classList.contains('is-open'); }

    /* ---- 跳转（带目标定位闪烁） ---- */
    function go(it) {
      if (!it) return;
      var el = it.el || (it.href ? document.querySelector(it.href) : null);
      setOpen(false);
      input.blur();
      if (it.href && window.history && history.replaceState) {
        history.replaceState(null, '', it.href);
      }
      if (el && el.scrollIntoView) {
        el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
        if (!reduceMotion) {
          el.classList.remove('sr-target');
          void el.offsetWidth;
          el.classList.add('sr-target');
          setTimeout(function () { el.classList.remove('sr-target'); }, 1500);
        }
      }
    }

    /* ---- 输入 & 键盘 ---- */
    input.addEventListener('focus', function () { setOpen(true); });
    input.addEventListener('input', function () {
      if (!isOpen()) setOpen(true); else render();
      if (reduceMotion) return;
      root.classList.remove('is-typing');
      void root.offsetWidth;
      root.classList.add('is-typing');
      clearTimeout(typingTimer);
      typingTimer = setTimeout(function () { root.classList.remove('is-typing'); }, 1100);
    });

    input.addEventListener('keydown', function (e) {
      var k = e.key;
      if (k === 'ArrowDown') { e.preventDefault(); setActive(active + 1); }
      else if (k === 'ArrowUp') {
        e.preventDefault();
        var n = list.querySelectorAll('.search-item').length;
        setActive(active - 1 < 0 ? n - 1 : active - 1);
      } else if (k === 'Enter') {
        e.preventDefault();
        if (active >= 0 && results[active]) go(results[active]);
        else if (results[0]) go(results[0]);
      } else if (k === 'Escape') {
        e.stopPropagation();                      // 先清词，再关面板，避免与全局 Esc 抢
        if (input.value) { input.value = ''; render(); }
        else { setOpen(false); input.blur(); }
      } else if (k === 'Tab') {
        setOpen(false);
      }
    });

    list.addEventListener('mousedown', function (e) { if (e.target.closest) e.preventDefault(); });
    list.addEventListener('click', function (e) {
      var li = e.target.closest ? e.target.closest('.search-item') : null;
      if (!li) return;
      go(results[parseInt(li.getAttribute('data-idx'), 10)]);
    });

    if (clearBtn) clearBtn.addEventListener('click', function () {
      input.value = '';
      render();
      input.focus();
    });
    if (trigger) trigger.addEventListener('click', function () {
      root.classList.add('open');
      setOpen(true);
      input.focus();
    });
    if (scrim) scrim.addEventListener('click', function () { setOpen(false); });

    document.addEventListener('pointerdown', function (e) {
      if (isOpen() && !root.contains(e.target)) setOpen(false);
    });
    document.addEventListener('keydown', function (e) {
      var k = (e.key || '').toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === 'k') {
        e.preventDefault();
        if (isOpen()) { setOpen(false); input.blur(); }
        else { root.classList.add('open'); setOpen(true); input.focus(); input.select(); }
      } else if (k === 'escape' && isOpen()) {
        setOpen(false);
      }
    });

    /* ---- 指针追光：真实镜面高光 + 折射层微视差 ---- */
    if (!reduceMotion) {
      var raf = null, px = 0.5, py = 0.2, cx = 0.5, cy = 0.2;
      function tick() {
        cx += (px - cx) * 0.14;
        cy += (py - cy) * 0.14;
        root.style.setProperty('--mx', (cx * 100).toFixed(1) + '%');
        root.style.setProperty('--my', (cy * 100).toFixed(1) + '%');
        root.style.setProperty('--rx', ((cx - 0.5) * 9).toFixed(2) + 'px');
        root.style.setProperty('--ry', ((cy - 0.5) * 5).toFixed(2) + 'px');
        if (Math.abs(px - cx) > 0.002 || Math.abs(py - cy) > 0.002) raf = requestAnimationFrame(tick);
        else raf = null;
      }
      root.addEventListener('pointermove', function (e) {
        var r = root.getBoundingClientRect();
        px = (e.clientX - r.left) / r.width;
        py = (e.clientY - r.top) / r.height;
        if (!raf) raf = requestAnimationFrame(tick);
      });
      root.addEventListener('pointerleave', function () {
        px = 0.5; py = -0.3;
        if (!raf) raf = requestAnimationFrame(tick);
      });
    }

    /* ---- 滚动时记录当前板块（空查询时用于标注「当前」） ---- */
    if ('IntersectionObserver' in window) {
      var spy = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          if (currentId === en.target.id) return;
          currentId = en.target.id;
          if (isOpen() && !collapse(input.value)) render();
        });
      }, { rootMargin: '-45% 0px -50% 0px' });
      document.querySelectorAll('#main section[id]').forEach(function (s) { spy.observe(s); });
    }

    if (kbdMod) kbdMod.textContent = isMac ? '⌘' : 'Ctrl';
    buildIndex();
  })();

})();
