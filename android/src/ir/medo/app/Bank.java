package ir.medo.app;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;

/** The whole offline archive: tree + questions, plus topic matching. */
public final class Bank {

    public static final String FORMAT = "medo-bank";
    public static final int SUPPORTED_VERSION = 1;

    public final int version;
    public final String generatedAt;
    public final int total;
    public final int withAnswer;
    public final int withExplanation;
    public final List<Node> roots = new ArrayList<>();
    public final List<Question> questions = new ArrayList<>();

    private final Map<Integer, Question> byId = new HashMap<>();
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
        for (Object raw : MiniJson.asList(root.get("questions"))) {
            Question q = Question.from(MiniJson.asMap(raw));
            questions.add(q);
            byId.put(q.id, q);
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

    public Question question(int id) {
        return byId.get(id);
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
            if (q.options.size() >= 2) {
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
