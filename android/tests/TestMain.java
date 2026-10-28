package ir.medo.app;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.EncodeHintType;
import com.google.zxing.MultiFormatWriter;
import com.google.zxing.common.BitMatrix;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Logic tests for the Android app, run on the desktop JVM.
 *
 * Everything under test is deliberately free of android.* imports, so the real
 * bundle can be parsed and quizzed here — no emulator, no phone. Usage:
 *
 *   java -cp <classes> ir.medo.app.TestMain [path/to/medo.medo]
 */
public final class TestMain {

    private static int passed;
    private static final List<String> failures = new ArrayList<>();

    public static void main(String[] args) {
        String bundlePath = args.length > 0 ? args[0] : "dist/medo.medo";

        jsonBasics();
        jsonErrors();
        stateStoreLifecycle();
        quizFlow();
        urlParsing();
        taggedCounts();
        sharedQuestionNodes();
        searchNormalization();
        scanTargets();
        qrDecoding();
        subtopicsAndAnswers();
        realBundle(new File(bundlePath));

        System.out.println();
        if (failures.isEmpty()) {
            System.out.println("ALL PASSED (" + passed + " checks)");
            return;
        }
        System.out.println("FAILED " + failures.size() + " of " + (passed + failures.size()) + " checks:");
        for (String f : failures) {
            System.out.println("  - " + f);
        }
        System.exit(1);
    }

    // ------------------------------------------------------------- QR decoding

    /** The scanner must read a real QR from the same NV21-shaped frame the
     *  camera hands it — upright, dark-theme (inverted), and empty. */
    private static void qrDecoding() {
        String text = "http://192.168.1.20:8765";
        try {
            Map<EncodeHintType, Object> hints = new HashMap<>();
            hints.put(EncodeHintType.MARGIN, 4);
            BitMatrix modules = new MultiFormatWriter().encode(
                    text, BarcodeFormat.QR_CODE, 0, 0, hints);

            int scale = 6;
            int pad = 24;
            int width = modules.getWidth() * scale + pad * 2;
            int height = modules.getHeight() * scale + pad * 2;
            byte[] frame = nv21(modules, scale, pad);
            eq("qr decodes", text, QrDecoder.decode(frame, width, height));

            byte[] dark = frame.clone();
            for (int i = 0; i < dark.length; i++) {
                dark[i] = (byte) (0xFF - (dark[i] & 0xFF));
            }
            eq("inverted qr decodes", text, QrDecoder.decode(dark, width, height));
            eq("blank frame decodes to nothing", null,
                    QrDecoder.decode(new byte[width * height], width, height));
            eq("short buffer refuses to decode", null,
                    QrDecoder.decode(new byte[8], width, height));

            // A ROM that pads each preview row: decoding the padded buffer as
            // `width` pixels per row shears the code, so it must be detected.
            int extra = 16;
            byte[] padded = padRows(frame, width, height, extra);
            eq("padded row stride detected", width + extra,
                    QrDecoder.strideOf(padded.length, width, height));
            eq("padded frame decodes", text, QrDecoder.decode(padded, width, height));
        } catch (Exception e) {
            check("qr decoding threw " + e, false);
        }
    }

    /** The Y plane again, with `extra` filler bytes on every row (stride). */
    private static byte[] padRows(byte[] tight, int width, int height, int extra) {
        int stride = width + extra;
        byte[] out = new byte[stride * height * 3 / 2];
        java.util.Arrays.fill(out, (byte) 0xFF);
        for (int row = 0; row < height; row++) {
            System.arraycopy(tight, row * width, out, row * stride, width);
        }
        return out;
    }

