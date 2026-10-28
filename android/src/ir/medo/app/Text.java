package ir.medo.app;

import java.text.Normalizer;
import java.util.Locale;

/**
 * One search canonicalisation for the whole app.
 *
 * Two screens used to carry their own copy (and the web/Python side a third),
 * so a query typed with an Arabic yeh matched on one screen and not the other.
 * The rules are the same everywhere: Arabic glyphs become Persian, ZWNJ and
 * the site's invisible code points disappear, Persian/Arabic digits become
 * ASCII, tatweel goes, whitespace collapses and case folds.
 *
 * `tests/search_vectors.json` is the shared contract with medo/text_utils.py
 * and medo/web/normalize.js.
 */
public final class Text {

    private Text() {
    }

    public static String normalizeSearch(String value) {
        if (value == null) {
            return "";
        }
        String text = Normalizer.normalize(value, Normalizer.Form.NFKC);
        StringBuilder out = new StringBuilder(text.length());
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (isInvisible(ch)) {
                continue;
            }
            if (ch == '\u064A' || ch == '\u0649') {
                out.append('\u06CC');          // Arabic yeh / alef maqsura
            } else if (ch == '\u0643') {
                out.append('\u06A9');          // Arabic kaf
            } else if (ch == '\u0640') {
                // tatweel: decoration, not a letter
            } else if (ch >= '\u06F0' && ch <= '\u06F9') {
                out.append((char) ('0' + (ch - '\u06F0')));
            } else if (ch >= '\u0660' && ch <= '\u0669') {
                out.append((char) ('0' + (ch - '\u0660')));
            } else {
                out.append(ch);
            }
        }
        return out.toString().replaceAll("\\s+", " ").trim().toLowerCase(Locale.ROOT);
    }

    /** The same set as medo/text_utils.py — ZWNJ (U+200C) included. */
    private static boolean isInvisible(char ch) {
        return ch == '\u00AD' || ch == '\u034F' || ch == '\u061C'
                || ch == '\u200B' || (ch >= '\u200C' && ch <= '\u200F')
                || (ch >= '\u202A' && ch <= '\u202E')
                || (ch >= '\u2060' && ch <= '\u2069')
                || (ch >= '\uFE00' && ch <= '\uFE0F')
                || ch == '\uFEFF';
    }
}
