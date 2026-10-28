package ir.medo.app;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** One multiple-choice question, exactly as the .medo bundle carries it. */
public final class Question {

    public static final class Option {
        public final int index;      // 1-based, as printed on the site
        public final String text;

        Option(int index, String text) {
            this.index = index;
            this.text = text;
        }
    }

    public final int id;
    public final int topicId;
    public final int tagId;          // 0 when the question belongs to the whole topic
    /** Every node this question sits in: {topicId, tagId}.  A question can be
     *  reachable through its topic and several «?tag=» subtopics at once; the
     *  single topicId/tagId pair above is only the first one. */
    public final List<int[]> nodes;
    /** Zip chunk that holds this question's body (-1 for a v1 JSON bank). */
    public final int chunkId;
    /** False while only the light index entry is in memory. */
    public final boolean bodyLoaded;
    public final String sourceUrl;
    public final int page;
    public final int ordinal;
    public final String stemHtml;
    public final String stemText;
    public final List<Option> options;
    public final int correct;        // 0 = the archive has no answer for it
    public final String answerSource;
    public final String explanationHtml;
    public final String explanationText;
    public final String explanationStatus;
    public final boolean hasImages;
    public final String path;
    public final boolean bookmarked;
    public final int selected;       // answer already recorded in the web UI, 0 = none
    /** Option index -> site vote share (0-100), the same number the desktop
     *  app prints under each option. Empty when the page had no poll. */
    public final Map<Integer, Integer> votes;

    private final int optionsInIndex;      // -1 = an old index without the count
    private final boolean lessonInIndex;

    private Question(Map<String, Object> m) {
        this.chunkId = MiniJson.integer(m, "chunk", -1);
        // Chunk bodies carry stem_html; index entries deliberately do not.
        this.bodyLoaded = m.get("stem_html") != null;
        this.optionsInIndex = MiniJson.integer(m, "options_count", -1);
        this.lessonInIndex = MiniJson.bool(m, "has_explanation");
        this.id = MiniJson.integer(m, "id", 0);
        this.topicId = MiniJson.integer(m, "topic_id", 0);
        this.tagId = MiniJson.integer(m, "tag_id", 0);
        List<int[]> nodeList = new ArrayList<>();
        for (Object raw : MiniJson.asList(m.get("nodes"))) {
            Map<String, Object> n = MiniJson.asMap(raw);
            int nodeTopic = MiniJson.integer(n, "topic_id", 0);
            if (nodeTopic > 0) {
                nodeList.add(new int[]{nodeTopic, MiniJson.integer(n, "tag_id", 0)});
            }
        }
        if (nodeList.isEmpty() && this.topicId > 0) {
            // A bundle written before question_nodes only has the one pair.
            nodeList.add(new int[]{this.topicId, Math.max(0, this.tagId)});
        }
        this.nodes = nodeList;
        this.sourceUrl = MiniJson.str(m, "source_url", "");
        this.page = MiniJson.integer(m, "page", 0);
        this.ordinal = MiniJson.integer(m, "ordinal", 0);
        this.stemHtml = MiniJson.str(m, "stem_html", "");
        this.stemText = MiniJson.str(m, "stem_text", "");
        List<Option> opts = new ArrayList<>();
        for (Object raw : MiniJson.asList(m.get("options"))) {
            Map<String, Object> o = MiniJson.asMap(raw);
            int index = MiniJson.integer(o, "index", 0);
            if (index > 0) {
                opts.add(new Option(index, MiniJson.str(o, "text", "")));
            }
        }
        this.options = opts;
        this.correct = MiniJson.integer(m, "correct", 0);
        this.answerSource = MiniJson.str(m, "answer_source", "unknown");
        this.explanationHtml = MiniJson.str(m, "explanation_html", "");
        this.explanationText = MiniJson.str(m, "explanation_text", "");
        this.explanationStatus = MiniJson.str(m, "explanation_status", "unknown");
        this.hasImages = MiniJson.bool(m, "has_images");
        this.path = MiniJson.str(m, "path", "");
        this.bookmarked = MiniJson.bool(m, "bookmarked");
        this.selected = MiniJson.integer(m, "selected", 0);

        Map<Integer, Integer> share = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : MiniJson.asMap(m.get("votes")).entrySet()) {
            Object value = e.getValue();
            int pct = value instanceof Number ? ((Number) value).intValue() : 0;
            try {
                if (pct > 0) {
                    share.put(Integer.parseInt(e.getKey()), pct);
                }
            } catch (NumberFormatException ignored) {
                // A malformed key must not break the whole question.
            }
        }
        this.votes = share;
    }

    static Question from(Map<String, Object> map) {
        return new Question(map);
    }

    public boolean hasAnswer() {
        return correct > 0;
    }

    public boolean hasExplanation() {
        return lessonInIndex || !explanationText.isEmpty() || !explanationHtml.isEmpty();
    }

    /** Options this question has, without reading its chunk when possible. */
    public int optionCount() {
        return bodyLoaded ? options.size() : optionsInIndex;
    }

    /** A quiz may show it: enough options, or an old index that cannot tell. */
    public boolean hasOptions() {
        return options.size() >= 2 || (!bodyLoaded && optionsInIndex < 0);
    }

    /** The option with the largest site poll share, or null without votes. */
    public Integer bestVoteOption() {
        Integer best = null;
        int max = -1;
        for (Map.Entry<Integer, Integer> e : votes.entrySet()) {
            int pct = e.getValue() == null ? 0 : e.getValue();
            if (pct > max) {
                max = pct;
                best = e.getKey();
            }
        }
        return max > 0 ? best : null;
    }

    public Option option(int index) {
        for (Option o : options) {
            if (o.index == index) {
                return o;
            }
        }
        return null;
    }

    /** "سوال ۱۲ — صفحه ۳" style meta line (digits are converted by the UI). */
    public String meta(int position, int total) {
        StringBuilder b = new StringBuilder();
        b.append("سوال ").append(position).append(" از ").append(total);
        if (page > 0) {
            b.append("  ·  صفحه ").append(page);
        }
        return b.toString();
    }
}
