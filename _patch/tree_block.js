  const treeKeyOf = node => String(node.source_url || node.title || '');
  const isMicroTopic = node => /\?tag=/.test(node.source_url || '');
  const isRealTopic = node => !isMicroTopic(node)
    && (node.kind === 'topic' || topicIdOf(node) !== null);

  /** Every real topic id below a node (categories may hold dozens). */
  function collectTopicIds(node) {
    const out = new Set();
    const walk = n => {
      if (isRealTopic(n)) {
        const tid = topicIdOf(n);
        if (tid !== null) out.add(Number(tid));
      }
      (n.children || []).forEach(walk);
    };
    walk(node);
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
    const scrollTop = host.scrollTop;
    host.innerHTML = '';
    if (!state.tree.length) {
      host.innerHTML = '<p class="tree-empty">درخت دسته‌بندی هنوز استخراج نشده است.<br>دستور <code>medo discover</code> را اجرا کنید.</p>';
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
    state.tree.forEach(node => {
      if (query && !subtreeMatches(node, query)) return;
      host.appendChild(buildTreeNode(node, 0));
    });
    updateTreeSelection();
    elements.treeClearBtn.hidden = !state.treeFilter;
    host.scrollTop = scrollTop;
  }

  function buildTreeNode(node, depth) {
    const wrap = document.createElement('div');
    wrap.className = 'tree-node';
    const kids = node.children || [];
    const tid = topicIdOf(node);
    const micro = isMicroTopic(node);
    const realTopic = isRealTopic(node);
    const groupIds = node.kind === 'category' ? collectTopicIds(node) : null;
    const canExpand = kids.length > 0;
    const key = treeKeyOf(node);
    const expanded = state.expandedKeys.has(key);
    if (canExpand && !expanded) wrap.classList.add('collapsed');

    const row = document.createElement('div');
    row.className = 'tree-row' + (micro ? ' tree-row-micro' : '');
    row.dataset.key = key;
    row.dataset.type = node.kind === 'category' ? 'group'
      : (realTopic ? 'topic' : (micro ? 'micro' : (node.kind || 'node')));
    row.dataset.topic = tid === null ? '' : String(tid);
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
    title.title = node.title || '';
    row.appendChild(title);

    if (realTopic) {
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
        count.className = 'tree-count';
        count.textContent = toPersianDigits(prog.scraped || 0) + '/' + toPersianDigits(prog.declared || 0);
        count.title = 'ذخیره‌شده از کل بانک';
        row.appendChild(count);
      }
    } else if (node.kind === 'category') {
      const totals = nodeTotals(node);
      const count = document.createElement('span');
      count.className = 'tree-count' + (totals.scraped > 0 ? ' has-data' : '');
      count.textContent = totals.scraped > 0
        ? toPersianDigits(totals.scraped) + ' سوال'
        : toPersianDigits(totals.topics) + ' مبحث';
      count.title = `${toPersianDigits(totals.topics)} مبحث، ${toPersianDigits(totals.declared)} سوال در بانک سایت`;
      row.appendChild(count);
    }

    // Inline download — right where the topic is listed.
    if (!micro && (realTopic || (groupIds && groupIds.size))) {
      const dl = document.createElement('button');
      dl.type = 'button';
      dl.className = 'tree-dl';
      dl.title = realTopic ? 'دانلود سوالات این مبحث' : 'دانلود همه مباحث این دسته';
      dl.setAttribute('aria-label', dl.title);
      dl.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 3v12"/><polyline points="7 10 12 15 17 10"/><path d="M4 21h16"/></svg>';
      dl.addEventListener('click', ev => {
        ev.stopPropagation();
        downloadFromTree(node, groupIds, tid);
      });
      row.appendChild(dl);
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

    if (realTopic && (node.progress || {}).declared > 0) {
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
      kids.forEach(k => {
        const query = (state.treeSearch || '').trim().toLowerCase();
        if (query && !subtreeMatches(k, query)) return;
        childBox.appendChild(buildTreeNode(k, depth + 1));
      });
      wrap.appendChild(childBox);
    }
    return wrap;
  }

  function toggleExpand(node) {
    const key = treeKeyOf(node);
    if (state.expandedKeys.has(key)) state.expandedKeys.delete(key);
    else state.expandedKeys.add(key);
    renderTree();
  }

  function setTreeFilter(filter) {
    state.treeFilter = filter;
    elements.treeClearBtn.hidden = !filter;
    updateTreeSelection();
    applyFilter();
    state.currentIndex = 0;
    renderCurrentQuestion();
    updateStats();
  }

  function updateTreeSelection() {
    const filter = state.treeFilter;
    document.querySelectorAll('#treeSidebar .tree-row').forEach(row => {
      const groupActive = filter && filter.type === 'group'
        && row.dataset.type === 'group' && row.dataset.key === filter.key;
      const topicActive = filter && filter.type === 'topic'
        && row.dataset.type === 'topic'
        && filter.ids.has(Number(row.dataset.topic));
      row.classList.toggle('active', Boolean(groupActive || topicActive));
    });
    if (filter && elements.authStatusText) {
      elements.authStatusText.textContent =
        `فیلتر فعال: «${filter.label}» — ${toPersianDigits(filter.ids.size)} مبحث`;
    }
  }

  function selectTreeNode(node, groupIds, tid) {
    if (groupIds && groupIds.size) {
      const key = treeKeyOf(node);
      const same = state.treeFilter && state.treeFilter.type === 'group'
        && state.treeFilter.key === key;
      if (same) return setTreeFilter(null);
      state.expandedKeys.add(key);   // show what this category selected
      renderTree();
      return setTreeFilter({ type: 'group', ids: groupIds, key, label: node.title });
    }
    if (tid !== null) {
      const same = state.treeFilter && state.treeFilter.type === 'topic'
        && state.treeFilter.ids.has(Number(tid))
        && !isMicroTopic(node);
      return setTreeFilter(same ? null : {
        type: 'topic', ids: new Set([Number(tid)]),
        key: treeKeyOf(node), label: node.title,
      });
    }
    if ((node.children || []).length) toggleExpand(node);
  }

  async function downloadFromTree(node, groupIds, tid) {
    const ids = groupIds && groupIds.size
      ? [...groupIds]
      : (tid !== null ? [Number(tid)] : []);
    if (!ids.length || state.downloading) return;
    state.downloading = true;
    document.body.classList.add('tree-downloading');
    try {
      const resp = await fetch('/api/scrape/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic_id: ids[0], topic_ids: ids, resume: true }),
      });
      const data = await resp.json();
      if (!data.ok) {
        elements.authStatusText.textContent = data.message || 'شروع دانلود ممکن نشد.';
        return;
      }
      await openScraperModal();
      elements.scrapeLogTerminal.innerHTML = '';
      renderedLogCount = 0;
      elements.scrapeProgressBox.style.display = 'block';
      elements.startScrapeBtn.style.display = 'none';
      elements.stopScrapeBtn.style.display = 'inline-flex';
      elements.scrapeProgressBar.classList.add('indeterminate');
      appendScrapeLog(data.message || 'شروع دانلود...');
      appendScrapeLog(ids.length === 1
        ? `مبحث انتخاب‌شده: ${ids[0]}`
        : `${toPersianDigits(ids.length)} مبحث از «${node.title || ''}»`);
      startScrapePolling();
    } catch (e) {
      elements.authStatusText.textContent = 'خطا در ارتباط با سرور محلی.';
    } finally {
      state.downloading = false;
      document.body.classList.remove('tree-downloading');
    }
  }