    /** Y-plane bytes: white paper, black modules, the encoder's quiet zone. */
    private static byte[] nv21(BitMatrix modules, int scale, int pad) {
        int width = modules.getWidth() * scale + pad * 2;
        int height = modules.getHeight() * scale + pad * 2;
        byte[] y = new byte[width * height];
        Arrays.fill(y, (byte) 0xFF);
        for (int r = 0; r < modules.getHeight(); r++) {
            for (int c = 0; c < modules.getWidth(); c++) {
                if (!modules.get(r, c)) {
                    continue;
                }
                for (int dy = 0; dy < scale; dy++) {
                    int row = pad + r * scale + dy;
                    for (int dx = 0; dx < scale; dx++) {
                        y[row * width + pad + c * scale + dx] = 0;
                    }
                }
            }
        }
        return y;
    }

    // ------------------------------------------------------------- scan target

    private static void scanTargets() {
        eq("scan bank path", ScanTarget.BANK, ScanTarget.kind("http://10.0.0.2:8765/medo.medo"));
        eq("scan apk path", ScanTarget.APK, ScanTarget.kind("http://10.0.0.2:8765/medo.apk"));
        eq("scan apk without scheme", ScanTarget.APK,
                ScanTarget.kind("192.168.1.9:8765/medo.apk\n"));
        eq("scan bare address is a bank", ScanTarget.BANK, ScanTarget.kind("192.168.1.9:8765"));
        eq("scan query stripped", ScanTarget.BANK,
                ScanTarget.kind("http://h:8765/medo.medo?x=1"));
        eq("scan empty", ScanTarget.UNKNOWN, ScanTarget.kind("   "));
        eq("scan normalise adds scheme", "http://h:1/medo.medo",
                ScanTarget.normalize("h:1/medo.medo"));
    }

    /** The archive must never report a subtopic's questions as its own, and a
     *  question without an answer must not be gradeable. */
    private static void subtopicsAndAnswers() {
        String json = "{\"format\":\"medo-bank\",\"version\":1,"
                + "\"stats\":{\"total\":2,\"with_answer\":1},"
                + "\"tree\":[{\"id\":1,\"kind\":\"topic\",\"title\":\"نفرولوژی\","
                + "\"source_url\":\"/qbank/topic-id-1\",\"question_count\":2,\"children\":["
                + "{\"id\":2,\"kind\":\"subtopic\",\"title\":\"سنگ‌ها\","
                + "\"source_url\":\"/qbank/topic-id-1?tag=3\",\"question_count\":1}]}],"
                + "\"questions\":["
                + "{\"id\":1,\"topic_id\":1,\"stem_text\":\"a\",\"correct\":2,"
                + "\"votes\":{\"1\":10,\"2\":70},"
                + "\"options\":[{\"index\":1,\"text\":\"x\"},{\"index\":2,\"text\":\"y\"}]},"
                + "{\"id\":2,\"topic_id\":1,\"stem_text\":\"b\",\"correct\":0,"
                + "\"options\":[{\"index\":1,\"text\":\"x\"},{\"index\":2,\"text\":\"y\"}]}]}";
        Bank bank = Bank.parse(json);
        Node root = bank.roots.get(0);
        Node sub = root.children.get(0);
        eq("topic owns both questions", 2, bank.countFor(root));
        eq("subtopic owns none without tag data", 0, bank.countFor(sub));
        eq("subtopic knows its parent", root, sub.parent);
        eq("empty subtopic climbs to the topic", root, bank.nearestWithQuestions(sub));
        eq("only the answered question is graded", 1, bank.withAnswers());
        eq("best vote option", (Integer) 2, bank.questions.get(0).bestVoteOption());
        check("vote option is null without votes",
                bank.questions.get(1).bestVoteOption() == null);

        try {
            File file = File.createTempFile("medo-answers", ".json");
            file.delete();
            StateStore store = new StateStore(file);
            Quiz quiz = new Quiz(bank.questions, store);
            quiz.select(1);
            eq("one gradable answer", 1, quiz.answeredGradable());
            eq("wrong pick is not counted correct", 0, quiz.correctCount());
            quiz.next();
            quiz.select(1);
            eq("two answers recorded", 2, quiz.answeredCount());
            eq("gradable ignores the answerless question", 1, quiz.answeredGradable());
            eq("answerless pick is not wrong-by-definition", 0, quiz.correctCount());
        } catch (Exception e) {
            check("answer test setup: " + e, false);
        }
    }

