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

  // --- State ---
  const state = {
    questions: [],
    filteredIndices: [],
    currentIndex: 0,
    userAnswers: {},   // qid -> selectedOptionIndex (1-based)
    bookmarks: new Set(),
    activeFilter: 'all', // 'all' | 'answered' | 'bookmarked'
    searchQuery: '',
    theme: localStorage.getItem('medo_theme') || 'theme-dark',
    tree: [],            // category hierarchy from MEDO_TREE / categories.json / /api/tree
    topicFilter: null,   // topic_id when a tree topic is selected
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
    themeToggleBtn: document.getElementById('themeToggleBtn'),
    treeToggleBtn: document.getElementById('treeToggleBtn'),
    treeSidebar: document.getElementById('treeSidebar'),
    treeScroll: document.getElementById('treeScroll'),
    treeClearBtn: document.getElementById('treeClearBtn'),
    breadcrumbBar: document.getElementById('breadcrumbBar'),
    imageModal: document.getElementById('imageModal'),
    modalImage: document.getElementById('modalImage'),
    modalBackdrop: document.getElementById('modalBackdrop'),
    modalCloseBtn: document.getElementById('modalCloseBtn'),
    // Scraper Modal elements
    downloadModalBtn: document.getElementById('downloadModalBtn'),
    scraperModal: document.getElementById('scraperModal'),
    scraperBackdrop: document.getElementById('scraperBackdrop'),
    scraperCloseBtn: document.getElementById('scraperCloseBtn'),
    authDot: document.getElementById('authDot'),
    authStatusText: document.getElementById('authStatusText'),
    loginHelperBtn: document.getElementById('loginHelperBtn'),
    topicInput: document.getElementById('topicInput'),
    startPageInput: document.getElementById('startPageInput'),
    maxPagesInput: document.getElementById('maxPagesInput'),
    startScrapeBtn: document.getElementById('startScrapeBtn'),
    stopScrapeBtn: document.getElementById('stopScrapeBtn'),
    scrapeProgressBox: document.getElementById('scrapeProgressBox'),
    scrapeProgressBar: document.getElementById('scrapeProgressBar'),
    scrapeStatusText: document.getElementById('scrapeStatusText'),
    scrapeCountText: document.getElementById('scrapeCountText'),
    scrapeLogTerminal: document.getElementById('scrapeLogTerminal'),
  };

  // --- Initialization ---
  async function init() {
    applyTheme(state.theme);
    loadSavedUserSession();
    bindEvents();
    loadTree();
    await loadQuestions();
  }

  function applyTheme(theme) {
    document.body.className = theme;
    localStorage.setItem('medo_theme', theme);
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

  async function loadQuestions() {
    if (window.MEDO_QUESTIONS && Array.isArray(window.MEDO_QUESTIONS)) {
      state.questions = window.MEDO_QUESTIONS;
    } else {
      try {
        const resp = await fetch('questions.json');
        if (resp.ok) {
          state.questions = await resp.json();
        } else {
          // Try backend API if running under 'medo serve'
          const apiResp = await fetch('/api/questions');
          if (apiResp.ok) state.questions = await apiResp.json();
        }
      } catch (err) {
        console.error('Failed to load questions:', err);
      }
    }

    if (!state.questions || state.questions.length === 0) {
      elements.stemContent.innerHTML = '<p style="text-align:center; color:var(--text-muted);">هیچ سوالی یافت نشد. لطفاً دستور <code>medo scrape</code> یا <code>medo export-web</code> را اجرا فرمایید.</p>';
      return;
    }

    applyFilter();
    renderCurrentQuestion();
    updateStats();
  }

  // --- Filtering & Search ---
  function applyFilter() {
    const q = state.searchQuery.trim().toLowerCase();
    state.filteredIndices = state.questions.map((item, idx) => idx).filter(idx => {
      const item = state.questions[idx];
      const qid = item.id || idx;

      // Tab filter
      if (state.activeFilter === 'answered' && !(qid in state.userAnswers)) return false;
      if (state.activeFilter === 'bookmarked' && !state.bookmarks.has(qid)) return false;

      // Tree topic filter
      if (state.topicFilter !== null && String(item.topic_id) !== String(state.topicFilter)) return false;

      // Search query
      if (q) {
        const text = (item.stem_text || '') + ' ' + (item.explanation_text || '') + ' ' + (item.options || []).map(o => o.text).join(' ');
        if (!text.toLowerCase().includes(q)) return false;
      }

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
      elements.stemContent.innerHTML = '<p style="text-align:center; color:var(--text-muted); margin: 40px 0;">موردی منطبق با جستجو یا فیلتر جاری یافت نشد.</p>';
      elements.optionsContainer.innerHTML = '';
      elements.explanationBox.style.display = 'none';
      elements.indexBadge.textContent = '۰ از ۰';
      elements.prevBtn.disabled = true;
      elements.nextBtn.disabled = true;
      return;
    }

    const realIdx = state.filteredIndices[state.currentIndex];
    const q = state.questions[realIdx];
    const qid = q.id || realIdx;
    const answeredOption = state.userAnswers[qid];

    // Meta badges
    const pathParts = (q.category_path || '').split('»').map(s => s.trim()).filter(Boolean);
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

      // Color coding if answered
      if (answeredOption) {
        optBox.classList.add('locked');
        if (opt.index === q.correct_index) {
          optBox.classList.add('correct');
        } else if (opt.index === answeredOption) {
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
    if (answeredOption && (q.explanation_html || q.explanation_text)) {
      elements.explanationBox.style.display = 'block';
      elements.explanationBox.classList.remove('collapsed');
      elements.explanationContent.innerHTML = q.explanation_html || `<p>${q.explanation_text}</p>`;

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
    elements.jumpSlider.value = state.currentIndex + 1;

    highlightPaletteActiveItem();
  }

  function handleOptionSelect(q, optIndex) {
    const qid = q.id || state.filteredIndices[state.currentIndex];
    if (state.userAnswers[qid]) return; // already answered

    state.userAnswers[qid] = optIndex;
    saveSession();

    // Trigger backend recording if served locally
    try {
      fetch('/api/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question_id: qid, selected_option: optIndex })
      }).catch(() => {});
    } catch (e) {}

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

      if (answered) {
        if (answered === q.correct_index) {
          btn.classList.add('correct');
        } else {
          btn.classList.add('wrong');
        }
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
    const total = state.questions.length;
    elements.countAll.textContent = toPersianDigits(total);

    const answeredCount = Object.keys(state.userAnswers).length;
    elements.countAnswered.textContent = toPersianDigits(answeredCount);

    const bookmarkedCount = state.bookmarks.size;
    elements.countBookmarked.textContent = toPersianDigits(bookmarkedCount);

    let correctCount = 0;
    Object.entries(state.userAnswers).forEach(([qid, selOpt]) => {
      const q = state.questions.find(item => String(item.id) === String(qid));
      if (q && q.correct_index === selOpt) correctCount++;
    });

    const accuracy = answeredCount > 0 ? Math.round((correctCount / answeredCount) * 100) : 0;
    elements.scoreStat.textContent = toPersianDigits(accuracy) + '٪';
  }

  // --- Category Tree ---
  async function loadTree() {
    let tree = Array.isArray(window.MEDO_TREE) ? window.MEDO_TREE : null;

    if (!tree) {
      try {
        const resp = await fetch('categories.json');
        if (resp.ok) tree = await resp.json();
      } catch (e) { /* file:// or absent */ }
    }
    if (!tree) {
      try {
        const resp = await fetch('/api/tree');
        if (resp.ok) tree = await resp.json();
      } catch (e) { /* static bundle */ }
    }

    if (Array.isArray(tree) && tree.length > 0) {
      state.tree = tree;
      renderTree();
      if (window.innerWidth >= 1280) elements.treeSidebar.classList.add('open');
    } else {
      elements.treeToggleBtn.style.display = 'none';
      elements.treeSidebar.classList.remove('open');
    }
  }

  function topicIdOf(node) {
    const m = /topic-id-(\d+)/.exec(node.source_url || '');
    return m ? parseInt(m[1], 10) : null;
  }

  function renderTree() {
    const host = elements.treeScroll;
    host.innerHTML = '';
    if (!state.tree.length) {
      host.innerHTML = '<p class="tree-empty">درخت دسته‌بندی هنوز استخراج نشده است.<br>دستور <code>medo discover</code> را اجرا کنید.</p>';
      return;
    }
    state.tree.forEach(node => host.appendChild(buildTreeNode(node, 0)));
  }

  function buildTreeNode(node, depth) {
    const wrap = document.createElement('div');
    wrap.className = 'tree-node';
    const kids = node.children || [];
    const tid = topicIdOf(node);
    const isTopic = (node.kind === 'topic') || tid !== null;
    // Deeper levels start collapsed so the tree stays compact
    if (kids.length > 0 && depth >= 1) wrap.classList.add('collapsed');

    const row = document.createElement('button');
    row.className = 'tree-row';
    if (isTopic && state.topicFilter !== null && String(tid) === String(state.topicFilter)) {
      row.classList.add('active');
    }

    if (kids.length > 0) {
      const chev = document.createElement('span');
      chev.className = 'tree-chevron';
      chev.innerHTML = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"/></svg>';
      row.appendChild(chev);
    }

    const title = document.createElement('span');
    title.className = 'tree-node-title';
    title.textContent = node.title || '(بدون عنوان)';
    row.appendChild(title);

    const prog = node.progress || {};
    if (isTopic) {
      if (node.partial || (prog.status && prog.status !== 'completed' && prog.scraped > 0)) {
        const badge = document.createElement('span');
        badge.className = 'tree-badge-partial';
        badge.textContent = 'ناقص';
        row.appendChild(badge);
      } else if (prog.status === 'completed') {
        const done = document.createElement('span');
        done.className = 'tree-badge-done';
        done.textContent = '✓';
        row.appendChild(done);
      }
      if (prog.declared > 0 || prog.scraped > 0) {
        const count = document.createElement('span');
        count.className = 'tree-count';
        count.textContent = toPersianDigits(prog.scraped || 0) + '/' + toPersianDigits(prog.declared || 0);
        row.appendChild(count);
      }
    } else if (node.question_count > 0) {
      const count = document.createElement('span');
      count.className = 'tree-count';
      count.textContent = toPersianDigits(node.question_count);
      row.appendChild(count);
    }

    row.addEventListener('click', () => {
      if (isTopic && tid !== null) {
        setTopicFilter(tid === state.topicFilter ? null : tid);
      } else if (kids.length > 0) {
        wrap.classList.toggle('collapsed');
      }
    });
    wrap.appendChild(row);

    // Thin progress bar under topic rows (scraped / declared)
    if (isTopic && prog.declared > 0) {
      const bar = document.createElement('div');
      bar.className = 'tree-progress';
      const fill = document.createElement('div');
      fill.className = 'tree-progress-fill' + (prog.status === 'completed' ? ' done' : '');
      fill.style.width = Math.min(100, Math.round(((prog.scraped || 0) / prog.declared) * 100)) + '%';
      bar.appendChild(fill);
      wrap.appendChild(bar);
    }

    if (kids.length > 0) {
      const childBox = document.createElement('div');
      childBox.className = 'tree-children';
      kids.forEach(k => childBox.appendChild(buildTreeNode(k, depth + 1)));
      wrap.appendChild(childBox);
    }
    return wrap;
  }

  function setTopicFilter(tid) {
    state.topicFilter = tid;
    elements.treeClearBtn.hidden = tid === null;
    renderTree();
    applyFilter();
    state.currentIndex = 0;
    renderCurrentQuestion();
    updateStats();
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
      });
    });

    // Search
    elements.searchInput.addEventListener('input', (e) => {
      state.searchQuery = e.target.value;
      elements.clearSearchBtn.hidden = !state.searchQuery;
      applyFilter();
      renderCurrentQuestion();
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

    // Sidebar Toggle
    elements.paletteToggleBtn.addEventListener('click', () => {
      const isHidden = elements.paletteSidebar.style.display === 'none';
      elements.paletteSidebar.style.display = isHidden ? 'flex' : 'none';
    });

    // Category tree toggle & filter clear
    elements.treeToggleBtn.addEventListener('click', () => {
      elements.treeSidebar.classList.toggle('open');
    });
    elements.treeClearBtn.addEventListener('click', () => setTopicFilter(null));

    // Modal Close
    elements.modalCloseBtn.addEventListener('click', closeImageModal);
    elements.modalBackdrop.addEventListener('click', closeImageModal);

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        if (e.key === 'Escape') e.target.blur();
        return;
      }

      if (e.key >= '1' && e.key <= '5') {
        const optNum = parseInt(e.key);
        const realIdx = state.filteredIndices[state.currentIndex];
        const q = state.questions[realIdx];
        if (q) handleOptionSelect(q, optNum);
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
      }
    });
  }

  // Kickstart
  document.addEventListener('DOMContentLoaded', init);
})();
