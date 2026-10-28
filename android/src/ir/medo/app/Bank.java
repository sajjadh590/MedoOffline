package ir.medo.app;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/** The whole offline archive: tree + questions, plus topic matching. */
public final class Bank {

    public static final String FORMAT = "medo-bank";
    /** v2 = zip(index.json + chunks/<category>.json); v1 = one JSON file. */
    public static final int SUPPORTED_VERSION = 2;

    public final int version;
    public final String generatedAt;
    public final int total;
    public final int withAnswer;
    public final int withExplanation;
    public final List<Node> roots = new ArrayList<>();
    public final List<Question> questions = new ArrayList<>();

    private final Map<Integer, Question> byId = new HashMap<>();
    private final Map<Integer, Integer> positions = new HashMap<>();
    private final List<Map<String, Object>> chunkFiles = new ArrayList<>();
    /** Light index entries, kept so an evicted chunk can be demoted again. */
    private final Map<Integer, Question> lights = new HashMap<>();
    private final Map<Integer, String> chunkPaths = new HashMap<>();
    /** The last two chunk bodies in memory, oldest first (LRU of 2). */
    private final Map<String, List<Question>> loadedChunks = new HashMap<>();
    private final Deque<String> chunkOrder = new ArrayDeque<>();
    private final Object loadLock = new Object();
    private File zipFile;
    private Map<String, List<Integer>> wordIndex;
    private final Map<String, Node> byUrl = new HashMap<>();
    private final Map<Integer, Integer> perTopic = new HashMap<>();
    private final Map<Long, Integer> perTag = new HashMap<>();
    private final Map<Integer, Integer> counts = new HashMap<>();

    private static Bank cache;

    private Bank(Map<String, Object> root) {
        Object format = root.get("format");
        if (!FORMAT.equals(format)) {
            throw new IllegalArgumentException("این فایل بانک مدو نیست (format=" + format + ")");
        }
        this.version = MiniJson.integer(root, "version", 0);
        if (version > SUPPORTED_VERSION) {
            throw new IllegalArgumentException("نسخهٔ فایل از برنامه جدیدتر است (v" + version + ")");
        }
        this.generatedAt = MiniJson.str(root, "generated_at", "");
        Map<String, Object> stats = MiniJson.asMap(root.get("stats"));
        this.total = MiniJson.integer(stats, "total", 0);
        this.withAnswer = MiniJson.integer(stats, "with_answer", 0);
        this.withExplanation = MiniJson.integer(stats, "with_explanation", 0);

        for (Object raw : MiniJson.asList(root.get("tree"))) {
            roots.add(Node.from(MiniJson.asMap(raw), 0));
        }
        index(roots);
        for (Object raw : MiniJson.asList(root.get("chunks"))) {
            Map<String, Object> chunk = MiniJson.asMap(raw);
            chunkFiles.add(chunk);
            chunkPaths.put(MiniJson.integer(chunk, "id", 0),
                    MiniJson.str(chunk, "file", ""));
        }
        for (Object raw : MiniJson.asList(root.get("questions"))) {
            Question q = Question.from(MiniJson.asMap(raw));
            positions.put(q.id, questions.size());
            questions.add(q);
            byId.put(q.id, q);
            lights.put(q.id, q);
            // A question can sit in several subtopics of the same topic; it
            // counts once per topic badge and once per subtopic badge.
            Set<Integer> seenTopics = new HashSet<>();
            for (int[] node : q.nodes) {
                if (node[0] <= 0) {
                    continue;
                }
                if (node[1] > 0) {
                    Long k = key(node[0], node[1]);
                    Integer seen = perTag.get(k);
                    perTag.put(k, seen == null ? 1 : seen + 1);
                }
                if (seenTopics.add(node[0])) {
                    Integer n = perTopic.get(node[0]);
                    perTopic.put(node[0], n == null ? 1 : n + 1);
                }
            }
        }
        for (Node top : roots) {
            count(top);           // memoise every subtree once
        }
    }

    public static Bank parse(String json) {
        return new Bank(MiniJson.asMap(MiniJson.parse(json)));
    }