    // ---------------------------------------------------------------- search

    /** The shared cases in tests/search_vectors.json, also run by Python + Node. */
    private static void searchNormalization() {
        // Escapes on both sides: the expected value must NOT keep the ZWNJ.
        eq("search: ZWNJ",
                "\u0645\u06CC\u0634\u0648\u062F",
                Text.normalizeSearch("\u0645\u06CC\u200C\u0634\u0648\u062F"));
        eq("search: arabic kaf", "کتاب", Text.normalizeSearch("كتاب"));
        eq("search: alef maqsura", "علی", Text.normalizeSearch("على"));
        eq("search: persian digits", "سؤال 123", Text.normalizeSearch("سؤال ۱۲۳"));
        eq("search: arabic digits", "سؤال 123", Text.normalizeSearch("سؤال ١٢٣"));
        eq("search: invisibles", "procalcitonin", Text.normalizeSearch("Procalcitonin\u200B\uFE0F"));
        eq("search: whitespace", "چند خط متن", Text.normalizeSearch("  چند   خط\nمتن "));
        eq("search: latin case", "abc", Text.normalizeSearch("ABC"));
        eq("search: پروگنوز", "پروگنوز", Text.normalizeSearch("پروگنوز"));
        eq("search: tatweel", "سلام", Text.normalizeSearch("سـلام"));
        eq("search: null", "", Text.normalizeSearch(null));
    }

    // ------------------------------------------------------------------ checks

    private static void check(String name, boolean condition) {
        if (condition) {
            passed++;
        } else {
            failures.add(name);
            System.out.println("FAIL " + name);
        }
    }

    private static void eq(String name, Object expected, Object actual) {
        boolean same = expected == null ? actual == null : expected.equals(actual);
        check(name + " (expected " + expected + ", got " + actual + ")", same);
    }

    // -------------------------------------------------------------------- json

    private static void jsonBasics() {
        String source = "{\"a\":1,\"b\":-2.5,\"c\":\"سلام \\u06F1\\nخط\","
                + "\"d\":[true,false,null],\"e\":{\"f\":\"x\"}}";
        Map<String, Object> root = MiniJson.asMap(MiniJson.parse(source));
        eq("number as long", 1L, root.get("a"));
        eq("fractional number", -2.5, root.get("b"));
        eq("unicode escape + newline", "سلام ۱\nخط", root.get("c"));
        eq("array size", 3, MiniJson.asList(root.get("d")).size());
        eq("null element", null, MiniJson.asList(root.get("d")).get(2));
        eq("nested object", "x", MiniJson.asMap(root.get("e")).get("f"));

        Map<String, Object> out = new HashMap<>();
        out.put("quote", "a\"b\\c");
        out.put("line", "x\ny");
        out.put("num", 12L);
        String written = MiniJson.write(out);
        Map<String, Object> back = MiniJson.asMap(MiniJson.parse(written));
        eq("writer round-trip quote", "a\"b\\c", back.get("quote"));
        eq("writer round-trip newline", "x\ny", back.get("line"));
        eq("writer round-trip number", 12L, back.get("num"));

        Map<String, Object> typed = MiniJson.asMap(MiniJson.parse(
                "{\"t\":true,\"f\":false,\"n\":null,\"s\":\"۰\"}"));
        eq("boolean true", Boolean.TRUE, typed.get("t"));
        eq("boolean false", Boolean.FALSE, typed.get("f"));
        check("null kept", typed.containsKey("n") && typed.get("n") == null);
        eq("persian digit survives", "۰", typed.get("s"));
    }

    private static void jsonErrors() {
        expectThrow("truncated object", "{\"a\":1");
        expectThrow("bad literal", "{\"a\":tru}");
        expectThrow("trailing junk", "{} x");
        expectThrow("unquoted key", "{a:1}");
        expectThrow("bad escape", "\"\\q\"");
    }

