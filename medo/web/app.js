/**
 * MedoOffline — Interactive Offline Medical MCQ Web App
 * Dependency-free Vanilla JS, fully RTL and offline compatible.
 */

(function () {
  'use strict';

  // --- Constants & Utilities ---
  const LETTERS = ['الف', 'ب', 'ج', 'د', 'هـ'];
  
  function toPersianDigits(n) {
    if (n === null || n === undefined) return '';
    const fa = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹'];
    return String(n).replace(/\d/g, d => fa[d]);
  }

  /** Bytes as a short Persian size (the unit is chosen, not assumed). */
  function formatBytes(n) {
    const bytes = Math.max(0, Number(n) || 0);
    if (bytes >= 1024 * 1024) {
      const mb = (bytes / (1024 * 1024)).toFixed(1).replace('.', '٫');
      return toPersianDigits(mb) + ' مگابایت';
    }
    if (bytes >= 1024) return toPersianDigits(Math.round(bytes / 1024)) + ' کیلوبایت';
    return toPersianDigits(Math.round(bytes)) + ' بایت';
  }

  // --- State ---
  const state = {
    questions: [],
    filteredIndices: [],
    currentIndex: 0,
    userAnswers: {},   // qid -> selectedOptionIndex (1-based)
    bookmarks: new Set(),
    activeFilter: 'all', // 'all' | 'answered' | 'bookmarked'
    searchQuery: '',
    // v2 key: the light, site-matching theme is the default, so a theme
    // chosen by an older build must not override it.
    theme: localStorage.getItem('medo_theme_v2') || 'theme-light',
    tree: [],            // category hierarchy from MEDO_TREE / categories.json / /api/tree
    // {type:'topic'|'group'|'subtopic', ids:Set<number>, tag?, key, label}
    treeFilter: null,
    expandedKeys: new Set(),
    treeSearch: '',
    // 'mine' = only categories that actually hold downloaded questions,
    // 'all'  = the site's full tree (for downloading something new).
    treeMode: localStorage.getItem('medo_tree_mode') || 'mine',
    downloading: false,
    usage: null,        // last /api/scrape/status .usage (measured traffic)
    // One folded haystack per question, built once at load (see
    // normalizeSearch): searching used to re-lowercase every lesson on every
    // keystroke and still missed Arabic spellings and Persian digits.
    searchIndex: new Map(),
  };

  // The shared canonical form (medo/web/normalize.js, matched by Python and
  // Android).  Falls back to a plain lowercase when the file is missing, e.g.
  // in a very old exported bundle.
  const normalizeSearch = window.medoNormalizeSearch || function (value) {
    return String(value === null || value === undefined ? '' : value).toLowerCase();
  };

  // --- DOM Elements ---
  const elements = {
    topicBadge: document.getElementById('topicBadge'),
    indexBadge: document.getElementById('indexBadge'),
    statusBadge: document.getElementById('statusBadge'),
    bookmarkBtn: document.getElementById('bookmarkBtn'),
    resetAnswerBtn: document.getElementById('resetAnswerBtn'),
    stemContent: document.getElementById('stemContent'),
    optionsContainer: document.getElementById('optionsContainer'),
    explanationBox: document.getElementById('explanationBox'),
    explanationHeader: document.getElementById('explanationHeader'),
    explanationContent: document.getElementById('explanationContent'),
    prevBtn: document.getElementById('prevBtn'),
    nextBtn: document.getElementById('nextBtn'),
    jumpSlider: document.getElementById('jumpSlider'),
    paletteGrid: document.getElementById('paletteGrid'),
    scoreStat: document.getElementById('scoreStat'),
    countAll: document.getElementById('countAll'),
    countAnswered: document.getElementById('countAnswered'),
    countBookmarked: document.getElementById('countBookmarked'),
    searchInput: document.getElementById('searchInput'),
    clearSearchBtn: document.getElementById('clearSearchBtn'),
    paletteSidebar: document.getElementById('paletteSidebar'),
    paletteToggleBtn: document.getElementById('paletteToggleBtn'),
    paletteBackdrop: document.getElementById('paletteBackdrop'),
    themeToggleBtn: document.getElementById('themeToggleBtn'),
    treeToggleBtn: document.getElementById('treeToggleBtn'),
    treeSidebar: document.getElementById('treeSidebar'),
    treeScroll: document.getElementById('treeScroll'),
    treeClearBtn: document.getElementById('treeClearBtn'),
    treeSearch: document.getElementById('treeSearch'),
    treeCloseBtn: document.getElementById('treeCloseBtn'),
    treeBackdrop: document.getElementById('treeBackdrop'),
    treeSummary: document.getElementById('treeSummary'),
    treeActiveFilter: document.getElementById('treeActiveFilter'),
    treeFilterValue: document.getElementById('treeFilterValue'),
    treeTabs: document.querySelectorAll('.tree-tab'),
    treeMineCount: document.getElementById('treeMineCount'),
    scrapeStatsText: document.getElementById('scrapeStatsText'),
    dlDockStats: document.getElementById('dlDockStats'),
    resumeScrapeBtn: document.getElementById('resumeScrapeBtn'),
    breadcrumbBar: document.getElementById('breadcrumbBar'),
    imageModal: document.getElementById('imageModal'),
    modalImage: document.getElementById('modalImage'),
    modalBackdrop: document.getElementById('modalBackdrop'),
    modalCloseBtn: document.getElementById('modalCloseBtn'),
    // Scraper Modal elements
    downloadModalBtn: document.getElementById('downloadModalBtn'),
    descOpener: document.getElementById('downloadModalBtn'),
    scraperModal: document.getElementById('scraperModal'),
    scraperBackdrop: document.getElementById('scraperBackdrop'),
    scraperCloseBtn: document.getElementById('scraperCloseBtn'),
    authDot: document.getElementById('authDot'),
    authStatusText: document.getElementById('authStatusText'),
    loginHelperBtn: document.getElementById('loginHelperBtn'),
    topicInput: document.getElementById('topicInput'),
    topicSelect: document.getElementById('topicSelect'),
    treeRefreshBtn: document.getElementById('treeRefreshBtn'),
    resumeCheckbox: document.getElementById('resumeCheckbox'),
    resumeHint: document.getElementById('resumeHint'),
    allQuestionsCheckbox: document.getElementById('allQuestionsCheckbox'),
    maxPagesHintWrap: document.getElementById('maxPagesHintWrap'),
    startPageInput: document.getElementById('startPageInput'),
    maxPagesInput: document.getElementById('maxPagesInput'),
    startScrapeBtn: document.getElementById('startScrapeBtn'),
    stopScrapeBtn: document.getElementById('stopScrapeBtn'),
    scrapeProgressBox: document.getElementById('scrapeProgressBox'),
    scrapeProgressBar: document.getElementById('scrapeProgressBar'),
    scrapeStatusText: document.getElementById('scrapeStatusText'),
    scrapeCountText: document.getElementById('scrapeCountText'),
    scrapeLogTerminal: document.getElementById('scrapeLogTerminal'),
    scrapeUsageLive: document.getElementById('scrapeUsageLive'),
    scrapeUsageSub: document.getElementById('scrapeUsageSub'),
    usageEstimateText: document.getElementById('usageEstimateText'),
    scraperMinBtn: document.getElementById('scraperMinBtn'),
    // Minimised download dock
    dlDock: document.getElementById('dlDock'),
    dlDockDot: document.getElementById('dlDockDot'),
    dlDockTitle: document.getElementById('dlDockTitle'),
    dlDockBar: document.getElementById('dlDockBar'),
    dlDockCount: document.getElementById('dlDockCount'),
    dlDockPage: document.getElementById('dlDockPage'),
    dlDockStopBtn: document.getElementById('dlDockStopBtn'),
    dlDockResumeBtn: document.getElementById('dlDockResumeBtn'),
    dlDockOpenBtn: document.getElementById('dlDockOpenBtn'),
    dlDockHideBtn: document.getElementById('dlDockHideBtn'),
    // Bank health / repair
    bankHealthText: document.getElementById('bankHealthText'),
    auditAnswersBtn: document.getElementById('auditAnswersBtn'),
    syncTagsBtn: document.getElementById('syncTagsBtn'),
    doctorBtn: document.getElementById('doctorBtn'),
    pdfBtn: document.getElementById('pdfBtn'),
    // Session strip
    sessionHeadline: document.getElementById('sessionHeadline'),
    sessionFill: document.getElementById('sessionFill'),
    sessionAccuracy: document.getElementById('sessionAccuracy'),
    // Phone bridge modal
    phoneBtn: document.getElementById('phoneBtn'),
    phoneModal: document.getElementById('phoneModal'),
    phoneBackdrop: document.getElementById('phoneBackdrop'),
    phoneCloseBtn: document.getElementById('phoneCloseBtn'),
    phoneDot: document.getElementById('phoneDot'),
    phoneStatusText: document.getElementById('phoneStatusText'),
    phoneStatusMeta: document.getElementById('phoneStatusMeta'),
    phoneUrls: document.getElementById('phoneUrls'),
    phoneStartBtn: document.getElementById('phoneStartBtn'),
    phoneStopBtn: document.getElementById('phoneStopBtn'),
    phoneRefreshBankBtn: document.getElementById('phoneRefreshBankBtn'),
    phoneCopyAdbBtn: document.getElementById('phoneCopyAdbBtn'),
    phoneBankDownload: document.getElementById('phoneBankDownload'),
    phoneApkInfo: document.getElementById('phoneApkInfo'),
    phoneApkDownload: document.getElementById('phoneApkDownload'),
    phoneApkQr: document.getElementById('phoneApkQr'),
    phoneQrCaption: document.getElementById('phoneQrCaption'),
    phoneBuildBtn: document.getElementById('phoneBuildBtn'),
    phoneLog: document.getElementById('phoneLog'),
  };

  // --- Initialization ---
  async function init() {
    applyTheme(state.theme);
    loadSavedUserSession();
    bindEvents();
    // The archive is read over HTTP: show the skeleton at once instead of an
    // empty card that looks broken until the request lands.
    showLoadingState();
    loadTree();
    await loadQuestions();
    // The header badge must reflect the real session on load, not stay on
    // «در حال بررسی…» until the download modal is opened.
    refreshAuthStatus();
    // A download survives a reload/restart on the server; surface it instead
    // of hiding it until the modal is opened again.
    refreshScrapeState().then(st => {
      if (st && st.is_running) {
        showDock();
        startScrapePolling();
      }
    });
  }

  function applyTheme(theme) {
    // classList, not className: body also carries `tree-open` / state classes
    // and a theme switch must not silently close the drawer or reset them.
    document.body.classList.remove('theme-light', 'theme-dark');
    document.body.classList.add(theme);
    localStorage.setItem('medo_theme_v2', theme);

    // Keep the phone's status bar / window chrome in step with the theme.
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute('content', theme === 'theme-dark' ? '#14181f' : '#f6f8fb');
    }
  }

  // --- App-level states ---------------------------------------------------

  const STATE_ICONS = {
    error: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 3l18 18"/><path d="M5.5 12.5A8 8 0 0 0 12 20a8 8 0 0 0 6-2.6"/><path d="M12 4a8 8 0 0 1 6.4 3.2"/></svg>',
    empty: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 5h16v10H4z"/><path d="M4 19h16"/><path d="M9 9h6"/></svg>'
  };

  /**
   * Shared chrome for "there is no question to show": park pagination and
   * answers, then put one honest, actionable card in place of the stem.
   */
  function paintState(node) {
    elements.stemContent.innerHTML = '';
    if (node) elements.stemContent.appendChild(node);
    elements.optionsContainer.innerHTML = '';
    elements.optionsContainer.style.display = 'none';
    elements.explanationBox.style.display = 'none';
    elements.breadcrumbBar.style.display = 'none';
    elements.prevBtn.disabled = true;
    elements.nextBtn.disabled = true;
    elements.jumpSlider.disabled = true;
    elements.jumpSlider.max = 1;
    elements.jumpSlider.value = 1;
    elements.bookmarkBtn.disabled = true;
    elements.resetAnswerBtn.disabled = true;
    elements.indexBadge.textContent = '—';
    elements.statusBadge.textContent = '—';
    elements.statusBadge.classList.remove('answered');
  }

  function stateCard(kind, opts) {
    const wrap = document.createElement('div');
    wrap.className = 'state-card state-' + kind;

    if (kind === 'loading') {
      wrap.setAttribute('aria-busy', 'true');
      wrap.innerHTML =
        '<div class="state-skeleton" aria-hidden="true">'
        + '<div class="sk-line w-70"></div>'
        + '<div class="sk-line w-90"></div>'
        + '<div class="sk-line w-90"></div>'
        + '<div class="sk-line w-55"></div>'
        + '</div>'
        + '<p class="state-body">در حال خواندن بانک سوالات…</p>';
      return wrap;
    }

    wrap.innerHTML =
      '<div class="state-icon" aria-hidden="true">' + (STATE_ICONS[kind] || '') + '</div>'
      + '<div class="state-title"></div>'
      + '<p class="state-body"></p>';
    wrap.querySelector('.state-title').textContent = opts.title;
    wrap.querySelector('.state-body').textContent = opts.body;

    if (opts.note) {
      const note = document.createElement('p');
      note.className = 'state-note';
      note.innerHTML = opts.note;
      wrap.appendChild(note);
    }

    if (opts.actions && opts.actions.length) {
      const row = document.createElement('div');
      row.className = 'state-actions';
      opts.actions.forEach(action => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn ' + (action.primary ? 'btn-primary' : 'btn-secondary');
        btn.textContent = action.label;
        btn.addEventListener('click', action.onClick);
        row.appendChild(btn);
      });
      wrap.appendChild(row);
    }

    return wrap;
  }

  function showLoadingState() {
    paintState(stateCard('loading'));
    elements.topicBadge.textContent = 'بانک سوالات';
    if (elements.sessionHeadline) {
      elements.sessionHeadline.textContent = 'در حال خواندن بانک…';
      elements.sessionFill.style.width = '0%';
      elements.sessionAccuracy.textContent = '—';
    }
  }

  function showEmptyState(options) {
    const serverDown = !!(options && options.serverDown);
    const retry = { label: 'تلاش دوباره', onClick: () => location.reload() };

    const node = serverDown
      ? stateCard('error', {
          title: 'سرور محلی پاسخ نمی‌دهد',
          body: 'این صفحه از نرم‌افزار باز شده، اما به سرور محلی وصل نشد. '
              + 'اگر پنجرهٔ اجرای برنامه را بسته‌ای، دوباره بازش کن و این صفحه را نوسازی کن.',
          note: 'اجرای دستی: <code>medo serve</code>',
          actions: [
            { label: retry.label, primary: true, onClick: retry.onClick },
            { label: 'تنظیمات دانلود', onClick: openScraperModal }
          ]
        })
      : stateCard('empty', {
          title: 'هنوز سوالی ذخیره نشده',
          body: 'بانک سوالات روی این دستگاه خالی است. می‌توانی همین حالا از مدوفست دانلود کنی، '
              + 'یا یک فایل بانک آماده را کنار برنامه بگذاری.',
          note: 'در ترمینال: <code>medo scrape --topic 79</code>',
          actions: [
            { label: 'دانلود سوالات', primary: true, onClick: openScraperModal },
            retry
          ]
        });

    paintState(node);
    elements.topicBadge.textContent = serverDown ? 'بدون اتصال' : 'بانک خالی';
    elements.countAll.textContent = '۰';
    elements.countAnswered.textContent = '۰';
    elements.countBookmarked.textContent = '۰';
    elements.scoreStat.textContent = '۰٪';
    elements.paletteGrid.innerHTML = '';
    if (elements.sessionHeadline) {
      elements.sessionHeadline.textContent = serverDown
        ? 'بدون اتصال به سرور' : 'بانک خالی است';
      elements.sessionFill.style.width = '0%';
      elements.sessionAccuracy.textContent = '۰٪';
    }
  }

  // --- Phone bridge (Wi-Fi / USB transfer + APK updates) ------------------
  let phoneTimer = null;
  let phoneFlashTimer = null;

  function openPhoneModal() {
    elements.phoneModal.classList.add('active');
    document.body.classList.add('phone-open');
    refreshPhone();
    if (phoneTimer) clearInterval(phoneTimer);
    phoneTimer = setInterval(refreshPhone, 4000);
  }

  function closePhoneModal() {
    elements.phoneModal.classList.remove('active');
    document.body.classList.remove('phone-open');
    if (phoneTimer) {
      clearInterval(phoneTimer);
      phoneTimer = null;
    }
  }

  async function refreshPhone() {
    try {
      const resp = await fetch('/api/phone', { cache: 'no-store' });
      if (!resp.ok) throw new Error('bad status');
      renderPhone(await resp.json());
    } catch (e) {
      elements.phoneDot.classList.remove('on');
      elements.phoneStatusText.textContent = 'سرور محلی پاسخ نمی‌دهد';
      elements.phoneStatusMeta.textContent = '';
      elements.phoneUrls.innerHTML =
        '<p class="phone-empty">برای انتقال، برنامه را از میانبر اصلی باز کن.</p>';
      elements.phoneStartBtn.disabled = true;
    }
  }

  async function phoneAction(path) {
    try {
      const resp = await fetch(path, { method: 'POST' });
      const data = await resp.json();
      if (data.phone) renderPhone(data.phone);
      flashPhone(data.message || 'انجام شد.', data.ok !== false);
    } catch (e) {
      flashPhone('ارتباط با سرور محلی برقرار نشد.', false);
    }
  }

  function flashPhone(text, ok) {
    elements.phoneStatusMeta.textContent = text;
    elements.phoneStatusMeta.style.color =
      ok === false ? 'var(--accent-wrong)' : 'var(--accent-success)';
    if (phoneFlashTimer) clearTimeout(phoneFlashTimer);
    phoneFlashTimer = setTimeout(() => {
      elements.phoneStatusMeta.style.color = '';
      refreshPhone();
    }, 2800);
  }

  function formatWhen(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    const time = String(d.getHours()).padStart(2, '0') + ':'
      + String(d.getMinutes()).padStart(2, '0');
    return toPersianDigits(d.toLocaleDateString('fa-IR')) + ' · ' + toPersianDigits(time);
  }

  function copyText(text, btn) {
    const done = () => {
      if (!btn) return;
      const old = btn.textContent;
      btn.textContent = 'کپی شد';
      setTimeout(() => { btn.textContent = old; }, 1400);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
    }
  }

  function fallbackCopy(text, done) {
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand('copy');
      done();
    } catch (e) { /* clipboard unavailable */ }
    document.body.removeChild(area);
  }

  let phoneQrUrl = '';
  let phoneQrPinned = '';
  let phoneBaseUrl = '';
  let phoneQrIsApk = false;

  /** The QR must follow the *live* address, not the one from page load. */
  function renderQr(url) {
    const wrap = document.getElementById('phoneQrWrap');
    const box = document.getElementById('phoneQr');
    if (!wrap || !box) return;
    if (!url || typeof window.qrcode !== 'function') {
      wrap.hidden = true;
      return;
    }
    if (url === phoneQrUrl && box.firstChild) {
      wrap.hidden = false;
      return;
    }
    try {
      const qr = window.qrcode(0, 'M');
      qr.addData(url);
      qr.make();
      // A 4-module quiet zone (the QR spec) and no baked-in pixel size, so the
      // CSS can render it large enough for a phone camera across a desk.
      box.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 16, scalable: true });
      const label = document.getElementById('phoneQrUrl');
      if (label) label.textContent = url;
      if (elements.phoneQrCaption) {
        elements.phoneQrCaption.textContent = phoneQrIsApk
          ? 'با دوربین گوشی اسکن کن؛ نسخهٔ جدید برنامه دانلود و نصب می‌شود.'
          : 'با دوربین گوشی اسکن کن؛ بانک تازه همان‌جا دریافت می‌شود.';
      }
      phoneQrUrl = url;
      wrap.hidden = false;
    } catch (e) {
      wrap.hidden = true;
    }
  }

  function renderPhone(info) {
    const running = !!info.running;
    elements.phoneDot.classList.toggle('on', running);
    elements.phoneStatusText.textContent = running
      ? `آمادهٔ انتقال — ${toPersianDigits(info.questions || 0)} سوال`
      : 'انتقال وای‌فای خاموش است';
    elements.phoneStatusMeta.textContent = running
      ? `پورت ${toPersianDigits(info.port)}`
        + (info.started_at ? ` · از ${toPersianDigits(info.started_at)}` : '')
      : '';
    elements.phoneStartBtn.hidden = running;
    elements.phoneStopBtn.hidden = !running;
    elements.phoneStartBtn.disabled = false;

    // Show every LAN address: a laptop on Wi-Fi + VPN has several, and only
    // the one on the phone's network will work. The list is live on each poll.
    const port = info.port;
    const allLan = (info.lan && info.lan.length)
      ? info.lan.map(ip => `http://${ip}:${port}`)
      : (info.suggested_url ? [info.suggested_url] : []);
    const urls = running ? ((info.urls && info.urls.length) ? info.urls : allLan) : allLan;
    elements.phoneUrls.innerHTML = '';
    if (!urls.length) {
      elements.phoneUrls.innerHTML =
        '<p class="phone-empty">آدرس شبکه پیدا نشد؛ از اتصال وای‌فای مطمئن شو.</p>';
    }
    urls.forEach((url, index) => {
      const row = document.createElement('div');
      row.className = 'phone-url' + (running && index === 0 ? ' primary' : '');

      const badge = document.createElement('span');
      badge.className = 'phone-url-badge';
      badge.textContent = running
        ? (index === 0 ? 'پیشنهادی' : 'جایگزین')
        : 'پس از روشن‌کردن';

      const code = document.createElement('code');
      code.textContent = url;

      const copy = document.createElement('button');
      copy.type = 'button';
      copy.className = 'btn btn-secondary btn-xs';
      copy.textContent = 'کپی';
      copy.addEventListener('click', () => copyText(url, copy));

      const qrButton = document.createElement('button');
      qrButton.type = 'button';
      qrButton.className = 'btn btn-secondary btn-xs';
      qrButton.textContent = 'QR';
      qrButton.title = 'نمایش کد QR برای همین آدرس';
      qrButton.addEventListener('click', () => {
        if (!running) {
          flashPhone('اول انتقال را روشن کن.', false);
          return;
        }
        phoneQrPinned = url;
        phoneQrIsApk = false;
        renderQr(url);
      });

      row.appendChild(badge);
      row.appendChild(code);
      row.appendChild(qrButton);
      row.appendChild(copy);
      elements.phoneUrls.appendChild(row);
    });
    phoneBaseUrl = urls[0] || '';
    if (!running) {
      phoneQrPinned = '';
      phoneQrIsApk = false;
    }
    // A pinned QR may point at /medo.apk, which is not one of the base URLs.
    const qrTarget = phoneQrPinned || phoneBaseUrl;
    renderQr(running ? qrTarget : '');

    const apk = info.apk || {};
    if (apk.exists) {
      elements.phoneApkInfo.innerHTML = 'نسخهٔ ساخته‌شده: <b>' + formatBytes(apk.bytes)
        + '</b> · ' + formatWhen(apk.mtime);
    } else {
      elements.phoneApkInfo.textContent = 'هنوز APK ساخته نشده است.';
    }
    elements.phoneApkDownload.style.opacity = apk.exists ? '' : '0.5';
    elements.phoneApkDownload.style.pointerEvents = apk.exists ? '' : 'none';

    if (info.building) {
      elements.phoneBuildBtn.disabled = true;
      elements.phoneBuildBtn.textContent = 'در حال ساخت…';
    } else {
      elements.phoneBuildBtn.disabled = false;
      elements.phoneBuildBtn.textContent = 'ساخت APK تازه';
    }

    if (info.building || info.build_log) {
      elements.phoneLog.hidden = false;
      elements.phoneLog.textContent = info.build_log || 'در حال ساخت APK…';
      elements.phoneLog.scrollTop = elements.phoneLog.scrollHeight;
    } else {
      elements.phoneLog.hidden = true;
    }

    if (info.build_ok === false && !info.building) {
      elements.phoneStatusMeta.textContent = 'ساخت APK ناموفق بود — خروجی پایین را ببین.';
      elements.phoneStatusMeta.style.color = 'var(--accent-wrong)';
    }
  }

  function loadSavedUserSession() {
    try {
      const savedAnswers = localStorage.getItem('medo_answers');
      if (savedAnswers) state.userAnswers = JSON.parse(savedAnswers);
      const savedBookmarks = localStorage.getItem('medo_bookmarks');
      if (savedBookmarks) state.bookmarks = new Set(JSON.parse(savedBookmarks));
    } catch (e) {
      console.warn('Could not read session storage', e);
    }
  }

  function saveSession() {
    try {
      localStorage.setItem('medo_answers', JSON.stringify(state.userAnswers));
      localStorage.setItem('medo_bookmarks', JSON.stringify(Array.from(state.bookmarks)));
    } catch (e) {}
  }

  function seedProgressFromArchive() {
    let changed = false;
    (state.questions || []).forEach(q => {
      if (q.id === undefined || q.id === null) return;
      const stored = q.user_selected_option;
      if (stored && !(q.id in state.userAnswers)) {
        state.userAnswers[q.id] = stored;
        changed = true;
      }
      if (q.bookmarked && !state.bookmarks.has(q.id)) {
        state.bookmarks.add(q.id);
        changed = true;
      }
    });
    if (changed) saveSession();
  }

  // Static export (file://) has no API: probing it would only pollute the
  // console, so writes are attempted only when a server is actually serving.
  function apiWrite(path, payload) {
    if (!(location.protocol === 'http:' || location.protocol === 'https:')) return;
    try {
      fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).catch(() => {});
    } catch (e) {}
  }

  // 400 rows per request: big enough to keep the first paint quick, small
  // enough that a huge Pro bank (long lesson text) never arrives as one blob.
  const QUESTION_PAGE_SIZE = 400;

  async function fetchQuestionPages() {
    const all = [];
    for (let offset = 0; offset < 500000; offset += QUESTION_PAGE_SIZE) {
      const resp = await fetch(
        `/api/questions?offset=${offset}&limit=${QUESTION_PAGE_SIZE}`,
        { cache: 'no-store' });
      if (!resp.ok) return null;
      const data = await resp.json();
      if (!Array.isArray(data)) return null;
      for (const item of data) all.push(item);
      if (data.length < QUESTION_PAGE_SIZE) break;
    }
    return all;
  }

  async function loadQuestions() {
    let fetchFailed = false;
    let archiveResponded = false;
    if (window.MEDO_QUESTIONS && Array.isArray(window.MEDO_QUESTIONS)) {
      state.questions = window.MEDO_QUESTIONS;
    } else {
      // Under the local server the live archive is the source of truth; the
      // static bundle file is only the fallback for a plain file:// open.
      const served = location.protocol === 'http:' || location.protocol === 'https:';
      if (served) {
        const paged = await fetchQuestionPages().catch(() => null);
        if (paged) {
          archiveResponded = true;
          state.questions = paged;
        } else {
          fetchFailed = true;
        }
      }
      if (!state.questions || state.questions.length === 0) {
        for (const src of ['questions.json']) {
          try {
            const resp = await fetch(src, { cache: 'no-store' });
            if (!resp.ok) {
              fetchFailed = true;
              continue;
            }
            const data = await resp.json();
            if (Array.isArray(data)) {
              // An empty array is a real answer ("archive is empty"), not a
              // failed request — the two states need different advice.
              archiveResponded = true;
              state.questions = data;
              break;
            }
          } catch (err) {
            fetchFailed = true;
            /* try the next source */
          }
        }
      }
    }

    if (!state.questions || state.questions.length === 0) {
      // "Server did not answer" and "archive is empty" need different advice:
      // restart the program, or download the bank.
      const served = location.protocol === 'http:' || location.protocol === 'https:';
      showEmptyState({ serverDown: served && !archiveResponded });
      return;
    }

    // The archive keeps answers/bookmarks as well (that is what `medo stats`
    // reports). localStorage is the freshest copy, so it wins per question and
    // the archive only fills what this browser has never seen.
    seedProgressFromArchive();
    buildSearchIndex();

    applyFilter();
    renderCurrentQuestion();
    updateStats();
    // The «بانک من» tab counts questions from this payload; it was rendered
    // before the archive arrived, so refresh it now.
    if (state.tree.length) renderTreeSummary();
  }

  // --- Filtering & Search ---
  // A question can be reached through several nodes (the whole topic, and any
  // number of «?tag=» subtopics), so the archive sends the full list.  Old
  // exports only carry the single topic_id/tag_id pair.
  function questionNodes(item) {
    if (Array.isArray(item.nodes) && item.nodes.length) return item.nodes;
    if (!item.topic_id) return [];
    return [{ topic_id: item.topic_id, tag_id: item.tag_id || 0 }];
  }

  function inTreeNode(item, f) {
    const nodes = questionNodes(item);
    if (f.tag !== undefined && f.tag !== null) {
      const only = Number([...f.ids][0]);
      return nodes.some(n => Number(n.topic_id) === only
        && Number(n.tag_id) === Number(f.tag));
    }
    return nodes.some(n => f.ids.has(Number(n.topic_id)));
  }

  /** Fold every question's searchable text once, not on every keystroke. */
  function buildSearchIndex() {
    state.searchIndex = new Map();
    state.questions.forEach((item, idx) => {
      const parts = [item.stem_text || '', item.path || '', item.explanation_text || ''];
      (item.options || []).forEach(opt => parts.push(opt.text || ''));
      state.searchIndex.set(idx, normalizeSearch(parts.join(' ')));
    });
  }

  /** Wrap the site's «تایید گزینه N» marker so it reads as a key badge. */
  function decorateLessonHtml(html) {
    return String(html || '').replace(
      /(تایید|تأیید)\s*گزینه\s*([0-9۰-۹]+)/g,
      '<span class="answer-key-badge">$&</span>');
  }

  /** The list endpoint sends no lesson HTML; fetch the full row on demand. */
  async function ensureFullQuestion(item) {
    if (!item || item.__full || item.__loading || !item.id) return;
    const served = location.protocol === 'http:' || location.protocol === 'https:';
    if (!served) return;
    item.__loading = true;
    try {
      const resp = await fetch('/api/question/' + item.id, { cache: 'no-store' });
      if (!resp.ok) return;
      const full = await resp.json();
      if (!full || full.ok === false) return;
      Object.assign(item, full);
      item.__full = true;
      const idx = state.filteredIndices[state.currentIndex];
      if (state.questions[idx] === item) renderCurrentQuestion();
    } catch (e) {
      /* offline: the plain explanation_text is already on screen */
    } finally {
      item.__loading = false;
    }
  }

  function applyFilter() {
    const q = normalizeSearch(state.searchQuery);
    state.filteredIndices = state.questions.map((item, idx) => idx).filter(idx => {
      const item = state.questions[idx];
      const qid = item.id || idx;

      // Tab filter
      if (state.activeFilter === 'answered' && !(qid in state.userAnswers)) return false;
      if (state.activeFilter === 'bookmarked' && !state.bookmarks.has(qid)) return false;

      // Tree filter: one topic, one subtopic, or every topic under a category
      if (state.treeFilter && !inTreeNode(item, state.treeFilter)) return false;

      // Search query: one pre-folded haystack per question.
      if (q && !(state.searchIndex.get(idx) || '').includes(q)) return false;

      return true;
    });

    if (state.filteredIndices.length === 0) {
      state.currentIndex = 0;
    } else if (state.currentIndex >= state.filteredIndices.length) {
      state.currentIndex = 0;
    }

    elements.jumpSlider.max = Math.max(1, state.filteredIndices.length);
    renderPalette();
  }

  // --- Rendering ---
  function renderCurrentQuestion() {
    if (state.filteredIndices.length === 0) {
      elements.optionsContainer.innerHTML = '';
      elements.explanationBox.style.display = 'none';
      elements.indexBadge.textContent = '۰ از ۰';
      elements.prevBtn.disabled = true;
      elements.nextBtn.disabled = true;
      elements.breadcrumbBar.style.display = 'none';

      const activeFilter = state.treeFilter;
      if (activeFilter && activeFilter.ids.size) {
        // Empty because nothing was downloaded yet — offer the action right
        // here instead of a dead end.
        elements.topicBadge.textContent = activeFilter.label;
        const isSub = activeFilter.tag !== undefined && activeFilter.tag !== null;
        const parentTopic = isSub ? findTopicNode([...activeFilter.ids][0]) : null;
        const parentStored = parentTopic
          ? ((parentTopic.progress || {}).scraped || 0) : 0;
        elements.stemContent.innerHTML =
          '<div class="empty-topic">'
          + '<div class="empty-topic-title">هنوز سوالی از این مبحث ذخیره نشده است</div>'
          + '<div class="empty-topic-note">'
          + (isSub && parentStored > 0
            ? 'زیرمبحث‌ها برش‌هایی از سوال‌های مبحث‌اند و جدا دانلود می‌شوند. سوال‌های «'
              + parentTopic.title + '» (' + toPersianDigits(parentStored)
              + ') همان‌جا ذخیره شده است.'
            : 'می‌توانید همین حالا سوالاتش را از سایت دانلود کنید.')
          + '</div></div>';
        const dlBtn = document.createElement('button');
        dlBtn.type = 'button';
        dlBtn.className = 'btn btn-primary empty-topic-dl';
        dlBtn.textContent = isSub
          ? 'دانلود سوالات این زیرمبحث'
          : (activeFilter.ids.size > 1
            ? `دانلود سوالات این دسته (${toPersianDigits(activeFilter.ids.size)} مبحث)`
            : 'دانلود سوالات این مبحث');
        dlBtn.addEventListener('click', () => {
          if (!isSub && activeFilter.ids.size > 1) {
            const ok = window.confirm(
              `سوالات ${toPersianDigits(activeFilter.ids.size)} مبحث از «${activeFilter.label}» دانلود شود؟\n`
              + 'این کار ممکن است چند دقیقه طول بکشد.'
            );
            if (!ok) return;
          }
          const targets = isSub
            ? [{ topic_id: [...activeFilter.ids][0], tag_id: activeFilter.tag,
                 subtopic_title: activeFilter.label }]
            : [...activeFilter.ids].map(id => ({ topic_id: id }));
          startScrape(targets, { label: activeFilter.label });
        });
        const emptyBox = elements.stemContent.querySelector('.empty-topic');
        emptyBox.appendChild(dlBtn);
        if (isSub && parentStored > 0 && parentTopic) {
          const parentBtn = document.createElement('button');
          parentBtn.type = 'button';
          parentBtn.className = 'btn btn-secondary empty-topic-dl';
          parentBtn.textContent = `نمایش ${toPersianDigits(parentStored)} سوال «${parentTopic.title}»`;
          parentBtn.addEventListener('click', () => {
            const tid = topicIdOf(parentTopic);
            setTreeFilter({
              type: 'topic', ids: new Set([Number(tid)]),
              key: treeKeyOf(parentTopic), label: parentTopic.title,
            });
          });
          emptyBox.appendChild(parentBtn);
        }
      } else {
        elements.topicBadge.textContent = 'بدون نتیجه';
        elements.stemContent.innerHTML = '';
        elements.stemContent.appendChild(stateCard('empty', {
          title: 'موردی پیدا نشد',
          body: 'هیچ سوالی با جستجو یا فیلتر فعلی هم‌خوانی ندارد.',
          actions: [{
            label: 'پاک‌کردن جستجو و فیلترها',
            primary: true,
            onClick: () => {
              elements.searchInput.value = '';
              state.searchQuery = '';
              elements.clearSearchBtn.hidden = true;
              if (state.treeFilter) setTreeFilter(null);
              applyFilter();
              renderCurrentQuestion();
            }
          }]
        }));
      }
      return;
    }

    const realIdx = state.filteredIndices[state.currentIndex];
    const q = state.questions[realIdx];
    const qid = q.id || realIdx;
    const answeredOption = state.userAnswers[qid];

    // Meta badges
    const pathParts = (q.category_path || '').split('»').map(s => s.trim()).filter(Boolean);
    // A question downloaded through a subtopic carries that sub-category name:
    // show it, so two questions of the same topic but different subtopics are
    // never presented as if they were filed identically.
    if (q.subtopic_title && pathParts[pathParts.length - 1] !== q.subtopic_title) {
      pathParts.push(q.subtopic_title);
    }
    elements.topicBadge.textContent = pathParts.length
      ? pathParts[pathParts.length - 1]
      : (q.topic_id ? `مبحث ${toPersianDigits(q.topic_id)}` : 'بانک سوالات');
    renderBreadcrumb(pathParts);
    elements.indexBadge.textContent = `سوال ${toPersianDigits(state.currentIndex + 1)} از ${toPersianDigits(state.filteredIndices.length)}`;
    
    // Bookmark status
    elements.bookmarkBtn.classList.toggle('active', state.bookmarks.has(qid));

    // Status badge
    if (answeredOption) {
      elements.statusBadge.textContent = 'پاسخ داده شد';
      elements.statusBadge.classList.add('answered');
    } else {
      elements.statusBadge.textContent = 'بی‌پاسخ';
      elements.statusBadge.classList.remove('answered');
    }

    // Stem
    elements.stemContent.innerHTML = q.stem_html || `<p>${q.stem_text}</p>`;

    // Options
    elements.optionsContainer.innerHTML = '';
    const voteMap = q.vote_percent || {};

    (q.options || []).forEach((opt, optIdx) => {
      const optLetter = LETTERS[opt.index - 1] || opt.index;
      const optBox = document.createElement('div');
      optBox.className = 'option-box';
      optBox.dataset.optIndex = opt.index;

      const percentVal = voteMap[opt.index] !== undefined && voteMap[opt.index] !== null ? voteMap[opt.index] : opt.vote_percent;

      // Color coding if answered. Without a stored answer there is nothing to
      // be «wrong» against: colouring the pick red would invent a verdict.
      const gradable = q.correct_index !== null && q.correct_index !== undefined;
      if (answeredOption) {
        optBox.classList.add('locked');
        if (gradable && opt.index === q.correct_index) {
          optBox.classList.add('correct');
        } else if (gradable && opt.index === answeredOption) {
          optBox.classList.add('wrong');
        }
      }

      optBox.innerHTML = `
        <div class="option-bar" style="width: ${answeredOption && percentVal ? percentVal : 0}%"></div>
        <div class="option-left">
          <div class="option-letter">${optLetter}</div>
          <div class="option-text">${opt.text}</div>
        </div>
        <div class="option-percent ${answeredOption && percentVal !== null && percentVal !== undefined ? 'visible' : ''}">
          ${percentVal !== null && percentVal !== undefined ? toPersianDigits(percentVal) + '٪' : ''}
        </div>
      `;

      optBox.addEventListener('click', () => handleOptionSelect(q, opt.index));
      elements.optionsContainer.appendChild(optBox);
    });

    // Explanation Box
    const missingAnswer = q.correct_index === null || q.correct_index === undefined;
    if (answeredOption && missingAnswer) {
      elements.explanationBox.style.display = 'block';
      elements.explanationBox.classList.remove('collapsed');
      elements.explanationContent.innerHTML =
        '<p class="answer-missing">پاسخ صحیح این سوال در بانک آفلاین ثبت نشده است؛ '
        + 'انتخاب شما غلط شمرده نمی‌شود و در دقت حساب نمی‌آید.</p>';
    } else if (answeredOption && (q.explanation_html || q.explanation_text)) {
      elements.explanationBox.style.display = 'block';
      elements.explanationBox.classList.remove('collapsed');
      elements.explanationContent.innerHTML = decorateLessonHtml(
        q.explanation_html || `<p>${q.explanation_text}</p>`);
      // The light list carries no lesson HTML: fetch the full page once, then
      // re-render it as the stored textbook markup.
      if (!q.explanation_html) ensureFullQuestion(q);

      // Add image lightbox triggers
      elements.explanationContent.querySelectorAll('img').forEach(img => {
        img.addEventListener('click', () => openImageModal(img.src));
      });
    } else {
      elements.explanationBox.style.display = 'none';
    }

    // Navigation buttons
    elements.prevBtn.disabled = state.currentIndex === 0;
    elements.nextBtn.disabled = state.currentIndex === state.filteredIndices.length - 1;
    // Coming back from an empty/error state: the answer surface must reappear.
    elements.optionsContainer.style.display = '';
    elements.jumpSlider.disabled = false;
    elements.bookmarkBtn.disabled = false;
    elements.resetAnswerBtn.disabled = false;
    elements.jumpSlider.value = state.currentIndex + 1;

    highlightPaletteActiveItem();
  }

  function handleOptionSelect(q, optIndex) {
    const qid = q.id || state.filteredIndices[state.currentIndex];
    if (state.userAnswers[qid]) return; // already answered

    state.userAnswers[qid] = optIndex;
    saveSession();

    // Trigger backend recording if served locally
    apiWrite('/api/answer', { question_id: qid, selected_option: optIndex });

    renderCurrentQuestion();
    renderPalette();
    updateStats();
  }

  function resetCurrentAnswer() {
    const realIdx = state.filteredIndices[state.currentIndex];
    const q = state.questions[realIdx];
    const qid = q.id || realIdx;
    delete state.userAnswers[qid];
    saveSession();
    apiWrite('/api/answer', { question_id: qid, selected_option: 0 });
    renderCurrentQuestion();
    renderPalette();
    updateStats();
  }

  function toggleBookmarkCurrent() {
    const realIdx = state.filteredIndices[state.currentIndex];
    const q = state.questions[realIdx];
    const qid = q.id || realIdx;

    if (state.bookmarks.has(qid)) {
      state.bookmarks.delete(qid);
    } else {
      state.bookmarks.add(qid);
    }
    saveSession();
    apiWrite('/api/bookmark', { question_id: qid, bookmarked: state.bookmarks.has(qid) });
    elements.bookmarkBtn.classList.toggle('active', state.bookmarks.has(qid));
    renderPalette();
    updateStats();
  }

  function renderPalette() {
    elements.paletteGrid.innerHTML = '';
    state.filteredIndices.forEach((realIdx, filterIdx) => {
      const q = state.questions[realIdx];
      const qid = q.id || realIdx;
      const answered = state.userAnswers[qid];
      const btn = document.createElement('button');
      btn.className = 'palette-btn';
      btn.textContent = toPersianDigits(filterIdx + 1);

      if (answered && q.correct_index !== null && q.correct_index !== undefined) {
        btn.classList.add(answered === q.correct_index ? 'correct' : 'wrong');
      }

      if (state.bookmarks.has(qid)) {
        btn.classList.add('bookmarked');
      }

      if (filterIdx === state.currentIndex) {
        btn.classList.add('current');
      }

      btn.addEventListener('click', () => {
        state.currentIndex = filterIdx;
        renderCurrentQuestion();
        if (paletteIsDrawer()) setPaletteOpen(false);
      });

      elements.paletteGrid.appendChild(btn);
    });
  }

  function highlightPaletteActiveItem() {
    elements.paletteGrid.querySelectorAll('.palette-btn').forEach((btn, idx) => {
      btn.classList.toggle('current', idx === state.currentIndex);
    });
  }

  function updateStats() {
    // Counters describe the current category, not the whole archive: with
    // «بیماری‌های پریکارد» selected, «همه (۸۰)» next to 30 questions was
    // simply wrong.
    const f = state.treeFilter;
    const inTree = item => !f || inTreeNode(item, f);
    const base = state.questions.filter(inTree);
    elements.countAll.textContent = toPersianDigits(base.length);

    const answeredCount = base.filter(q => q.id in state.userAnswers).length;
    elements.countAnswered.textContent = toPersianDigits(answeredCount);

    const bookmarkedCount = base.filter(q => state.bookmarks.has(q.id)).length;
    elements.countBookmarked.textContent = toPersianDigits(bookmarkedCount);

    let correctCount = 0;
    let gradableAnswered = 0;
    base.forEach(q => {
      const picked = state.userAnswers[q.id];
      if (picked === undefined) return;
      if (q.correct_index === null || q.correct_index === undefined) return;
      gradableAnswered++;
      if (picked === q.correct_index) correctCount++;
    });

    // Accuracy is over questions the archive can actually grade; otherwise a
    // question the site never revealed silently drags the score down.
    const accuracy = gradableAnswered > 0
      ? Math.round((correctCount / gradableAnswered) * 100) : 0;
    elements.scoreStat.textContent = toPersianDigits(accuracy) + '٪';

    // Session strip above the card: same numbers, but readable at a glance
    // instead of hidden inside the palette tabs.
    if (elements.sessionHeadline) {
      const total = base.length;
      elements.sessionHeadline.textContent = total
        ? `${toPersianDigits(answeredCount)} از ${toPersianDigits(total)} پاسخ داده`
        : 'سوالی در این بخش نیست';
      elements.sessionFill.style.width =
        (total ? Math.round((answeredCount / total) * 100) : 0) + '%';
      elements.sessionAccuracy.textContent = toPersianDigits(accuracy) + '٪';
    }
  }

  // --- Scraper / Extraction Modal ---
  let scrapePollTimer = null;
  let renderedLogCount = 0;

  async function checkServer() {
    try {
      const resp = await fetch('/api/auth-status', { cache: 'no-store' });
      if (resp.ok) return true;
    } catch (e) { /* static bundle or server down */ }
    return false;
  }

  async function refreshAuthStatus() {
    // An exported/file:// bundle has no server behind it; probing would only
    // produce a 404 in the console on every open.
    if (window.MEDO_QUESTIONS) return;
    try {
      const resp = await fetch('/api/auth-status', { cache: 'no-store' });
      const data = await resp.json();
      if (data.authenticated) {
        elements.authDot.classList.add('ok');
        elements.authStatusText.textContent = 'وارد حساب شدید — آماده استخراج';
      } else if (data.authorizing) {
        elements.authDot.classList.remove('ok');
        elements.authStatusText.textContent = 'در انتظار تأیید ورود… کد پیامک را در پنجرهٔ بازشده وارد کنید';
      } else {
        elements.authDot.classList.remove('ok');
        elements.authStatusText.textContent = 'لاگین نکرده‌اید — ابتدا «ورود با مرورگر» را بزنید';
      }
    } catch (e) {
      elements.authStatusText.textContent = 'سرور محلی در دسترس نیست';
    }
  }

  let loginPollTimer = null;

  function startLoginPolling() {
    if (loginPollTimer) clearInterval(loginPollTimer);
    let tries = 0;
    loginPollTimer = setInterval(async () => {
      tries++;
      await refreshAuthStatus();
      // After a confirmed login the server continues the interrupted
      // download by itself. Pick that run up here instead of making the
      // user press «شروع دانلود» a second time.
      if (elements.authDot.classList.contains('ok')) {
        try {
          const r = await fetch('/api/scrape/status', { cache: 'no-store' });
          const st = await r.json();
          if (st.is_running && !scrapePollTimer) {
            await openScraperModal();
            elements.scrapeProgressBox.style.display = 'block';
            elements.startScrapeBtn.style.display = 'none';
            elements.stopScrapeBtn.style.display = 'inline-flex';
            elements.scrapeProgressBar.classList.add('indeterminate');
            appendScrapeLog('ورود تأیید شد — ادامه خودکار دانلود...');
            startScrapePolling();
            clearInterval(loginPollTimer);
            loginPollTimer = null;
            return;
          }
        } catch (e) { /* keep polling */ }
      }
      if (tries > 60) { clearInterval(loginPollTimer); loginPollTimer = null; }
    }, 5000);
  }

  function updateResumeHint() {
    const tid = parseTopicId(elements.topicInput.value)
      || (elements.topicSelect ? parseInt(elements.topicSelect.value, 10) : null);
    const hint = elements.resumeHint;
    if (!hint) return;
    if (!tid) { hint.textContent = ''; return; }
    fetch('/api/scrape/state?topic_id=' + tid, { method: 'POST', cache: 'no-store' })
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (d && d.last_page > 0) {
          hint.textContent = `ادامه از صفحه ${toPersianDigits(d.last_page + 1)}`;
        } else {
          hint.textContent = '';
        }
      })
      .catch(() => { hint.textContent = ''; });
  }

  async function openScraperModal() {
    elements.scraperModal.classList.add('active');
    hideDock();
    const ok = await checkServer();
    const missing = document.getElementById('serverMissingBox');
    if (missing) missing.style.display = ok ? 'none' : 'block';
    if (ok) {
      refreshAuthStatus();
      renderTopicSelect();
      updateResumeHint();
      refreshResumeButton();
      refreshBankHealth();
    }
  }

  /** «ادامهٔ دانلود» is shown only when the archive has unfinished work.

      The list comes from the database, so it survives an app restart (or a
      download that stopped before the window was closed) and does not depend
      on the run being in this process's memory.
  */
  function updateResumeButton(st) {
    const btn = elements.resumeScrapeBtn;
    if (!btn) return;
    const show = Boolean(st && st.can_resume);
    btn.style.display = show ? 'inline-flex' : 'none';
    if (show) {
      btn.textContent = `ادامه دانلود نیمه‌تمام (${toPersianDigits(st.pending_count)} مبحث)`;
      const labels = (st.pending || []).map(p => p.label).filter(Boolean);
      btn.title = labels.length
        ? 'ادامه از آخرین صفحهٔ ذخیره‌شده: ' + labels.join('، ')
        : 'ادامه از آخرین صفحهٔ ذخیره‌شده';
    }
  }

  async function refreshResumeButton() {
    try {
      const resp = await fetch('/api/scrape/status', { cache: 'no-store' });
      if (resp.ok) {
        const st = await resp.json();
        updateResumeButton(st);
        renderUsage(st);
      }
    } catch (e) { /* static bundle */ }
  }

  /** The live internet-usage line of the download panel.

      `run_bytes` is the scraper's own byte counter, so the number grows while
      questions are being fetched; `estimated_bytes` is the measured average
      times the site-declared questions still to download.
  */
  function renderUsage(st) {
    const liveEl = elements.scrapeUsageLive;
    const subEl = elements.scrapeUsageSub;
    if (!liveEl) return;
    const u = (st && st.usage) || null;
    if (!u) {
      liveEl.textContent = 'مصرف اینترنت: —';
      if (subEl) subEl.textContent = '';
      return;
    }
    state.usage = u;
    const running = Boolean(st.is_running);
    const runBytes = Number(u.run_bytes) || 0;
    const runQuestions = Number(u.run_questions) || 0;
    if (running) {
      liveEl.textContent = 'مصرف اینترنت: ' + formatBytes(runBytes)
        + (runQuestions ? ` (${toPersianDigits(runQuestions)} سوال)` : '');
    } else if (runBytes > 0) {
      liveEl.textContent = 'آخرین اجرا: ' + formatBytes(runBytes)
        + (runQuestions ? ` برای ${toPersianDigits(runQuestions)} سوال` : '');
    } else if (Number(u.today_bytes) > 0) {
      // The app was restarted: per-run memory is gone, today's total is not.
      liveEl.textContent = 'مصرف امروز: ' + formatBytes(u.today_bytes);
    } else {
      liveEl.textContent = 'مصرف اینترنت: هنوز اندازه‌گیری نشده';
    }

    const parts = [];
    if (Number(u.avg_question_bytes) > 0) {
      parts.push('میانگین هر سوال: ' + formatBytes(u.avg_question_bytes));
    } else if (running) {
      // Never show total-so-far ÷ questions-so-far: the site pre-fetches in
      // batches, so that number fell with every question and looked broken.
      parts.push('میانگین هر سوال پس از پایان این اجرا محاسبه می‌شود');
    }
    if (Number(u.estimated_bytes) > 0) {
      parts.push('تخمین تا پایان: ~' + formatBytes(u.estimated_bytes)
        + (u.estimated_questions
          ? ` برای ${toPersianDigits(u.estimated_questions)} سوال` : ''));
    }
    if (Number(u.today_bytes) > 0 && runBytes > 0) {
      parts.push('امروز: ' + formatBytes(u.today_bytes));
    }
    if (subEl) subEl.textContent = parts.join(' · ');
    updateUsageEstimate();
  }

  /** Estimate shown before starting: measured average × the selected topic. */
  function updateUsageEstimate() {
    const el = elements.usageEstimateText;
    if (!el) return;
    const u = state.usage || {};
    const avg = Number(u.avg_question_bytes) || 0;
    if (!(avg > 0)) {
      el.hidden = true;
      return;
    }
    const opt = elements.topicSelect && elements.topicSelect.selectedOptions
      ? elements.topicSelect.selectedOptions[0] : null;
    const declared = opt ? Number(opt.dataset.declared || 0) : 0;
    el.hidden = false;
    el.innerHTML = '';
    const avgLine = document.createElement('div');
    avgLine.innerHTML = `میانگین هر سوال: <strong>${formatBytes(avg)}</strong>`;
    el.appendChild(avgLine);
    const line = document.createElement('div');
    line.innerHTML = declared > 0
      ? `این مبحث (${toPersianDigits(declared)} سوال): `
        + `<strong>~${formatBytes(avg * declared)}</strong>`
      : `۲۰۰ سوال: <strong>~${formatBytes(avg * 200)}</strong>`;
    el.appendChild(line);
  }

  function closeScraperModal() {
    elements.scraperModal.classList.remove('active');
    // A live run must never become invisible: collapse into the dock so the
    // download can still be stopped or reopened from the corner. Merely
    // closing an idle dialog must not leave a corner card behind.
    if (lastScrapeState && lastScrapeState.is_running) {
      showDock();
      renderScrapeDock(lastScrapeState);
    } else {
      refreshScrapeState().then(st => {
        if (st && st.is_running) showDock();
      });
    }
  }

  // --- Minimised download dock -------------------------------------------
  let lastScrapeState = null;
  let dockHideTimer = null;

  function showDock() {
    if (!elements.dlDock) return;
    if (dockHideTimer) { clearTimeout(dockHideTimer); dockHideTimer = null; }
    elements.dlDock.hidden = false;
  }

  function hideDock() {
    if (elements.dlDock) elements.dlDock.hidden = true;
  }

  /** The dock mirrors the modal panel: same numbers, two buttons and a bar. */
  function renderScrapeDock(st) {
    if (!elements.dlDock || !st) return;
    lastScrapeState = st;
    const running = Boolean(st.is_running);
    const stopping = running && st.status === 'stopping';
    const job = st.job || 'scrape';
    // «ادامه» belongs to downloads only; a finished repair must not offer it.
    const resumable = !running && Boolean(st.can_resume) && job === 'scrape';
    const jobTitle = job === 'answers' ? 'بازیابی پاسخ‌ها'
      : (job === 'tags' ? 'پخش سوال‌ها در زیرمبحث‌ها'
        : (job === 'pdf' ? 'ساخت PDF کتاب' : 'دانلود سوالات'));

    if (running) {
      elements.dlDockTitle.textContent = stopping
        ? 'در حال توقف ' + jobTitle + '…'
        : (job === 'scrape'
          ? 'در حال دانلود سوالات — صفحه ' + toPersianDigits(st.current_page || 0)
          : 'در حال ' + jobTitle + '…');
    } else if (resumable) {
      elements.dlDockTitle.textContent = 'دانلود نیمه‌تمام — '
        + toPersianDigits(st.pending_count || 0) + ' مبحث آمادهٔ ادامه';
    } else if (job === 'answers') {
      elements.dlDockTitle.textContent = 'بازیابی پاسخ‌ها تمام شد';
    } else if (job === 'tags') {
      elements.dlDockTitle.textContent = 'زیرمبحث‌ها به‌روزرسانی شد';
    } else if (job === 'pdf') {
      elements.dlDockTitle.textContent = st.status === 'completed'
        ? 'فایل PDF ساخته شد — dist/medo_test_book.pdf'
        : 'ساخت PDF متوقف شد — ' + scrapeStatusLabel(st.status);
    } else if (st.status === 'completed') {
      elements.dlDockTitle.textContent = 'دانلود تمام شد — ' + toPersianDigits(st.scraped_count || 0) + ' سوال';
    } else {
      elements.dlDockTitle.textContent = 'دانلود متوقف شد — ' + scrapeStatusLabel(st.status);
    }

    elements.dlDockCount.textContent = job === 'answers'
      ? toPersianDigits(st.scraped_count || 0) + ' پاسخ بازیابی شد'
      : (job === 'tags'
        ? 'نتیجه در گزارش پایین'
        : (job === 'pdf'
          ? 'خروجی در پوشهٔ dist'
          : toPersianDigits(st.scraped_count || 0) + ' سوال ذخیره شد'));
    elements.dlDockPage.textContent = resumable
      ? toPersianDigits(st.pending_count || 0) + ' مبحث ادامه‌پذیر'
      : '';
    elements.dlDockStopBtn.hidden = !running || stopping;
    elements.dlDockResumeBtn.hidden = !resumable;
    elements.dlDockBar.classList.toggle('live', running && !stopping);
    elements.dlDockDot.classList.toggle('live', running && !stopping);
    renderScrapeStats(st);

    // Finished: show the result long enough to be read, then leave the corner.
    if (!running && !resumable && !dockHideTimer) {
      dockHideTimer = setTimeout(() => { hideDock(); dockHideTimer = null; }, 6000);
    }
  }

  async function refreshBankHealth() {
    if (!elements.bankHealthText) return;
    try {
      const resp = await fetch('/api/bank/health', { cache: 'no-store' });
      if (!resp.ok) throw new Error('offline');
      const h = await resp.json();
      const parts = [toPersianDigits(h.total) + ' سوال'];
      if (h.missing_answers) {
        parts.push(toPersianDigits(h.missing_answers) + ' بدون پاسخ');
      }
      if (h.untagged) {
        parts.push(toPersianDigits(h.untagged) + ' بدون زیرمبحث');
      }
      if (h.needs_repair) {
        parts.push(toPersianDigits(h.needs_repair) + ' نیازمند ترمیم');
      }
      elements.bankHealthText.textContent = parts.join(' · ');
      elements.auditAnswersBtn.disabled = !h.missing_answers;
      elements.auditAnswersBtn.textContent = h.missing_answers
        ? 'پیدا کردن ' + toPersianDigits(h.missing_answers) + ' پاسخ جاافتاده'
        : 'همهٔ سوالات پاسخ دارند';
      elements.syncTagsBtn.disabled = !h.untagged;
      elements.syncTagsBtn.textContent = h.untagged
        ? 'پخش ' + toPersianDigits(h.untagged) + ' سوال در زیرمبحث‌ها'
        : 'همهٔ سوال‌ها زیرمبحث دارند';
    } catch (e) {
      elements.bankHealthText.textContent = 'برای بررسی سلامت بانک، سرور محلی لازم است.';
    }
  }

  /** Run one repair job; it uses the same status/log panel as a download. */
  async function runBankRepair(path, job, body) {
    try {
      const resp = await fetch(path, {
        method: 'POST',
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await resp.json();
      if (!data.ok) {
        elements.bankHealthText.textContent = data.message || 'اجرا ممکن نشد.';
        return;
      }
      elements.scrapeProgressBox.style.display = 'block';
      elements.startScrapeBtn.style.display = 'none';
      elements.stopScrapeBtn.style.display = 'inline-flex';
      elements.scrapeProgressBar.classList.add('indeterminate');
      elements.scrapeLogTerminal.innerHTML = '';
      renderedLogCount = 0;
      appendScrapeLog(data.message || 'شروع عملیات...');
      showDock();
      renderScrapeDock({ is_running: true, status: 'running', job: job,
                         current_page: 0, scraped_count: 0 });
      startScrapePolling();
    } catch (e) {
      elements.bankHealthText.textContent = 'سرور محلی در دسترس نیست.';
    }
  }

  async function refreshScrapeState() {
    try {
      const resp = await fetch('/api/scrape/status', { cache: 'no-store' });
      if (!resp.ok) return null;
      const st = await resp.json();
      renderScrapeDock(st);
      return st;
    } catch (e) {
      return null;
    }
  }

  function minimizeScraperModal() {
    elements.scraperModal.classList.remove('active');
    const st = lastScrapeState;
    if (st && (st.is_running || st.can_resume)) {
      showDock();
      renderScrapeDock(st);
    }
    // Fresh numbers, and the dock only if there really is a run or a
    // continuation to offer.
    refreshScrapeState().then(fresh => {
      if (fresh && (fresh.is_running || fresh.can_resume)) {
        showDock();
        renderScrapeDock(fresh);
      }
    });
  }

  function parseTopicId(raw) {
    raw = (raw || '').trim();
    const m = /topic-id-(\d+)/.exec(raw);
    if (m) return parseInt(m[1], 10);
    const n = parseInt(raw, 10);
    return isNaN(n) ? null : n;
  }

  function flatTreeTopics(nodes, out) {
    out = out || [];
    (nodes || []).forEach(n => {
      const tid = topicIdOf(n);
      // Micro-topics («?tag=») share their parent's topic id: listing them
      // would add thousands of duplicate entries to the topic picker.
      if (tid !== null && !/\?tag=/.test(n.source_url || '')) {
        out.push({
          id: tid, title: n.title, path: n.path || '',
          declared: Number((n.progress || {}).declared || 0),
        });
      }
      flatTreeTopics(n.children, out);
    });
    return out;
  }

  function topicPathOf(node) {
    const parts = [];
    let cur = node;
    while (cur) {
      parts.unshift(cur.title || '');
      cur = cur.parent || null;
    }
    return parts.filter(Boolean).join(' » ');
  }

  function annotateTreePaths(nodes, parent) {
    (nodes || []).forEach(n => {
      n.parent = parent;
      n.path = topicPathOf(n);
      annotateTreePaths(n.children, n);
    });
    return nodes;
  }

  function renderTopicSelect() {
    const sel = elements.topicSelect;
    if (!sel) return;
    const prev = sel.value;
    const topics = flatTreeTopics(annotateTreePaths(state.tree, null));
    sel.innerHTML = '';
    const head = document.createElement('option');
    head.value = '';
    head.textContent = topics.length
      ? `— ${toPersianDigits(topics.length)} مبحث یافت شد، انتخاب کنید —`
      : '— دستهبندیها استخراج نشده؛ «بروزرسانی دستهبندیها» را بزنید —';
    sel.appendChild(head);
    topics.forEach(t => {
      const o = document.createElement('option');
      o.value = String(t.id);
      if (t.declared > 0) o.dataset.declared = String(t.declared);
      o.textContent = (t.path || t.title) ? `${t.path || t.title} (${toPersianDigits(t.id)})` : String(t.id);
      sel.appendChild(o);
    });
    if (prev) sel.value = prev;
    updateUsageEstimate();
  }

  async function refreshTreeFromServer() {
    elements.treeRefreshBtn.disabled = true;
    try {
      const resp = await fetch('/api/discover-tree', { method: 'POST' });
      const data = await resp.json();
      if (data.ok) {
        // Discovery runs in the background; poll the controller log until it
        // finishes, then refresh the tree dropdown.
        elements.authStatusText.textContent = data.message || 'در حال دریافت دستهبندیها...';
        let tries = 0;
        const poll = setInterval(async () => {
          tries++;
          let st = null;
          try {
            const r = await fetch('/api/scrape/status', { cache: 'no-store' });
            st = await r.json();
          } catch (e) { clearInterval(poll); return; }
          const lastLog = (st.logs || [])[ (st.logs || []).length - 1 ] || '';
          if (lastLog.startsWith('درخت دستهبندی گرفته شد')
              || lastLog.startsWith('خطا در گرفتن درخت')) {
            clearInterval(poll);
            elements.authStatusText.textContent = lastLog;
            await loadTree();
            renderTopicSelect();
          } else if (tries > 200) {
            clearInterval(poll);
            elements.authStatusText.textContent = 'دریافت دستهبندی بیش از حد طول کشید؛ بعداً دوباره بزنید.';
          }
        }, 2000);
      } else {
        elements.authStatusText.textContent = data.message || 'فراخوانی دستهبندی ممکن نشد.';
      }
    } catch (e) {
      elements.authStatusText.textContent = 'سرور محلی در دسترس نیست.';
    }
    elements.treeRefreshBtn.disabled = false;
  }

  function appendScrapeLog(msg) {
    const el = elements.scrapeLogTerminal;
    const line = document.createElement('div');
    line.textContent = msg;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
    while (el.children.length > 250) el.removeChild(el.firstChild);
  }

  // Distinct name from the tree's startScrape(targets): both used to be
  // declared as `startScrape`, and the later declaration silently replaced
  // the form button's handler — the modal's «شروع دانلود» did nothing.
  async function startScrapeFromForm() {
    let tid = parseTopicId(elements.topicInput.value);
    if (!tid && elements.topicSelect) {
      tid = parseInt(elements.topicSelect.value, 10) || null;
    }
    if (!tid) {
      if (elements.topicInput.value.trim()) elements.topicInput.classList.add('error');
      elements.authStatusText.textContent = 'یک مبحث انتخاب کنید یا شماره/آدرس آن را بنویسید.';
      return;
    }
    elements.topicInput.classList.remove('error');
    const body = { topic_id: tid };
    // Default: always start from page 1; resume-from-last is opt-in.
    const sp = elements.startPageInput
      ? parseInt(elements.startPageInput.value, 10) : NaN;
    if (!isNaN(sp) && sp > 0) body.start_page = sp;
    else if (elements.resumeCheckbox && elements.resumeCheckbox.checked) body.resume = true;
    // Default: the entire topic; a manual page cap is opt-in.
    if (!(elements.allQuestionsCheckbox && elements.allQuestionsCheckbox.checked)) {
      const mp = parseInt(elements.maxPagesInput.value, 10);
      if (!isNaN(mp) && mp > 0) body.max_pages = mp;
    }

    try {
      const resp = await fetch('/api/scrape/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!data.ok) {
        elements.authStatusText.textContent = data.message || 'شروع استخراج ممکن نشد';
        return;
      }
      elements.scrapeLogTerminal.innerHTML = '';
      renderedLogCount = 0;
      elements.scrapeProgressBox.style.display = 'block';
      elements.startScrapeBtn.style.display = 'none';
      elements.stopScrapeBtn.style.display = 'inline-flex';
      elements.scrapeProgressBar.classList.add('indeterminate');
      renderUsage({ is_running: true, usage: {
        run_bytes: 0, run_questions: 0,
        avg_question_bytes: (state.usage || {}).avg_question_bytes || 0,
        today_bytes: (state.usage || {}).today_bytes || 0,
      } });
      appendScrapeLog(data.message || 'شروع استخراج...');
      startScrapePolling();
    } catch (e) {
      appendScrapeLog('خطا در ارتباط با سرور محلی.');
    }
  }

  async function stopScrapeRun() {
    try {
      await fetch('/api/scrape/stop', { method: 'POST' });
      appendScrapeLog('درخواست توقف ارسال شد...');
      if (lastScrapeState) {
        lastScrapeState.status = 'stopping';
        renderScrapeDock(lastScrapeState);
      }
    } catch (e) { /* server gone */ }
  }

  async function resumeScrapeRun() {
    try {
      const resp = await fetch('/api/scrape/resume', { method: 'POST' });
      const data = await resp.json();
      if (!data.ok) {
        elements.authStatusText.textContent = data.message
          || 'ادامهٔ دانلود ممکن نشد.';
        if (!elements.dlDock.hidden) {
          elements.dlDockTitle.textContent = data.message || 'ادامه ممکن نشد';
        }
        return;
      }
      elements.scrapeProgressBox.style.display = 'block';
      elements.startScrapeBtn.style.display = 'none';
      elements.stopScrapeBtn.style.display = 'inline-flex';
      elements.scrapeProgressBar.classList.add('indeterminate');
      renderUsage({ is_running: true, usage: {
        run_bytes: 0, run_questions: 0,
        avg_question_bytes: (state.usage || {}).avg_question_bytes || 0,
        today_bytes: (state.usage || {}).today_bytes || 0,
      } });
      elements.scrapeLogTerminal.innerHTML = '';
      renderedLogCount = 0;
      appendScrapeLog(data.message || 'ادامه دانلود...');
      updateResumeButton({ can_resume: false });
      renderScrapeDock({ is_running: true, status: 'running',
                         current_page: 0, scraped_count: 0 });
      startScrapePolling();
    } catch (e) {
      elements.authStatusText.textContent = 'سرور محلی در دسترس نیست.';
    }
  }

  const SCRAPE_STATUS_FA = {
    idle: 'آماده',
    running: 'در حال اجرا',
    stopping: 'در حال توقف',
    completed: 'پایان یافت',
    auth_required: 'نیاز به ورود به حساب (سایت درخواست ورود کرد)',
    limit: 'محدودیت سایت — بعداً ادامه دهید',
    blocked: 'سایت مرورگر را رد کرد (۴۰۳) — دوباره تلاش کنید',
    error: 'خطا در استخراج',
  };

  function scrapeStatusLabel(status) {
    return SCRAPE_STATUS_FA[status] || (status || 'نامشخص');
  }

  /** Live pacing/cost numbers; the server sends counts only, never page text. */
  function scrapeStatsLine(st) {
    const s = st && st.stats;
    if (!s) return '';
    const parts = [];
    parts.push(toPersianDigits(s.requests_per_minute || 0) + ' درخواست/دقیقه');
    parts.push('تأخیر ' + toPersianDigits((s.delay_low || 0).toFixed(1))
      + '–' + toPersianDigits((s.delay_high || 0).toFixed(1)) + ' ثانیه');
    parts.push('۴۲۹ امروز ' + toPersianDigits(s.throttles_429_today || 0));
    parts.push(toPersianDigits(s.questions_per_hour || 0) + ' سوال/ساعت');
    if (s.eta_seconds !== null && s.eta_seconds !== undefined) {
      const mins = Math.max(1, Math.round(Number(s.eta_seconds) / 60));
      parts.push('زمان تقریبی ' + toPersianDigits(mins) + ' دقیقه');
    }
    if (s.cooldown_seconds > 0) {
      parts.push('توقف موقت ' + toPersianDigits(s.cooldown_seconds) + ' ثانیه');
    }
    return parts.join(' · ');
  }

  function renderScrapeStats(st) {
    const line = scrapeStatsLine(st);
    if (elements.scrapeStatsText) elements.scrapeStatsText.textContent = line;
    if (elements.dlDockStats) elements.dlDockStats.textContent = line;
  }

  function startScrapePolling() {
    if (scrapePollTimer) clearInterval(scrapePollTimer);
    scrapePollTimer = setInterval(async () => {
      let st;
      try {
        const resp = await fetch('/api/scrape/status', { cache: 'no-store' });
        st = await resp.json();
      } catch (e) {
        return; // server gone; keep polling quietly
      }
      elements.scrapeStatusText.textContent = st.is_running
        ? 'در حال استخراج... صفحه ' + toPersianDigits(st.current_page || 0)
        : ('وضعیت: ' + scrapeStatusLabel(st.status));
      elements.scrapeCountText.textContent = toPersianDigits(st.scraped_count || 0) + ' سوال ذخیره شد';
      renderScrapeStats(st);
      renderUsage(st);
      renderScrapeDock(st);

      // `logs` is a sliding window (the last 25 lines), so the number of new
      // lines has to come from the server's monotonic sequence counter —
      // comparing it with logs.length froze the panel once the window filled.
      updateResumeButton(st);

      const logs = st.logs || [];
      const total = Number.isFinite(st.logs_seq) ? st.logs_seq : logs.length;
      if (total < renderedLogCount) renderedLogCount = 0; // new run reset
      const fresh = Math.min(total - renderedLogCount, logs.length);
      if (fresh > 0) logs.slice(logs.length - fresh).forEach(appendScrapeLog);
      renderedLogCount = total;

      if (!st.is_running) {
        clearInterval(scrapePollTimer);
        scrapePollTimer = null;
        elements.startScrapeBtn.style.display = 'inline-flex';
        elements.stopScrapeBtn.style.display = 'none';
        elements.scrapeProgressBar.classList.remove('indeterminate');
        state.downloading = false;
        document.body.classList.remove('tree-downloading');
        appendScrapeLog('پایان عملیات — بارگذاری مجدد سوالات...');
        await loadQuestions();
        await loadTree();
        renderTree();
        refreshAuthStatus();
        refreshResumeButton();
        refreshBankHealth();
      }
    }, 1200);
  }

  // --- Category Tree ---
  async function loadTree() {
    let tree = Array.isArray(window.MEDO_TREE) ? window.MEDO_TREE : null;

    if (!tree) {
      // Server first (this is the live app); the static bundle opened from
      // file:// falls through to the exported categories.json.
      try {
        const resp = await fetch('/api/tree', { cache: 'no-store' });
        if (resp.ok) tree = await resp.json();
      } catch (e) { /* file:// / server down */ }
    }
    if (!tree) {
      try {
        const resp = await fetch('categories.json', { cache: 'no-store' });
        if (resp.ok) tree = await resp.json();
      } catch (e) { /* absent in the dev server */ }
    }

    if (Array.isArray(tree) && tree.length > 0) {
      state.tree = tree;
      if (state.expandedKeys.size === 0) {
        // Open the branches the user actually has questions in; the full
        // tree starts collapsed except its categories.
        tree.forEach(node => state.expandedKeys.add(String(node.source_url || node.title || '')));
        const reveal = nodes => (nodes || []).forEach(n => {
          if (state.treeMode === 'mine' && subtreeStored(n) > 0) {
            state.expandedKeys.add(String(n.source_url || n.title || ''));
          }
          reveal(n.children);
        });
        reveal(tree);
      }
      setTreeMode(state.treeMode);
      renderTopicSelect();
      setTreeOpen(window.innerWidth >= 1280);
    } else {
      elements.treeToggleBtn.style.display = 'none';
      setTreeOpen(false);
    }
  }

  /** Desktop: the panel sits inline. Small screens: it is a drawer. */
  function setTreeOpen(open) {
    if (!elements.treeSidebar) return;
    elements.treeSidebar.classList.toggle('open', open);
    document.body.classList.toggle('tree-open', open && window.innerWidth < 1280);
    if (elements.treeToggleBtn) {
      elements.treeToggleBtn.setAttribute('aria-expanded', String(open));
    }
  }

  function treeIsDrawer() {
    return window.innerWidth < 1280;
  }

  /** Desktop: inline panel. Phones: left drawer (same pattern as the tree). */
  function paletteIsDrawer() {
    return window.innerWidth <= 900;
  }

  function paletteIsOpen() {
    if (paletteIsDrawer()) {
      return elements.paletteSidebar.classList.contains('open');
    }
    return elements.paletteSidebar.style.display !== 'none';
  }

  /** The header wraps into two rows on phones; measure it instead of
   *  hard-coding a height, so the drawers never leave a strip of page showing. */
  function syncNavHeight() {
    const nav = document.querySelector('.navbar');
    if (!nav) return;
    const h = Math.round(nav.getBoundingClientRect().height);
    if (h > 0) document.documentElement.style.setProperty('--nav-h', h + 'px');
  }

  function setPaletteOpen(open) {
    if (!elements.paletteSidebar) return;
    if (paletteIsDrawer()) {
      // Inline display from the old toggle would hide the drawer for good.
      elements.paletteSidebar.style.display = '';
      elements.paletteSidebar.classList.toggle('open', open);
      document.body.classList.toggle('palette-open', open);
    } else {
      elements.paletteSidebar.classList.remove('open');
      document.body.classList.remove('palette-open');
      elements.paletteSidebar.style.display = open ? 'flex' : 'none';
    }
    elements.paletteToggleBtn.setAttribute('aria-expanded', String(open));
  }

  /** «۷ دسته · ۱۵۰ مبحث» / «۲۳۴ سوال · ۴ مبحث» under the panel title. */
  function renderTreeSummary(nodes) {
    if (!elements.treeMineCount) { /* older bundle */ }
    else elements.treeMineCount.textContent = toPersianDigits(state.questions.length);
    if (!elements.treeSummary) return;
    const source = nodes || (state.treeMode === 'mine'
      ? buildLibraryTree(state.tree) : (state.tree || []));
    if (state.treeMode === 'mine') {
      let topics = 0;
      const walk = node => {
        if (isRealTopic(node) || isMicroTopic(node)) topics += 1;
        (node.children || []).forEach(walk);
      };
      source.forEach(walk);
      elements.treeSummary.textContent = state.questions.length
        ? `${toPersianDigits(state.questions.length)} سوال · ${toPersianDigits(topics)} مبحث`
        : '';
      return;
    }
    let categories = 0, topics = 0;
    const walk = node => {
      if (node.kind === 'category') categories += 1;
      else if (isRealTopic(node) && !isMicroTopic(node)) topics += 1;
      (node.children || []).forEach(walk);
    };
    (state.tree || []).forEach(walk);
    elements.treeSummary.textContent = topics
      ? `${toPersianDigits(categories)} دسته · ${toPersianDigits(topics)} مبحث`
      : '';
  }

  function setTreeMode(mode) {
    state.treeMode = mode === 'all' ? 'all' : 'mine';
    try { localStorage.setItem('medo_tree_mode', state.treeMode); } catch (e) {}
    (elements.treeTabs || []).forEach(btn => {
      const active = btn.dataset.treeMode === state.treeMode;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', String(active));
    });
    renderTree();
  }

  function topicIdOf(node) {
    const m = /topic-id-(\d+)/.exec(node.source_url || '');
    return m ? parseInt(m[1], 10) : null;
  }

  const treeKeyOf = node => String(node.source_url || node.title || '');
  const isMicroTopic = node => /\?tag=/.test(node.source_url || '');
  const isRealTopic = node => !isMicroTopic(node)
    && (node.kind === 'topic' || topicIdOf(node) !== null);

  /** Every real topic id below a node (categories may hold dozens). */
  const isSubtopic = node => /[?&]tag=\d+/.test(node.source_url || '');
  const tagIdOf = node => {
    const m = /[?&]tag=(\d+)/.exec(node.source_url || '');
    return m ? parseInt(m[1], 10) : null;
  };

  function collectTopicIds(node) {
    if (node.__topicIds) return node.__topicIds;
    const out = new Set();
    const walk = n => {
      if (isRealTopic(n)) {
        const tid = topicIdOf(n);
        if (tid !== null) out.add(Number(tid));
      }
      (n.children || []).forEach(walk);
    };
    walk(node);
    node.__topicIds = out;   // memoised: a section subtree is walked often
    return out;
  }

  /** The topic node for a numeric site topic id, wherever it sits in the tree. */
  function findTopicNode(tid) {
    let found = null;
    const walk = n => {
      if (found) return;
      if (isRealTopic(n) && topicIdOf(n) === Number(tid)) { found = n; return; }
      (n.children || []).forEach(walk);
    };
    (state.tree || []).forEach(walk);
    return found;
  }

  /** Questions stored directly under a node (a topic or one subtopic). */
  function nodeStored(node) {
    if (isMicroTopic(node) || isRealTopic(node)) {
      return Number((node.progress || {}).scraped || 0);
    }
    return 0;
  }

  /** Questions stored under a node, without double-counting subtopics.

    A subtopic is a slice of its topic's pool, so the topic's own count
    already includes it; only a branch (category/section) sums its children.
    */
  function subtreeStored(node) {
    if (isMicroTopic(node)) return nodeStored(node);
    if (isRealTopic(node)) return nodeStored(node);
    return (node.children || []).reduce((sum, child) => sum + subtreeStored(child), 0);
  }

  /** The archive view: the same tree, with everything that holds no question
      removed.  This is what makes the sidebar read as «my categories»
      instead of 150 mostly-empty site topics. */
  function buildLibraryTree(nodes) {
    const out = [];
    (nodes || []).forEach(node => {
      const stored = subtreeStored(node);
      if (!stored) return;
      const clone = { ...node };
      if (isMicroTopic(node) || isRealTopic(node)) {
        // Keep only subtopics that own downloaded questions.
        clone.children = (node.children || [])
          .filter(child => subtreeStored(child) > 0)
          .map(child => ({ ...child, children: [] }));
      } else {
        clone.children = buildLibraryTree(node.children);
      }
      out.push(clone);
    });
    return out;
  }

  /** Aggregated stored/expected questions for a category row. */
  function nodeTotals(node) {
    let scraped = 0, declared = 0, topics = 0;
    const walk = n => {
      if (isRealTopic(n)) {
        const p = n.progress || {};
        scraped += p.scraped || 0;
        declared += p.declared || 0;
        topics += 1;
      }
      (n.children || []).forEach(walk);
    };
    walk(node);
    return { scraped, declared, topics };
  }

  function subtreeMatches(node, query) {
    if ((node.title || '').toLowerCase().includes(query)) return true;
    return (node.children || []).some(child => subtreeMatches(child, query));
  }

  function renderTree() {
    const host = elements.treeScroll;
    if (!host) return;
    annotateTreePaths(state.tree, null);
    const scrollTop = host.scrollTop;
    host.innerHTML = '';
    if (!state.tree.length) {
      host.innerHTML = '<p class="tree-empty">درخت دسته‌بندی هنوز استخراج نشده است.<br>دستور <code>medo discover</code> را اجرا کنید.</p>';
      return;
    }
    const source = state.treeMode === 'mine'
      ? buildLibraryTree(state.tree) : state.tree;
    if (!source.length) {
      // Nothing downloaded yet: show the download tree instead of an empty
      // panel, or the user cannot start at all.
      host.innerHTML = '<p class="tree-empty">هنوز سوالی ذخیره نشده است.<br>'
        + 'از تب «همه دسته‌ها» یک مبحث انتخاب کنید و دانلودش کنید.</p>';
      renderTreeSummary([]);
      return;
    }
    const query = (state.treeSearch || '').trim().toLowerCase();
    if (query) {
      // Reveal matches (and their ancestors) instead of leaving them inside
      // collapsed branches.
      const reveal = node => {
        if (subtreeMatches(node, query)) state.expandedKeys.add(treeKeyOf(node));
        (node.children || []).forEach(reveal);
      };
      state.tree.forEach(reveal);
    }
    source.forEach(node => {
      if (query && !subtreeMatches(node, query)) return;
      host.appendChild(buildTreeNode(node, 0));
    });
    renderTreeSummary(source);
    updateTreeSelection();
    host.scrollTop = scrollTop;
  }

  function buildTreeNode(node, depth) {
    const wrap = document.createElement('div');
    wrap.className = 'tree-node';
    const kids = node.children || [];
    const tid = topicIdOf(node);
    const micro = isMicroTopic(node);
    const realTopic = isRealTopic(node);
    const sub = isMicroTopic(node);
    // Any folder with topics underneath acts as a group — including the
    // «۱۴۰۵»/«قلب» sections whose source_url is medo:path:… rather than a
    // /qbank/cat-id. Those used to only expand, with no filter or download.
    const descendantIds = realTopic || micro ? null : collectTopicIds(node);
    const groupIds = descendantIds && descendantIds.size ? descendantIds : null;
    const canExpand = kids.length > 0;
    const key = treeKeyOf(node);
    const expanded = state.expandedKeys.has(key);
    if (canExpand && !expanded) wrap.classList.add('collapsed');

    const row = document.createElement('div');
    row.className = 'tree-row'
      + (micro ? ' tree-row-micro' : '')
      + (!micro && node.kind === 'category' ? ' is-category' : '')
      + (!micro && realTopic ? ' is-topic' : '');
    row.dataset.key = key;
    row.dataset.type = groupIds ? 'group'
      : (realTopic ? 'topic' : (micro ? 'micro' : (node.kind || 'node')));
    row.dataset.topic = tid === null ? '' : String(tid);
    row.dataset.tag = sub && tagIdOf(node) !== null ? String(tagIdOf(node)) : '';
    row.dataset.ids = groupIds ? [...groupIds].join(',') : '';
    row.dataset.depth = String(depth);
    row.tabIndex = 0;
    row.setAttribute('role', 'treeitem');
    if (canExpand) row.setAttribute('aria-expanded', expanded ? 'true' : 'false');

    const chev = document.createElement('span');
    chev.className = 'tree-chevron' + (canExpand ? '' : ' empty');
    if (canExpand) {
      chev.innerHTML = '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"/></svg>';
      chev.addEventListener('click', ev => {
        ev.stopPropagation();
        toggleExpand(node);
      });
    }
    row.appendChild(chev);

    const title = document.createElement('span');
    title.className = 'tree-node-title';
    title.textContent = node.title || '(بدون عنوان)';
    const progForTip = node.progress || {};
    title.title = [node.path || node.title || '',
      (progForTip.scraped || progForTip.declared)
        ? `${toPersianDigits(progForTip.scraped || 0)} از ${toPersianDigits(progForTip.declared || 0)} سوال ذخیره شده`
        : '',
      micro ? 'زیرمبحث — فقط سوالات همین زیرمبحث نمایش/دانلود می‌شود' : ''
    ].filter(Boolean).join('\n');
    row.appendChild(title);

    if (realTopic || micro) {
      const prog = node.progress || {};
      if (prog.status === 'completed') {
        const done = document.createElement('span');
        done.className = 'tree-badge-done';
        done.textContent = '✓';
        row.appendChild(done);
      } else if (node.partial || ((prog.scraped || 0) > 0 && prog.status !== 'completed')) {
        const badge = document.createElement('span');
        badge.className = 'tree-badge-partial';
        badge.textContent = 'ناقص';
        row.appendChild(badge);
      }
      if ((prog.declared || 0) > 0 || (prog.scraped || 0) > 0) {
        const count = document.createElement('span');
        count.className = 'tree-count' + ((prog.scraped || 0) > 0 ? ' has-data' : '');
        count.textContent = toPersianDigits(prog.scraped || 0) + '/' + toPersianDigits(prog.declared || 0);
        count.title = micro
          ? 'ذخیره‌شده از سوالات این زیرمبحث'
          : 'ذخیره‌شده از کل بانک';
        row.appendChild(count);
      }
    } else if (groupIds) {
      const totals = nodeTotals(node);
      const count = document.createElement('span');
      count.className = 'tree-count' + (totals.scraped > 0 ? ' has-data' : '');
      count.textContent = totals.scraped > 0
        ? toPersianDigits(totals.scraped) + ' سوال'
        : toPersianDigits(totals.topics) + ' مبحث';
      count.title = `${toPersianDigits(totals.topics)} مبحث، ${toPersianDigits(totals.declared)} سوال در بانک سایت`;
      row.appendChild(count);
    }

    // Inline download — right where the topic/subtopic is listed.
    if ((micro && tid !== null) || (!micro && (realTopic || groupIds))) {
      const dl = document.createElement('button');
      dl.type = 'button';
      dl.className = 'tree-dl';
      const done = (realTopic || micro) && (node.progress || {}).status === 'completed';
      dl.title = micro
        ? (done ? 'دانلود مجدد سوالات این زیرمبحث'
                : `دانلود سوالات زیرمبحث «${node.title}»`)
        : (realTopic
          ? (done ? 'دانلود مجدد سوالات این مبحث' : 'دانلود سوالات این مبحث')
          : 'دانلود همه مباحث این دسته');
      dl.setAttribute('aria-label', dl.title);
      dl.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 3v12"/><polyline points="7 10 12 15 17 10"/><path d="M4 21h16"/></svg>';
      dl.addEventListener('click', ev => {
        ev.stopPropagation();
        if (dl.disabled) return;
        downloadFromTree(node, groupIds, tid, dl);
      });
      row.appendChild(dl);
    }

    // A topic downloaded from its own page stores every question without a
    // subtopic tag, so its subtopic rows stay empty. One click queues the
    // filtered (tag) pages, which is the only way the site reveals the split.
    if (realTopic) {
      const subs = kids.filter(k => isSubtopic(k)
        && (((k.progress || {}).declared) || 0) > 0);
      const emptySubs = subs.filter(k => !((k.progress || {}).scraped || 0));
      if (emptySubs.length) {
        const split = document.createElement('button');
        split.type = 'button';
        split.className = 'tree-dl tree-split';
        split.title = `دانلود تفکیک‌شدهٔ ${toPersianDigits(emptySubs.length)} زیرمبحث`;
        split.setAttribute('aria-label', split.title);
        split.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.1"><path d="M4 7h5M4 17h5M15 7h5M15 17h5M9 7v10M15 7v10"/></svg>';
        split.addEventListener('click', ev => {
          ev.stopPropagation();
          if (split.disabled) return;
          startScrape(emptySubs.map(k => ({
            topic_id: tid, tag_id: tagIdOf(k), subtopic_title: k.title,
          })), { label: `زیرمبحث‌های ${node.title}`, btn: split });
        });
        row.appendChild(split);
      }
    }

    const select = () => selectTreeNode(node, groupIds, tid);
    row.addEventListener('click', ev => {
      if (ev.target.closest('.tree-dl') || ev.target.closest('.tree-chevron')) return;
      select();
    });
    row.addEventListener('keydown', ev => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        select();
      }
    });
    wrap.appendChild(row);

    if ((realTopic || micro) && (node.progress || {}).declared > 0) {
      const bar = document.createElement('div');
      bar.className = 'tree-progress';
      const fill = document.createElement('div');
      const prog = node.progress || {};
      fill.className = 'tree-progress-fill' + (prog.status === 'completed' ? ' done' : '');
      fill.style.width = Math.min(100, Math.round(((prog.scraped || 0) / prog.declared) * 100)) + '%';
      bar.appendChild(fill);
      wrap.appendChild(bar);
    }

    if (canExpand) {
      const childBox = document.createElement('div');
      childBox.className = 'tree-children';
      // Lazy: the tree holds 2.5k micro-topics; building every collapsed
      // branch made one chevron click re-layout 380 ms worth of DOM.
      if (expanded) fillChildren(childBox, node, depth);
      wrap.appendChild(childBox);
    }
    return wrap;
  }

  function fillChildren(childBox, node, depth) {
    const query = (state.treeSearch || '').trim().toLowerCase();
    (node.children || []).forEach(k => {
      if (query && !subtreeMatches(k, query)) return;
      childBox.appendChild(buildTreeNode(k, depth + 1));
    });
    childBox.dataset.filled = '1';
  }

  function toggleExpand(node) {
    const key = treeKeyOf(node);
    const opening = !state.expandedKeys.has(key);
    if (opening) state.expandedKeys.add(key);
    else state.expandedKeys.delete(key);

    // Toggle in place — a full renderTree() rebuilt thousands of rows.
    const row = document.querySelector(
      '#treeSidebar .tree-row[data-key="' + CSS.escape(key) + '"]'
    );
    const wrap = row ? row.parentElement : null;
    const childBox = wrap ? wrap.querySelector(':scope > .tree-children') : null;
    if (!row || !childBox) {
      renderTree();
      return;
    }
    if (opening && childBox.dataset.filled !== '1') {
      fillChildren(childBox, node, Number(row.dataset.depth) || 0);
    }
    wrap.classList.toggle('collapsed', !opening);
    row.setAttribute('aria-expanded', String(opening));
  }

  function setTreeFilter(filter) {
    state.treeFilter = filter;
    updateTreeSelection();
    applyFilter();
    state.currentIndex = 0;
    renderCurrentQuestion();
    updateStats();
    if (filter && treeIsDrawer()) setTreeOpen(false);
  }

  function updateTreeSelection() {
    const filter = state.treeFilter;
    document.querySelectorAll('#treeSidebar .tree-row').forEach(row => {
      const groupActive = filter && filter.type === 'group'
        && row.dataset.type === 'group' && row.dataset.key === filter.key;
      const topicActive = filter && filter.type === 'topic'
        && (row.dataset.type === 'topic' || row.dataset.type === 'micro')
        && filter.ids.has(Number(row.dataset.topic));
      const subtopicActive = filter && filter.type === 'subtopic'
        && row.dataset.type === 'micro' && row.dataset.tag
        && String(filter.tag) === row.dataset.tag
        && filter.ids.has(Number(row.dataset.topic));
      const active = Boolean(groupActive || topicActive || subtopicActive);
      row.classList.toggle('active', active);
      if (active) row.setAttribute('aria-current', 'true');
      else row.removeAttribute('aria-current');
    });

    if (elements.treeActiveFilter) {
      const visible = Boolean(filter);
      elements.treeActiveFilter.hidden = !visible;
      if (visible && elements.treeFilterValue) {
        const count = filter.ids.size;
        elements.treeFilterValue.textContent = count > 1
          ? `${filter.label} (${toPersianDigits(count)} مبحث)`
          : filter.label;
        elements.treeFilterValue.dataset.tag = filter.tag != null ? String(filter.tag) : '';
        elements.treeFilterValue.title = filter.label;
      }
    }
  }

  function selectTreeNode(node, groupIds, tid) {
    if (groupIds && groupIds.size) {
      const key = treeKeyOf(node);
      const same = state.treeFilter && state.treeFilter.type === 'group'
        && state.treeFilter.key === key;
      if (same) return setTreeFilter(null);
      if (!state.expandedKeys.has(key)) toggleExpand(node);  // show its contents
      return setTreeFilter({ type: 'group', ids: groupIds, key, label: node.title });
    }
    if (tid !== null) {
      const micro = isMicroTopic(node);
      const tag = micro ? tagIdOf(node) : null;
      const same = state.treeFilter && state.treeFilter.key === treeKeyOf(node);
      if (same) return setTreeFilter(null);
      if (tag !== null) {
        // A subtopic is its own category: show just its questions, not the
        // whole parent topic (which is what made every sub-category of a
        // topic look like the same bucket).
        return setTreeFilter({
          type: 'subtopic', ids: new Set([Number(tid)]), tag,
          key: treeKeyOf(node), label: node.title,
        });
      }
      return setTreeFilter({
        type: 'topic', ids: new Set([Number(tid)]),
        key: treeKeyOf(node), label: node.title,
      });
    }
    if ((node.children || []).length) toggleExpand(node);
  }

  /** Start a run for topics/subtopics and attach the progress UI. */
  async function startScrape(targets, opts) {
    const options = opts || {};
    if (!targets.length || state.downloading) return false;
    const done = Boolean(options.done);
    const btn = options.btn || null;

    state.downloading = true;
    document.body.classList.add('tree-downloading');
    if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
    if (treeIsDrawer()) setTreeOpen(false);
    try {
      const resp = await fetch('/api/scrape/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic_id: targets[0].topic_id,
          targets,
          resume: !done,
          start_page: done ? 1 : undefined,
        }),
      });
      const data = await resp.json();
      if (!data.ok) {
        elements.authStatusText.textContent = data.message || 'شروع دانلود ممکن نشد.';
        // Show it where the user is (the sidebar), not only in the modal.
        if (elements.treeFilterValue) {
          elements.treeActiveFilter.hidden = false;
          elements.treeFilterValue.textContent = data.message || 'شروع دانلود ممکن نشد.';
        }
        return false;
      }
      await openScraperModal();
      elements.scrapeLogTerminal.innerHTML = '';
      renderedLogCount = 0;
      elements.scrapeProgressBox.style.display = 'block';
      elements.startScrapeBtn.style.display = 'none';
      elements.stopScrapeBtn.style.display = 'inline-flex';
      elements.scrapeProgressBar.classList.add('indeterminate');
      renderUsage({ is_running: true, usage: {
        run_bytes: 0, run_questions: 0,
        avg_question_bytes: (state.usage || {}).avg_question_bytes || 0,
        today_bytes: (state.usage || {}).today_bytes || 0,
      } });
      appendScrapeLog(data.message || 'شروع دانلود...');
      appendScrapeLog(targets.length === 1
        ? (targets[0].tag_id
          ? `زیرمبحث انتخاب‌شده: ${options.label || targets[0].tag_id}`
          : `مبحث انتخاب‌شده: ${targets[0].topic_id}`)
        : `${toPersianDigits(targets.length)} مبحث از «${options.label || ''}»`);
      startScrapePolling();
      return true;
    } catch (e) {
      elements.authStatusText.textContent = 'خطا در ارتباط با سرور محلی.';
      return false;
    } finally {
      state.downloading = false;
      document.body.classList.remove('tree-downloading');
      if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
    }
  }

  async function downloadFromTree(node, groupIds, tid, btn) {
    const tag = isMicroTopic(node) ? tagIdOf(node) : null;
    let targets;
    if (tag !== null && tid !== null) {
      targets = [{ topic_id: Number(tid), tag_id: tag, subtopic_title: node.title }];
    } else if (groupIds && groupIds.size) {
      targets = [...groupIds].map(id => ({ topic_id: Number(id) }));
    } else if (tid !== null) {
      targets = [{ topic_id: Number(tid) }];
    } else {
      return;
    }
    if (!targets.length || state.downloading) return;

    // A finished topic has nothing left to resume — a second click means
    // "download it again", so restart at page 1 and say so first.
    const isGroup = targets.length > 1;
    const done = !isGroup && (node.progress || {}).status === 'completed';
    if (isGroup) {
      const ok = window.confirm(
        `سوالات ${toPersianDigits(targets.length)} مبحث از «${node.title}» دانلود شود؟\n`
        + 'این کار ممکن است چند دقیقه طول بکشد و از سهمیه امروز سایت کم کند.'
      );
      if (!ok) return;
    } else if (done) {
      const ok = window.confirm(
        `سوالات «${node.title}» قبلاً کامل ذخیره شده است.\n`
        + 'دوباره از صفحه ۱ خوانده شود؟'
      );
      if (!ok) return;
    }

    await startScrape(targets, { label: node.title, done, btn });
  }

  /** Kept for callers that only have topic ids (e.g. the empty-state CTA). */
  async function startScrapeForIds(ids, opts) {
    return startScrape(ids.map(id => ({ topic_id: Number(id) })), opts);
  }

  // --- Breadcrumb ---
  function renderBreadcrumb(pathParts) {
    const bar = elements.breadcrumbBar;
    if (!bar) return;
    if (!pathParts || pathParts.length === 0) {
      bar.style.display = 'none';
      bar.innerHTML = '';
      return;
    }
    bar.style.display = 'flex';
    bar.innerHTML = '';
    pathParts.forEach((part, idx) => {
      if (idx > 0) {
        const sep = document.createElement('span');
        sep.className = 'crumb-sep';
        sep.textContent = '»';
        bar.appendChild(sep);
      }
      const crumb = document.createElement('span');
      crumb.className = idx === pathParts.length - 1 ? 'crumb-topic' : 'crumb';
      crumb.textContent = part;
      bar.appendChild(crumb);
    });
  }

  // --- Lightbox Modal ---
  function openImageModal(src) {
    elements.modalImage.src = src;
    elements.imageModal.classList.add('active');
  }

  function closeImageModal() {
    elements.imageModal.classList.remove('active');
    elements.modalImage.src = '';
  }

  // --- Event Bindings ---
  function bindEvents() {
    // Navigation buttons
    elements.prevBtn.addEventListener('click', () => {
      if (state.currentIndex > 0) {
        state.currentIndex--;
        renderCurrentQuestion();
      }
    });

    elements.nextBtn.addEventListener('click', () => {
      if (state.currentIndex < state.filteredIndices.length - 1) {
        state.currentIndex++;
        renderCurrentQuestion();
      }
    });

    // Slider
    elements.jumpSlider.addEventListener('input', (e) => {
      state.currentIndex = Math.max(0, parseInt(e.target.value) - 1);
      renderCurrentQuestion();
    });

    // Bookmark & Reset
    elements.bookmarkBtn.addEventListener('click', toggleBookmarkCurrent);
    elements.resetAnswerBtn.addEventListener('click', resetCurrentAnswer);

    // Filter Tabs
    document.querySelectorAll('.filter-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        state.activeFilter = tab.dataset.filter;
        applyFilter();
        renderCurrentQuestion();
        if (paletteIsDrawer()) setPaletteOpen(false);
      });
    });

    // Search
    let searchTimer = null;
    elements.searchInput.addEventListener('input', (e) => {
      state.searchQuery = e.target.value;
      elements.clearSearchBtn.hidden = !state.searchQuery;
      // 200 ms: long enough that a Persian word is typed once, short enough
      // that the result feels immediate on a large bank.
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        applyFilter();
        renderCurrentQuestion();
      }, 200);
    });

    elements.clearSearchBtn.addEventListener('click', () => {
      elements.searchInput.value = '';
      state.searchQuery = '';
      elements.clearSearchBtn.hidden = true;
      applyFilter();
      renderCurrentQuestion();
    });

    // Explanation Accordion Toggle
    elements.explanationHeader.addEventListener('click', () => {
      elements.explanationBox.classList.toggle('collapsed');
    });

    // Theme Toggle
    elements.themeToggleBtn.addEventListener('click', () => {
      const nextTheme = state.theme === 'theme-dark' ? 'theme-light' : 'theme-dark';
      state.theme = nextTheme;
      applyTheme(nextTheme);
    });

    // Header height feeds every drawer offset — measure it on load, after the
    // bundled font settles, and whenever the bar or the window changes size.
    syncNavHeight();
    window.addEventListener('resize', syncNavHeight);
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(syncNavHeight).catch(() => {});
    }
    if (window.ResizeObserver) {
      new ResizeObserver(syncNavHeight).observe(document.querySelector('.navbar'));
    }

    // Question palette: inline panel on desktop, left drawer on phones.
    elements.paletteToggleBtn.setAttribute('aria-expanded', String(!paletteIsDrawer()));
    elements.paletteToggleBtn.addEventListener('click', () => {
      setPaletteOpen(!paletteIsOpen());
    });
    if (elements.paletteBackdrop) {
      elements.paletteBackdrop.addEventListener('click', () => setPaletteOpen(false));
    }
    let paletteWasDrawer = paletteIsDrawer();
    window.addEventListener('resize', () => {
      const drawer = paletteIsDrawer();
      if (drawer !== paletteWasDrawer) {
        paletteWasDrawer = drawer;
        // Inline panel on desktop, closed drawer on phones — never a panel
        // that disappeared because the window happened to be resized.
        setPaletteOpen(!drawer);
      }
    });

    // Category tree toggle & filter clear
    elements.treeToggleBtn.addEventListener('click', () => {
      setTreeOpen(!elements.treeSidebar.classList.contains('open'));
    });
    if (elements.treeCloseBtn) {
      elements.treeCloseBtn.addEventListener('click', () => setTreeOpen(false));
    }
    if (elements.treeBackdrop) {
      elements.treeBackdrop.addEventListener('click', () => setTreeOpen(false));
    }
    let wasDesktop = window.innerWidth >= 1280;
    window.addEventListener('resize', () => {
      const isDesktop = window.innerWidth >= 1280;
      if (isDesktop !== wasDesktop) {
        // Crossing the breakpoint: the inline panel has no room to stay
        // inline (and the drawer would suddenly cover the question), so
        // follow the new mode instead of leaving a half-open layout.
        setTreeOpen(isDesktop);
        wasDesktop = isDesktop;
      } else {
        document.body.classList.toggle(
          'tree-open',
          elements.treeSidebar.classList.contains('open') && !isDesktop
        );
      }
    });
    elements.treeClearBtn.addEventListener('click', () => setTreeFilter(null));
    (elements.treeTabs || []).forEach(btn => {
      btn.addEventListener('click', () => setTreeMode(btn.dataset.treeMode));
    });
    if (elements.resumeScrapeBtn) {
      elements.resumeScrapeBtn.addEventListener('click', resumeScrapeRun);
    }
    if (elements.treeSearch) {
      let searchTimer = null;
      elements.treeSearch.addEventListener('input', () => {
        state.treeSearch = elements.treeSearch.value;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(renderTree, 160);
      });
    }

    // Scraper / extraction modal
    elements.descOpener.addEventListener('click', openScraperModal);

    // Phone bridge: Wi-Fi transfer, USB instructions, APK updates.
    elements.phoneBtn.addEventListener('click', openPhoneModal);
    elements.phoneCloseBtn.addEventListener('click', closePhoneModal);
    elements.phoneBackdrop.addEventListener('click', closePhoneModal);
    elements.phoneStartBtn.addEventListener('click', () => phoneAction('/api/phone/start'));
    elements.phoneStopBtn.addEventListener('click', () => phoneAction('/api/phone/stop'));
    elements.phoneRefreshBankBtn.addEventListener('click', () => phoneAction('/api/phone/bundle'));
    elements.phoneBuildBtn.addEventListener('click', () => phoneAction('/api/phone/build-apk'));
    if (elements.phoneApkQr) {
      elements.phoneApkQr.addEventListener('click', () => {
        if (!phoneBaseUrl) {
          flashPhone('اول انتقال را روشن کن تا آدرس پیدا شود.', false);
          return;
        }
        phoneQrPinned = phoneBaseUrl.replace(/\/$/, '') + '/medo.apk';
        phoneQrIsApk = true;
        renderQr(phoneQrPinned);
      });
    }
    elements.phoneCopyAdbBtn.addEventListener('click', () =>
      copyText('adb reverse tcp:8765 tcp:8765', elements.phoneCopyAdbBtn));
    elements.scraperCloseBtn.addEventListener('click', closeScraperModal);
    elements.scraperBackdrop.addEventListener('click', closeScraperModal);
    if (elements.scraperMinBtn) {
      elements.scraperMinBtn.addEventListener('click', minimizeScraperModal);
    }
    if (elements.dlDock) {
      elements.dlDockOpenBtn.addEventListener('click', openScraperModal);
      elements.dlDockHideBtn.addEventListener('click', hideDock);
      elements.dlDockStopBtn.addEventListener('click', stopScrapeRun);
      elements.dlDockResumeBtn.addEventListener('click', resumeScrapeRun);
    }
    if (elements.auditAnswersBtn) {
      elements.auditAnswersBtn.addEventListener('click',
        () => runBankRepair('/api/answers/audit', 'answers'));
    }
    if (elements.syncTagsBtn) {
      elements.syncTagsBtn.addEventListener('click',
        () => runBankRepair('/api/tags/sync', 'tags'));
    }
    if (elements.pdfBtn) {
      elements.pdfBtn.addEventListener('click', () => {
        // One category when the tree is filtered to a single topic, else the
        // whole bank; a whole-bank book is the slow one, so say which it is.
        const f = state.treeFilter;
        const only = (f && f.ids && f.ids.size === 1) ? Number([...f.ids][0]) : 0;
        const topicId = only > 0 ? only : null;
        elements.pdfBtn.disabled = true;
        runBankRepair('/api/pdf', 'pdf', { topic_id: topicId })
          .finally(() => { elements.pdfBtn.disabled = false; });
      });
    }
    if (elements.doctorBtn) {
      elements.doctorBtn.addEventListener('click', async () => {
        elements.doctorBtn.disabled = true;
        elements.bankHealthText.textContent = 'در حال بررسی شبکه…';
        try {
          const resp = await fetch('/api/doctor', { cache: 'no-store' });
          const result = await resp.json();
          const failed = (result.checks || []).filter(c => !c.ok).map(c => c.detail);
          elements.bankHealthText.textContent = result.message
            + (failed.length ? ' — ' + failed.join(' | ') : '')
            + (result.verdict === 'network' ? ' ' + result.help : '');
        } catch (e) {
          elements.bankHealthText.textContent = 'سرور محلی در دسترس نیست.';
        } finally {
          elements.doctorBtn.disabled = false;
        }
      });
    }
    elements.loginHelperBtn.addEventListener('click', async () => {
      try {
        await fetch('/api/login', { method: 'POST' });
        elements.authStatusText.textContent = 'مرورگر لاگین باز شد — تا پایان لاگین باز میماند؛ وضعیت خودکار بررسی میشود.';
        startLoginPolling();
      } catch (e) {
        elements.authStatusText.textContent = 'سرور محلی در دسترس نیست.';
      }
    });
    elements.treeRefreshBtn.addEventListener('click', refreshTreeFromServer);
    elements.topicSelect.addEventListener('change', () => {
      if (elements.topicSelect.value) elements.topicInput.value = '';
      updateResumeHint();
      updateUsageEstimate();
    });
    elements.topicInput.addEventListener('input', () => {
      if (elements.topicInput.value.trim()) elements.topicSelect.value = '';
      updateUsageEstimate();
    });
    elements.resumeCheckbox.addEventListener('change', updateResumeHint);
    elements.allQuestionsCheckbox.addEventListener('change', () => {
      elements.maxPagesHintWrap.style.display = elements.allQuestionsCheckbox.checked ? 'none' : 'inline-flex';
    });
    elements.startScrapeBtn.addEventListener('click', startScrapeFromForm);
    elements.stopScrapeBtn.addEventListener('click', stopScrapeRun);

    elements.modalCloseBtn.addEventListener('click', closeImageModal);
    elements.modalBackdrop.addEventListener('click', closeImageModal);

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        if (e.key === 'Escape') e.target.blur();
        return;
      }

      // A shortcut must never answer a question through an open dialog, and
      // it must only pick an option that this question actually has (the
      // range used to allow 5 on a four-option question).
      const dialogOpen = elements.scraperModal.classList.contains('active')
        || elements.imageModal.classList.contains('active')
        || elements.phoneModal.classList.contains('active');

      if (!dialogOpen && e.key >= '1' && e.key <= '5') {
        const optNum = parseInt(e.key);
        const realIdx = state.filteredIndices[state.currentIndex];
        const q = state.questions[realIdx];
        const exists = q && (q.options || []).some(opt => opt.index === optNum);
        if (exists) handleOptionSelect(q, optNum);
      } else if (e.key === 'ArrowRight' || e.key === 'k') {
        elements.prevBtn.click();
      } else if (e.key === 'ArrowLeft' || e.key === 'j') {
        elements.nextBtn.click();
      } else if (e.key === 'b' || e.key === 'B') {
        toggleBookmarkCurrent();
      } else if (e.key === 'e' || e.key === 'E') {
        elements.explanationHeader.click();
      } else if (e.key === '/') {
        e.preventDefault();
        elements.searchInput.focus();
      } else if (e.key === 'Escape') {
        closeImageModal();
        closeScraperModal();
        closePhoneModal();
        if (elements.treeSidebar.classList.contains('open') && treeIsDrawer()) {
          setTreeOpen(false);
        }
        if (elements.paletteSidebar.classList.contains('open')) {
          setPaletteOpen(false);
        }
      }
    });
  }

  // Kickstart
  document.addEventListener('DOMContentLoaded', init);
})();