    /** Read either format: the v2 zip, or the old single JSON document. */
    public static Bank load(File file) throws Exception {
        if (isZip(file)) {
            return parseZip(file);
        }
        return parse(Io.readText(file));
    }

    public static boolean isZip(File file) throws Exception {
        FileInputStream in = new FileInputStream(file);
        try {
            return in.read() == 'P' && in.read() == 'K';
        } finally {
            in.close();
        }
    }

    /**
     * Zip bundle: the index only (tree, counts, light entries).  A chunk body
     * is read the first time one of its questions is opened and only the last
     * two chunks stay in memory, so a 20k bank starts small instead of loading
     * every lesson up front (a 128 MB phone heap used to run out).
     */
    public static Bank parseZip(File file) throws Exception {
        ZipFile zip = new ZipFile(file);
        try {
            ZipEntry indexEntry = zip.getEntry("index.json");
            if (indexEntry == null) {
                throw new IllegalArgumentException("فایل بانک ناقص است (index.json ندارد)");
            }
            Map<String, Object> index = MiniJson.asMap(
                    MiniJson.parse(readEntry(zip, indexEntry)));
            Bank bank = new Bank(index);
            bank.zipFile = file;
            return bank;
        } finally {
            zip.close();
        }
    }

    /** The index entry for this id, never reading a chunk. */
    public Question light(int id) {
        Question q = lights.get(id);
        return q != null ? q : byId.get(id);
    }

    /** True when this id's chunk body is currently in memory. */
    public boolean isHydrated(int id) {
        Question q = byId.get(id);
        return q != null && q.bodyLoaded;
    }

    /**
     * One question with its options and lesson; reads its chunk if needed.
     *
     * A light entry is always returned when the chunk is missing, so a damaged
     * archive still shows stems instead of crashing.
     */
    public Question question(int id) {
        Question q = byId.get(id);
        if (q == null || q.bodyLoaded || zipFile == null) {
            return q;
        }
        String path = chunkPaths.get(q.chunkId);
        if (path == null || path.isEmpty()) {
            return q;
        }
        loadChunk(path);
        Question full = byId.get(id);
        return full == null ? q : full;
    }

    /**
     * Fully hydrated copies for a bulk writer (PDF).
     *
     * The result keeps every body alive, so this is only for a deliberate
     * export, never for the list/quiz paths.
     */
    public List<Question> fullQuestions(List<Question> items) {
        List<Question> out = new ArrayList<>(items.size());
        for (Question q : items) {
            Question full = question(q.id);
            out.add(full == null ? q : full);
        }
        return out;
    }

    /** Read one chunk entry and promote/demote the LRU pair. */
    private List<Question> loadChunk(String path) {
        synchronized (loadLock) {
            List<Question> cached = loadedChunks.get(path);
            if (cached != null) {
                chunkOrder.remove(path);
                chunkOrder.addLast(path);
                return cached;
            }
            List<Question> parsed = new ArrayList<>();
            ZipFile zip = null;
            try {
                zip = new ZipFile(zipFile);
                ZipEntry entry = zip.getEntry(path);
                if (entry == null) {
                    return parsed;          // a missing chunk loses one category
                }
                Map<String, Object> body = MiniJson.asMap(
                        MiniJson.parse(readEntry(zip, entry)));
                for (Object raw : MiniJson.asList(body.get("questions"))) {
                    Question q = Question.from(MiniJson.asMap(raw));
                    parsed.add(q);
                    Integer at = positions.get(q.id);
                    if (at == null) {
                        positions.put(q.id, questions.size());
                        questions.add(q);
                    } else {
                        questions.set(at, q);
                    }
                    byId.put(q.id, q);
                }
            } catch (Exception ignored) {
                // A broken chunk must not break the rest of the archive.
            } finally {
                if (zip != null) {
                    try {
                        zip.close();
                    } catch (Exception ignored) {
                        // nothing useful to do on close failure
                    }
                }
            }
            if (!parsed.isEmpty()) {
                loadedChunks.put(path, parsed);
                chunkOrder.addLast(path);
                while (chunkOrder.size() > 2) {
                    demote(chunkOrder.removeFirst());
                }
            }
            return parsed;
        }
    }