    private static void expectThrow(String name, String json) {
        try {
            MiniJson.parse(json);
            check(name + " should throw", false);
        } catch (IllegalArgumentException expected) {
            passed++;
        }
    }

    // ------------------------------------------------------------------- state

    private static void stateStoreLifecycle() {
        try {
            File file = File.createTempFile("medo-state", ".json");
            file.delete();
            StateStore store = new StateStore(file);
            eq("fresh answer", 0, store.answer(7));
            check("fresh bookmark off", !store.marked(7));

            store.setAnswer(7, 3);
            store.setAnswer(8, 1);
            store.setMarked(8, true);
            eq("answer saved", 3, store.answer(7));
            eq("answered count", 2, store.answeredCount());
            check("state file written", file.isFile());

            StateStore reloaded = new StateStore(file);
            eq("answer survives reload", 3, reloaded.answer(7));
            check("bookmark survives reload", reloaded.marked(8));
            check("unmarked stays off", !reloaded.marked(7));
            eq("reloaded count", 2, reloaded.answeredCount());

            reloaded.setAnswer(7, 0);
            eq("cleared answer", 0, new StateStore(file).answer(7));
            eq("count after clear", 1, new StateStore(file).answeredCount());

            store.setMarked(8, false);
            check("unmark survives", !new StateStore(file).marked(8));
            file.delete();
        } catch (Exception e) {
            check("state store threw " + e, false);
        }
    }

    // -------------------------------------------------------------------- quiz

    private static void quizFlow() {
        List<Question> items = fakeQuestions();
        File tmp;
        try {
            tmp = File.createTempFile("medo-quiz", ".json");
            tmp.delete();
        } catch (Exception e) {
            check("temp file", false);
            return;
        }
        StateStore state = new StateStore(tmp);
        Quiz quiz = new Quiz(items, state);
        eq("vote share parsed", 80, items.get(0).votes.get(1));
        eq("vote share count", 2, items.get(0).votes.size());

        eq("starts at first", 1, quiz.position());
        eq("size", 3, quiz.size());
        check("not answered yet", !quiz.answered());

        quiz.select(2);                       // question 1: correct is 2
        check("answered", quiz.answered());
        check("counted correct", quiz.correct());
        eq("score", 1, quiz.correctCount());

        quiz.select(1);                       // a second pick must not overwrite
        eq("first answer wins", 2, quiz.selected());

        check("next works", quiz.next());
        eq("position 2", 2, quiz.position());
        quiz.select(1);                       // question 2: correct is 3
        check("wrong answer detected", !quiz.correct());
        eq("score still 1", 1, quiz.correctCount());
        eq("answered two", 2, quiz.answeredCount());

        quiz.toggleMark();
        check("marked", quiz.marked());

        check("prev works", quiz.prev());
        eq("back to 1", 1, quiz.position());
        check("prev at start is false", !quiz.prev());

        quiz.next();
        quiz.next();
        eq("last position", 3, quiz.position());
        check("no next at end", !quiz.hasNext());
        check("next at end returns false", !quiz.next());

        quiz.goFirst();
        eq("goFirst", 1, quiz.position());

        quiz.goTo(2);
        eq("goTo middle", 3, quiz.position());
        quiz.goTo(-4);
        eq("goTo ignores negative", 3, quiz.position());
        quiz.goTo(99);
        eq("goTo ignores overflow", 3, quiz.position());
        quiz.goFirst();

        quiz.clear();
        eq("cleared current", 0, quiz.selected());
        eq("count after clear", 1, quiz.answeredCount());

        quiz.next();
        quiz.next();
        check("unknown answer is not correct", !quiz.correct());

        check("empty quiz rejected", throwsOnEmpty());
        tmp.delete();
    }

