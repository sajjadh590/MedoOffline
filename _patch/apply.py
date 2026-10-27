from pathlib import Path

p = Path("medo/web/app.js")
s = p.read_text(encoding="utf-8")

s = s.replace(
    "    tree: [],            // category hierarchy from MEDO_TREE / categories.json / /api/tree\n"
    "    topicFilter: null,   // topic_id when a tree topic is selected\n",
    "    tree: [],            // category hierarchy from MEDO_TREE / categories.json / /api/tree\n"
    "    treeFilter: null,    // {type:'topic'|'group', ids:Set<number>, key, label}\n"
    "    expandedKeys: new Set(),\n"
    "    treeSearch: '',\n"
    "    downloading: false,\n",
)

s = s.replace(
    "      // Tree topic filter\n"
    "      if (state.topicFilter !== null && String(item.topic_id) !== String(state.topicFilter)) return false;\n",
    "      // Tree filter: one topic, or every topic under a category\n"
    "      if (state.treeFilter && !state.treeFilter.ids.has(Number(item.topic_id))) return false;\n",
)

s = s.replace(
    "    treeClearBtn: document.getElementById('treeClearBtn'),\n",
    "    treeClearBtn: document.getElementById('treeClearBtn'),\n"
    "    treeSearch: document.getElementById('treeSearch'),\n",
)

s = s.replace(
    "    if (Array.isArray(tree) && tree.length > 0) {\n"
    "      state.tree = tree;\n"
    "      renderTree();\n",
    "    if (Array.isArray(tree) && tree.length > 0) {\n"
    "      state.tree = tree;\n"
    "      if (state.expandedKeys.size === 0) {\n"
    "        tree.forEach(node => state.expandedKeys.add(String(node.source_url || node.title || '')));\n"
    "      }\n"
    "      renderTree();\n",
)

start = s.index("  function renderTree() {")
end = s.index("  // --- Breadcrumb ---")
block = Path("_patch/tree_block.js").read_text(encoding="utf-8")
s = s[:start] + block + s[end:]

s = s.replace(
    "    elements.treeClearBtn.addEventListener('click', () => setTopicFilter(null));\n",
    "    elements.treeClearBtn.addEventListener('click', () => setTreeFilter(null));\n"
    "    if (elements.treeSearch) {\n"
    "      let searchTimer = null;\n"
    "      elements.treeSearch.addEventListener('input', () => {\n"
    "        state.treeSearch = elements.treeSearch.value;\n"
    "        clearTimeout(searchTimer);\n"
    "        searchTimer = setTimeout(renderTree, 160);\n"
    "      });\n"
    "    }\n",
)

s = s.replace(
    "        appendScrapeLog('پایان عملیات — بارگذاری مجدد سوالات...');\n"
    "        await loadQuestions();\n",
    "        state.downloading = false;\n"
    "        document.body.classList.remove('tree-downloading');\n"
    "        appendScrapeLog('پایان عملیات — بارگذاری مجدد سوالات...');\n"
    "        await loadQuestions();\n",
)

p.write_text(s, encoding="utf-8")
print("app.js patched; topicFilter refs left:", s.count("topicFilter"))
