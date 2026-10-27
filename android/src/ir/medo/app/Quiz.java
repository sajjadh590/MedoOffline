package ir.medo.app;

import java.util.List;

/**
 * Quiz session state: which question is showing, what the user picked, and the
 * running score. No Android imports — android/tests exercises this on the JVM.
 */
public final class Quiz {

    private final List<Question> items;
    private final StateStore state;
    private int index;

    public Quiz(List<Question> items, StateStore state) {
        if (items == null || items.isEmpty()) {
            throw new IllegalArgumentException("no questions");
        }
        this.items = items;
        this.state = state;
    }

    public List<Question> items() {
        return items;
    }

    public Question current() {
        return items.get(index);
    }

    public int position() {
        return index + 1;
    }

    public int size() {
        return items.size();
    }

    // ------------------------------------------------------------- answering

    public int selected() {
        return state.answer(current().id);
    }

    public boolean answered() {
        return selected() > 0;
    }

    public boolean correct() {
        Question q = current();
        return q.hasAnswer() && selected() == q.correct;
    }

    /** Records the pick. The first answer stands until {@link #clear()}. */
    public void select(int option) {
        if (option <= 0 || answered()) {
            return;
        }
        Question q = current();
        for (Question.Option o : q.options) {
            if (o.index == option) {
                state.setAnswer(q.id, option);
                return;
            }
        }
    }

    public void clear() {
        state.setAnswer(current().id, 0);
    }

    public boolean marked() {
        return state.marked(current().id);
    }

    public void toggleMark() {
        state.setMarked(current().id, !marked());
    }

    // ------------------------------------------------------------ navigation

    public boolean hasNext() {
        return index + 1 < items.size();
    }

    public boolean next() {
        if (!hasNext()) {
            return false;
        }
        index++;
        return true;
    }

    public boolean prev() {
        if (index == 0) {
            return false;
        }
        index--;
        return true;
    }

    /** Back to the first question; recorded answers stay put. */
    public void goFirst() {
        index = 0;
    }

    /** Jump to a 0-based position (used when the list opens one question). */
    public void goTo(int position) {
        if (position >= 0 && position < items.size()) {
            index = position;
        }
    }

    public int answeredCount() {
        int n = 0;
        for (Question q : items) {
            if (state.answer(q.id) > 0) {
                n++;
            }
        }
        return n;
    }

    /** Answered questions that the archive can actually grade. */
    public int answeredGradable() {
        int n = 0;
        for (Question q : items) {
            if (state.answer(q.id) > 0 && q.hasAnswer()) {
                n++;
            }
        }
        return n;
    }

    public int correctCount() {
        int n = 0;
        for (Question q : items) {
            int picked = state.answer(q.id);
            if (q.hasAnswer() && picked > 0 && picked == q.correct) {
                n++;
            }
        }
        return n;
    }
}
