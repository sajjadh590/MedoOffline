package ir.medo.app;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Answers and bookmarks live only on the phone: the app never talks to the
 * internet, so this small JSON file is the single source of progress.
 */
public final class StateStore {

    private static final int VERSION = 1;

    private final File file;
    private final Map<Integer, Integer> answers = new LinkedHashMap<>();
    private final Set<Integer> marks = new LinkedHashSet<>();

    public StateStore(File file) {
        this.file = file;
        load();
    }

    public File file() {
        return file;
    }

    /** Restores saved progress; a corrupt or missing file means "start fresh". */
    public void load() {
        if (file == null || !file.isFile()) {
            return;
        }
        try {
            String text = Io.readText(file);
            Map<String, Object> root = MiniJson.asMap(MiniJson.parse(text));
            answers.clear();
            marks.clear();
            for (Map.Entry<String, Object> e : MiniJson.asMap(root.get("answers")).entrySet()) {
                if (e.getValue() instanceof Number) {
                    try {
                        answers.put(Integer.parseInt(e.getKey()), ((Number) e.getValue()).intValue());
                    } catch (NumberFormatException ignored) {
                        // Skip a hand-edited key rather than losing the file.
                    }
                }
            }
            for (Object raw : MiniJson.asList(root.get("marks"))) {
                if (raw instanceof Number) {
                    marks.add(((Number) raw).intValue());
                }
            }
        } catch (RuntimeException | IOException e) {
            answers.clear();
            marks.clear();
        }
    }

    public synchronized int answer(int questionId) {
        Integer value = answers.get(questionId);
        return value == null ? 0 : value;
    }

    public synchronized void setAnswer(int questionId, int option) {
        if (option <= 0) {
            answers.remove(questionId);
        } else {
            answers.put(questionId, option);
        }
        save();
    }

    public synchronized boolean marked(int questionId) {
        return marks.contains(questionId);
    }

    public synchronized void setMarked(int questionId, boolean on) {
        if (on) {
            marks.add(questionId);
        } else {
            marks.remove(questionId);
        }
        save();
    }

    public synchronized int answeredCount() {
        return answers.size();
    }

    public synchronized int answeredCount(List<Question> questions) {
        int n = 0;
        for (Question q : questions) {
            if (answers.containsKey(q.id)) {
                n++;
            }
        }
        return n;
    }

    /** Writes through a temp file so a killed process cannot truncate progress. */
    public synchronized void save() {
        if (file == null) {
            return;
        }
        Map<String, Object> root = new LinkedHashMap<>();
        root.put("v", VERSION);
        Map<String, Object> plain = new LinkedHashMap<>();
        for (Map.Entry<Integer, Integer> e : answers.entrySet()) {
            plain.put(String.valueOf(e.getKey()), e.getValue());
        }
        root.put("answers", plain);
        root.put("marks", new ArrayList<Object>(marks));

        File tmp = new File(file.getAbsolutePath() + ".tmp");
        try {
            File parent = file.getParentFile();
            if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
                return;
            }
            FileOutputStream out = new FileOutputStream(tmp);
            try {
                out.write(MiniJson.write(root).getBytes("UTF-8"));
            } finally {
                out.close();
            }
            if (!tmp.renameTo(file)) {
                file.delete();
                tmp.renameTo(file);
            }
        } catch (IOException e) {
            // Progress is best-effort on disk; the in-memory copy stays usable.
            tmp.delete();
        }
    }
}