    private static boolean throwsOnEmpty() {
        try {
            StateStore state = new StateStore(null);
            new Quiz(new ArrayList<Question>(), state);
            return false;
        } catch (IllegalArgumentException expected) {
            return true;
        }
    }

    private static List<Question> fakeQuestions() {
        List<Question> items = new ArrayList<>();
        items.add(fake(1, 2));
        items.add(fake(2, 3));
        items.add(fake(3, 0));                 // answer unknown in the archive
        return items;
    }

    private static Question fake(int id, int correct) {
        StringBuilder json = new StringBuilder();
        json.append("{\"id\":").append(id)
                .append(",\"topic_id\":79,\"tag_id\":null,\"source_url\":\"/qbank/topic-id-79\",")
                .append("\"page\":1,\"ordinal\":").append(id)
                .append(",\"stem_html\":\"<p>سوال</p>\",\"stem_text\":\"سوال\",")
                .append("\"options\":[{\"index\":1,\"text\":\"الف\"},{\"index\":2,\"text\":\"ب\"},")
                .append("{\"index\":3,\"text\":\"ج\"}],")
                .append("\"correct\":").append(correct)
                .append(",\"answer_source\":\"marker\",\"explanation_text\":\"توضیح\",")
                .append("\"explanation_status\":\"full\",\"has_images\":false,\"path\":\"طب/آریتمی\",")
                .append("\"votes\":{\"1\":80,\"2\":15}}");
        return Question.from(MiniJson.asMap(MiniJson.parse(json.toString())));
    }

    // --------------------------------------------------------------------- url

    private static void urlParsing() {
        eq("topic id", 79, Bank.topicId("/qbank/topic-id-79"));
        eq("topic id with tag", 83, Bank.topicId("/qbank/topic-id-83?tag=17"));
        eq("tag id", 17, Bank.tagId("/qbank/topic-id-83?tag=17"));
        eq("no tag", 0, Bank.tagId("/qbank/topic-id-79"));
        eq("no topic", 0, Bank.topicId("/qbank/cat-id-2"));
        eq("null url", 0, Bank.topicId(null));
    }

    // --------------------------------------------------------------------- tree

    /** A tagged question is a slice of its topic, never counted twice. */
    private static void taggedCounts() {
        String json = "{\"format\":\"medo-bank\",\"version\":1,\"generated_at\":\"\","
                + "\"stats\":{\"total\":3,\"with_answer\":3},"
                + "\"tree\":[{\"id\":1,\"parent_id\":null,\"kind\":\"topic\","
                + "\"title\":\"مبحث\",\"source_url\":\"/qbank/topic-id-9\",\"position\":1,"
                + "\"question_count\":3,\"children\":["
                + "{\"id\":2,\"parent_id\":1,\"kind\":\"subtopic\",\"title\":\"الف\","
                + "\"source_url\":\"/qbank/topic-id-9?tag=5\",\"position\":1,"
                + "\"question_count\":2,\"children\":[]},"
                + "{\"id\":3,\"parent_id\":1,\"kind\":\"subtopic\",\"title\":\"ب\","
                + "\"source_url\":\"/qbank/topic-id-9?tag=6\",\"position\":2,"
                + "\"question_count\":1,\"children\":[]},"
                + "{\"id\":4,\"parent_id\":1,\"kind\":\"subtopic\",\"title\":\"ج\","
                + "\"source_url\":\"/qbank/topic-id-9?tag=7\",\"position\":3,"
                + "\"question_count\":0,\"children\":[]}]}],"
                + "\"questions\":[" + question(1, 5) + "," + question(2, 5) + ","
                + question(3, 6) + "]}";
        Bank bank = Bank.parse(json);
        Node topic = bank.roots.get(0);
        Node first = topic.children.get(0);
        Node second = topic.children.get(1);
        Node empty = topic.children.get(2);
        eq("topic badge is the whole pool", 3, bank.countFor(topic));
        eq("first subtopic badge", 2, bank.countFor(first));
        eq("second subtopic badge", 1, bank.countFor(second));
        eq("empty subtopic badge", 0, bank.countFor(empty));
        eq("topic list is the pool", 3, bank.forNode(topic).size());
        eq("subtopic list is its slice", 2, bank.forNode(first).size());
        eq("second slice", 1, bank.forNode(second).size());
        eq("empty subtopic falls back to its topic", 3,
                bank.countFor(bank.nearestWithQuestions(empty)));
        eq("nothing above the root", null, bank.nearestWithQuestions(null));
    }