    /** Replace an evicted chunk's bodies with their light index entries. */
    private void demote(String path) {
        List<Question> old = loadedChunks.remove(path);
        if (old == null) {
            return;
        }
        for (Question q : old) {
            Question light = lights.get(q.id);
            Integer at = positions.get(q.id);
            if (light != null && at != null) {
                questions.set(at, light);
                byId.put(q.id, light);
            }
        }
    }

    private static String readEntry(ZipFile zip, ZipEntry entry) throws Exception {
        InputStream in = zip.getInputStream(entry);
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[16 * 1024];
            int read;
            while ((read = in.read(buffer)) > 0) {
                out.write(buffer, 0, read);
            }
            return new String(out.toByteArray(), "UTF-8");
        } finally {
            in.close();
        }
    }

    private void index(List<Node> nodes) {
        for (Node n : nodes) {
            if (n.sourceUrl != null && !n.sourceUrl.isEmpty()) {
                byUrl.put(n.sourceUrl, n);
            }
            index(n.children);
        }
    }

    // ------------------------------------------------------------- accessors

    public static Bank cached() {
        return cache;
    }

    public static void remember(Bank bank) {
        cache = bank;
    }

    /** Word -> ids, built once on the first search and kept for the session. */
    private void ensureWordIndex() {
        if (wordIndex != null) {
            return;
        }
        Map<String, List<Integer>> index = new HashMap<>();
        for (Question q : questions) {
            StringBuilder text = new StringBuilder(q.stemText)
                    .append(' ').append(q.path);
            for (Question.Option o : q.options) {
                text.append(' ').append(o.text);
            }
            for (String word : Text.normalizeSearch(text.toString())
                    .split("[^\\p{L}\\p{N}]+")) {
                if (word.length() < 2) {
                    continue;
                }
                List<Integer> ids = index.get(word);
                if (ids == null) {
                    ids = new ArrayList<>();
                    index.put(word, ids);
                }
                if (ids.isEmpty() || ids.get(ids.size() - 1) != q.id) {
                    ids.add(q.id);
                }
            }
        }
        wordIndex = index;
    }

    /** Every question whose stem/options contain ALL the words of the query. */
    public Set<Integer> searchIds(String query) {
        String needle = Text.normalizeSearch(query);
        if (needle.isEmpty()) {
            return new HashSet<>(byId.keySet());
        }
        ensureWordIndex();
        Set<Integer> current = null;
        for (String word : needle.split("[^\\p{L}\\p{N}]+")) {
            if (word.isEmpty()) {
                continue;
            }
            List<Integer> ids = wordIndex.get(word);
            if (ids == null) {
                return new HashSet<>();
            }
            if (current == null) {
                current = new HashSet<>(ids);
            } else {
                current.retainAll(new HashSet<>(ids));
            }
            if (current.isEmpty()) {
                return current;
            }
        }
        return current == null ? new HashSet<>(byId.keySet()) : current;
    }

    public Node node(String sourceUrl) {
        return sourceUrl == null ? null : byUrl.get(sourceUrl);
    }

    public int size() {
        return questions.size();
    }

    /** Every question in the subtree, in the order the site pages them. */
    public List<Question> forNode(Node node) {
        if (node == null) {
            return new ArrayList<>(questions);
        }
        Set<Integer> wholeTopics = new HashSet<>();
        Set<Long> tagged = new HashSet<>();
        collectKeys(node, wholeTopics, tagged);
        List<Question> out = new ArrayList<>();
        for (Question q : questions) {
            for (int[] n : q.nodes) {
                if (wholeTopics.contains(n[0])
                        || (n[1] > 0 && tagged.contains(key(n[0], n[1])))) {
                    out.add(q);
                    break;
                }
            }
        }
        return out;
    }

    private static void collectKeys(Node node, Set<Integer> wholeTopics, Set<Long> tagged) {
        int topic = topicId(node.sourceUrl);
        int tag = tagId(node.sourceUrl);
        if (topic > 0) {
            if (tag > 0) {
                tagged.add(key(topic, tag));
            } else {
                wholeTopics.add(topic);
            }
        }
        for (Node child : node.children) {
            collectKeys(child, wholeTopics, tagged);
        }
    }

    /** Questions the archive can grade (the site revealed an answer). */
    public int withAnswers() {
        int n = 0;
        for (Question q : questions) {
            if (q.hasAnswer()) {
                n++;
            }
        }
        return n;
    }

    /**
     * The closest node above (or at) this one that owns stored questions.
     *
     * A subtopic is a filtered slice of its topic; when the archive was built
     * from the unfiltered topic page the slice is empty, and tapping it must
     * not be a dead end.
     */
    public Node nearestWithQuestions(Node node) {
        Node cursor = node;
        while (cursor != null) {
            if (countFor(cursor) > 0) {
                return cursor;
            }
            cursor = cursor.parent;
        }
        return null;
    }

    /** Questions with options, i.e. ones a quiz can actually show. */
    public List<Question> answerable() {
        List<Question> out = new ArrayList<>();
        for (Question q : questions) {
            if (q.hasOptions()) {
                out.add(q);
            }
        }
        return out;
    }

    public List<Question> random(int count) {
        List<Question> pool = answerable();
        Collections.shuffle(pool, new Random());
        return new ArrayList<>(pool.subList(0, Math.min(count, pool.size())));
    }

    /**
     * Total questions under a node, for the row badge.
     *
     * Counted from an index rather than by filtering every question: the tree
     * has thousands of rows and the list asks this for each visible one.
     */
    public int countFor(Node node) {
        Integer cached = counts.get(node.id);
        return cached == null ? count(node) : cached;
    }

    private int count(Node node) {
        int topic = topicId(node.sourceUrl);
        int tag = tagId(node.sourceUrl);
        int total = 0;
        if (topic > 0) {
            if (tag > 0) {
                Integer n = perTag.get(key(topic, tag));
                total = n == null ? 0 : n;
            } else {
                Integer n = perTopic.get(topic);
                total = n == null ? 0 : n;
            }
        }
        for (Node child : node.children) {
            int childCount = count(child);      // always memoised, even if skipped
            // A subtopic is a slice of its topic's pool, and this node already
            // counted every question of that pool: adding the slice again made
            // a topic read «۳۰ + ۱۰» on its badge. Same rule the web app uses.
            if (topic > 0 && tag == 0 && topicId(child.sourceUrl) == topic) {
                continue;
            }
            total += childCount;
        }
        if (node.id > 0) {
            counts.put(node.id, total);
        }
        return total;
    }

    // ------------------------------------------------------------ url pieces

    private static long key(int topic, int tag) {
        return ((long) topic << 20) | tag;
    }

    /** "/qbank/topic-id-79?tag=3" -> 79 */
    public static int topicId(String url) {
        if (url == null) {
            return 0;
        }
        int at = url.indexOf("topic-id-");
        if (at < 0) {
            return 0;
        }
        at += "topic-id-".length();
        int end = at;
        while (end < url.length() && Character.isDigit(url.charAt(end))) {
            end++;
        }
        if (end == at) {
            return 0;
        }
        try {
            return Integer.parseInt(url.substring(at, end));
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    /** "/qbank/topic-id-79?tag=3" -> 3, 0 when the node is the whole topic. */
    public static int tagId(String url) {
        if (url == null) {
            return 0;
        }
        int at = url.indexOf("tag=");
        if (at < 0) {
            return 0;
        }
        at += "tag=".length();
        int end = at;
        while (end < url.length() && Character.isDigit(url.charAt(end))) {
            end++;
        }
        if (end == at) {
            return 0;
        }
        try {
            return Integer.parseInt(url.substring(at, end));
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    // ----------------------------------------------------------------- tree

    /** Depth-first rows reflecting the expanded state (drives the ListView). */
    public static void flatten(List<Node> nodes, List<Node> out) {
        for (Node n : nodes) {
            out.add(n);
            if (n.expanded) {
                flatten(n.children, out);
            }
        }
    }

    /** Every node, regardless of the expanded flags — used by search. */
    public static void flattenAll(List<Node> nodes, List<Node> out) {
        for (Node n : nodes) {
            out.add(n);
            flattenAll(n.children, out);
        }
    }
}
