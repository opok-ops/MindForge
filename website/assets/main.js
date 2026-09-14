/* ============================================================
   MindForge — assets/main.js
   原生 JS · IIFE 隔离作用域 · 无粒子动画 · 无第三方依赖
   模块
   01 导航：吸顶态 / 抽屉菜单 / 锚点高亮
   02 代码复制
   03 版本号与统计数字注入
   ============================================================ */

(function () {
  'use strict';

  var hasIO = typeof window.IntersectionObserver === 'function';

  function qs(selector, scope) {
    return (scope || document).querySelector(selector);
  }

  function qsa(selector, scope) {
    return Array.prototype.slice.call((scope || document).querySelectorAll(selector));
  }

  /* ============================ 01 导航 ============================ */

  /** 吸顶态：用哨兵元素的 IntersectionObserver 代替 scroll 监听。 */
  function initNavScrollState() {
    var nav = qs('.nav');
    var sentinel = qs('#nav-sentinel');
    if (!nav || !sentinel || !hasIO) { return; }

    var io = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) {
        nav.classList.remove('is-scrolled');
      } else {
        nav.classList.add('is-scrolled');
      }
    }, { threshold: 0 });
    io.observe(sentinel);
  }

  /** 移动端抽屉菜单。 */
  function initNavDrawer() {
    var toggle = qs('#nav-toggle');
    var drawer = qs('#nav-drawer');
    if (!toggle || !drawer) { return; }

    var isOpen = false;

    function setOpen(next) {
      isOpen = next;
      toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      drawer.classList.toggle('is-open', isOpen);
      drawer.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
    }

    toggle.addEventListener('click', function () { setOpen(!isOpen); });

    qsa('a[href^="#"]', drawer).forEach(function (link) {
      link.addEventListener('click', function () { setOpen(false); });
    });

    document.addEventListener('keydown', function (evt) {
      if (evt.key === 'Escape' && isOpen) { setOpen(false); toggle.focus(); }
    });

    document.addEventListener('click', function (evt) {
      if (!isOpen) { return; }
      if (drawer.contains(evt.target) || toggle.contains(evt.target)) { return; }
      setOpen(false);
    });

    setOpen(false);
  }

  /** 当前区块对应的导航锚点高亮。 */
  function initActiveAnchor() {
    var links = qsa('.nav-links a[href^="#"]');
    if (!links.length || !hasIO) { return; }

    var map = {};
    var targets = [];
    links.forEach(function (link) {
      var id = link.getAttribute('href').slice(1);
      if (!id) { return; }
      var section = document.getElementById(id);
      if (!section) { return; }
      map[id] = link;
      targets.push(section);
    });
    if (!targets.length) { return; }

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) { return; }
        var link = map[entry.target.id];
        if (!link) { return; }
        links.forEach(function (l) { l.classList.remove('is-active'); });
        link.classList.add('is-active');
      });
    }, { rootMargin: '-40% 0px -55% 0px', threshold: 0 });

    targets.forEach(function (t) { io.observe(t); });
  }

  /* ============================ 02 代码复制 ============================ */

  function initCopyButtons() {
    var buttons = qsa('[data-copy]');
    if (!buttons.length) { return; }

    buttons.forEach(function (btn) {
      var selector = btn.getAttribute('data-copy');
      var target = selector ? qs(selector) : null;
      if (!target) { return; }

      var label = btn.querySelector('.copy-label');
      var defaultText = label ? label.textContent : '';
      var timer = null;

      function flash(ok) {
        btn.classList.add('is-done');
        if (label) { label.textContent = ok ? '已复制' : '复制失败'; }
        if (timer) { window.clearTimeout(timer); }
        timer = window.setTimeout(function () {
          btn.classList.remove('is-done');
          if (label) { label.textContent = defaultText; }
        }, 1900);
      }

      btn.addEventListener('click', function () {
        var text = target.textContent || target.innerText || '';
        text = text.replace(/\s+$/, '');

        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () { flash(true); }, function () { flash(false); });
          return;
        }
        try {
          var ta = document.createElement('textarea');
          ta.value = text;
          ta.setAttribute('readonly', 'readonly');
          ta.style.position = 'fixed';
          ta.style.top = '-1000px';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          var ok = document.execCommand('copy');
          document.body.removeChild(ta);
          flash(ok);
        } catch (err) {
          flash(false);
        }
      });
    });
  }

  /* ============================ 03 版本与统计注入 ============================ */

  /** 从 JSON-LD 读取 softwareVersion / toolCount / moduleCount / testCount 并注入页面。 */
  function injectMeta() {
    var data = null;
    try {
      var ld = qs('script[type="application/ld+json"]');
      if (ld) { data = JSON.parse(ld.textContent); }
    } catch (e) { data = null; }
    if (!data) { return; }

    if (data.softwareVersion) {
      qsa('[data-ver]').forEach(function (el) {
        var v = data.softwareVersion;
        el.textContent = el.dataset.verTpl ? el.dataset.verTpl.replace('{v}', v) : 'v' + v;
      });
    }

    var nums = {
      toolCount: data.toolCount,
      moduleCount: data.moduleCount,
      testCount: data.testCount
    };
    qsa('[data-num]').forEach(function (el) {
      var key = el.dataset.num;
      if (nums[key] != null) { el.textContent = String(nums[key]); }
    });
  }

  /* ============================ 启动 ============================ */

  function boot() {
    initNavScrollState();
    initNavDrawer();
    initActiveAnchor();
    initCopyButtons();
    injectMeta();

    var yearEl = qs('#foot-year');
    if (yearEl) { yearEl.textContent = String(new Date().getFullYear()); }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