    /** One question can sit in the whole topic and two subtopics at once. */
    private static void sharedQuestionNodes() {
        String shared = "{\"id\":1,\"topic_id\":9,\"tag_id\":5,"
                + "\"nodes\":[{\"topic_id\":9,\"tag_id\":0},"
                + "{\"topic_id\":9,\"tag_id\":5},{\"topic_id\":9,\"tag_id\":6}],"
                + "\"source_url\":\"/qbank/topic-id-9?tag=5\",\"page\":1,\"ordinal\":1,"
                + "\"stem_html\":\"<p>س</p>\",\"stem_text\":\"س\","
                + "\"options\":[{\"index\":1,\"text\":\"الف\"},"
                + "{\"index\":2,\"text\":\"ب\"}],"
                + "\"correct\":1,\"answer_source\":\"color\"}";
        String single = "{\"id\":2,\"topic_id\":9,\"tag_id\":5,"
                + "\"nodes\":[{\"topic_id\":9,\"tag_id\":5}],"
                + "\"source_url\":\"/qbank/topic-id-9?tag=5\",\"page\":1,\"ordinal\":2,"
                + "\"stem_html\":\"<p>س</p>\",\"stem_text\":\"س\","
                + "\"options\":[{\"index\":1,\"text\":\"الف\"},"
                + "{\"index\":2,\"text\":\"ب\"}],"
                + "\"correct\":1,\"answer_source\":\"color\"}";
        String json = "{\"format\":\"medo-bank\",\"version\":1,\"generated_at\":\"\","
                + "\"stats\":{\"total\":2,\"with_answer\":2},"
                + "\"tree\":[{\"id\":1,\"parent_id\":null,\"kind\":\"topic\","
                + "\"title\":\"مبحث\",\"source_url\":\"/qbank/topic-id-9\",\"position\":1,"
                + "\"question_count\":2,\"children\":["
                + "{\"id\":2,\"parent_id\":1,\"kind\":\"subtopic\",\"title\":\"الف\","
                + "\"source_url\":\"/qbank/topic-id-9?tag=5\",\"position\":1,"
                + "\"question_count\":2,\"children\":[]},"
                + "{\"id\":3,\"parent_id\":1,\"kind\":\"subtopic\",\"title\":\"ب\","
                + "\"source_url\":\"/qbank/topic-id-9?tag=6\",\"position\":2,"
                + "\"question_count\":1,\"children\":[]}]}],"
                + "\"questions\":[" + shared + "," + single + "]}";
        Bank bank = Bank.parse(json);
        Node topic = bank.roots.get(0);
        eq("shared question counts once for its topic", 2, bank.countFor(topic));
        eq("first subtopic sees both", 2, bank.countFor(topic.children.get(0)));
        eq("second subtopic sees the shared one", 1, bank.countFor(topic.children.get(1)));
        eq("topic list has both", 2, bank.forNode(topic).size());
        eq("second slice has one", 1, bank.forNode(topic.children.get(1)).size());
        eq("question keeps all nodes", 3, bank.questions.get(0).nodes.size());
    }

    private static String question(int id, int tag) {
        return "{\"id\":" + id + ",\"topic_id\":9,\"tag_id\":" + tag + ","
                + "\"source_url\":\"/qbank/topic-id-9?tag=" + tag + "\",\"page\":1,"
                + "\"ordinal\":" + id + ",\"stem_html\":\"<p>س</p>\",\"stem_text\":\"س\","
                + "\"options\":[{\"index\":1,\"text\":\"الف\"},"
                + "{\"index\":2,\"text\":\"ب\"}],"
                + "\"correct\":1,\"answer_source\":\"color\"}";
    }

    // -------------------------------------------------------------- real bundle

    private static void realBundle(File file) {
        if (!file.isFile()) {
            check("bundle exists at " + file, false);
            return;
        }
        try {
            String text = new String(readAll(file), "UTF-8");
            long started = System.currentTimeMillis();
            Bank bank = Bank.parse(text);
            long parseMs = System.currentTimeMillis() - started;

            check("bundle parsed", bank.size() > 0);
            eq("format version", 1, bank.version);
            check("tree not empty", !bank.roots.isEmpty());
            check("parse under 3s (" + parseMs + "ms)", parseMs < 3000);
            System.out.println("     parsed " + bank.size() + " questions in " + parseMs + " ms");

            Question first = bank.questions.get(0);
            check("question has options", first.options.size() >= 2);
            check("persian stem survives", first.stemText.length() > 0);
            check("question reachable by id", bank.question(first.id) == first);

            Node leaf = findLeafWithQuestions(bank);
            check("found a topic with questions", leaf != null);
            if (leaf != null) {
                List<Question> subset = bank.forNode(leaf);
                int topic = Bank.topicId(leaf.sourceUrl);
                boolean onlyOwnTopic = true;
                for (Question q : subset) {
                    boolean inTopic = false;
                    for (int[] n : q.nodes) {
                        if (n[0] == topic) {
                            inTopic = true;
                        }
                    }
                    if (!inTopic) {
                        onlyOwnTopic = false;
                    }
                }
                check("subtree returns only its own topic", onlyOwnTopic);
                eq("countFor matches the list", subset.size(), bank.countFor(leaf));
                check("badge count > 0", bank.countFor(leaf) > 0);
            }

            int rootCount = 0;
            for (Node root : bank.roots) {
                rootCount += bank.countFor(root);
            }
            check("root badges are not empty", rootCount > 0);

            // Collapsing a root must hide its subtree.
            List<Node> rows = new ArrayList<>();
            Bank.flatten(bank.roots, rows);
            int expandedRows = rows.size();
            Node top = bank.roots.get(0);
            if (top.hasChildren()) {
                top.expanded = false;
                rows.clear();
                Bank.flatten(bank.roots, rows);
                check("collapse hides children", rows.size() < expandedRows);
                top.expanded = true;
            }

            List<Question> random = bank.random(20);
            check("random returns at most 20", random.size() <= 20);
            check("random picks answerable questions",
                    random.isEmpty() || random.get(0).options.size() >= 2);

            try {
                Bank.parse("{\"format\":\"something-else\",\"version\":1}");
                check("rejects foreign file", false);
            } catch (IllegalArgumentException expected) {
                passed++;
            }
            try {
                Bank.parse("{\"format\":\"medo-bank\",\"version\":99}");
                check("rejects newer version", false);
            } catch (IllegalArgumentException expected) {
                passed++;
            }
        } catch (Exception e) {
            check("real bundle threw " + e, false);
        }
    }

    private static Node findLeafWithQuestions(Bank bank) {
        List<Node> stack = new ArrayList<>(bank.roots);
        Node best = null;
        while (!stack.isEmpty()) {
            Node node = stack.remove(0);
            if (!node.hasChildren() && bank.countFor(node) > 0) {
                if (best == null || bank.countFor(node) > bank.countFor(best)) {
                    best = node;
                }
            }
            stack.addAll(node.children);
        }
        return best;
    }

    private static byte[] readAll(File file) throws Exception {
        InputStream in = new FileInputStream(file);
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[16384];
            int n;
            while ((n = in.read(buffer)) > 0) {
                out.write(buffer, 0, n);
            }
            return out.toByteArray();
        } finally {
            in.close();
        }
    }

    private TestMain() {
    }
}
