package ir.medo.app;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A tiny JSON reader/writer.
 *
 * Android ships org.json, but the bank parser and the local progress file are
 * also exercised on the desktop JVM (see android/tests), where org.json does
 * not exist. One small, tested implementation keeps phone and tests identical.
 */
public final class MiniJson {

    private final String text;
    private int at;

    private MiniJson(String text) {
        this.text = text;
    }

    public static Object parse(String json) {
        if (json == null) {
            throw new IllegalArgumentException("empty JSON");
        }
        MiniJson p = new MiniJson(json);
        p.skipSpace();
        Object value = p.readValue();
        p.skipSpace();
        if (p.at != json.length()) {
            throw p.error("unexpected trailing data");
        }
        return value;
    }

    // ---------------------------------------------------------------- reading

    private Object readValue() {
        if (at >= text.length()) {
            throw error("unexpected end");
        }
        char c = text.charAt(at);
        switch (c) {
            case '{':
                return readObject();
            case '[':
                return readArray();
            case '"':
                return readString();
            case 't':
                expect("true");
                return Boolean.TRUE;
            case 'f':
                expect("false");
                return Boolean.FALSE;
            case 'n':
                expect("null");
                return null;
            default:
                return readNumber();
        }
    }

    private Map<String, Object> readObject() {
        Map<String, Object> out = new LinkedHashMap<>();
        at++;                       // {
        skipSpace();
        if (peek() == '}') {
            at++;
            return out;
        }
        while (true) {
            skipSpace();
            if (peek() != '"') {
                throw error("object key must be a string");
            }
            String key = readString();
            skipSpace();
            if (peek() != ':') {
                throw error("missing ':'");
            }
            at++;
            skipSpace();
            out.put(key, readValue());
            skipSpace();
            char c = peek();
            at++;
            if (c == '}') {
                return out;
            }
            if (c != ',') {
                throw error("missing ',' or '}'");
            }
        }
    }

    private List<Object> readArray() {
        List<Object> out = new ArrayList<>();
        at++;                       // [
        skipSpace();
        if (peek() == ']') {
            at++;
            return out;
        }
        while (true) {
            skipSpace();
            out.add(readValue());
            skipSpace();
            char c = peek();
            at++;
            if (c == ']') {
                return out;
            }
            if (c != ',') {
                throw error("missing ',' or ']'");
            }
        }
    }

    private String readString() {
        StringBuilder b = new StringBuilder();
        at++;                       // opening quote
        while (true) {
            if (at >= text.length()) {
                throw error("unterminated string");
            }
            char c = text.charAt(at++);
            if (c == '"') {
                return b.toString();
            }
            if (c != '\\') {
                b.append(c);
                continue;
            }
            if (at >= text.length()) {
                throw error("unterminated escape");
            }
            char e = text.charAt(at++);
            switch (e) {
                case '"': b.append('"'); break;
                case '\\': b.append('\\'); break;
                case '/': b.append('/'); break;
                case 'b': b.append('\b'); break;
                case 'f': b.append('\f'); break;
                case 'n': b.append('\n'); break;
                case 'r': b.append('\r'); break;
                case 't': b.append('\t'); break;
                case 'u':
                    if (at + 4 > text.length()) {
                        throw error("bad \\u escape");
                    }
                    b.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                    at += 4;
                    break;
                default:
                    throw error("bad escape \\" + e);
            }
        }
    }

    private Object readNumber() {
        int start = at;
        boolean fractional = false;
        while (at < text.length()) {
            char c = text.charAt(at);
            if (c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E') {
                if (c == '.' || c == 'e' || c == 'E') {
                    fractional = true;
                }
                at++;
            } else if (c >= '0' && c <= '9') {
                at++;
            } else {
                break;
            }
        }
        String raw = text.substring(start, at);
        if (raw.isEmpty()) {
            throw error("unexpected character '" + text.charAt(start) + "'");
        }
        try {
            return fractional ? (Object) Double.valueOf(raw) : (Object) Long.valueOf(raw);
        } catch (NumberFormatException e) {
            throw error("bad number '" + raw + "'");
        }
    }

    private void expect(String word) {
        if (!text.startsWith(word, at)) {
            throw error("expected " + word);
        }
        at += word.length();
    }

    private char peek() {
        if (at >= text.length()) {
            throw error("unexpected end");
        }
        return text.charAt(at);
    }

    private void skipSpace() {
        while (at < text.length()) {
            char c = text.charAt(at);
            if (c == ' ' || c == '\n' || c == '\r' || c == '\t') {
                at++;
            } else {
                break;
            }
        }
    }

    private IllegalArgumentException error(String message) {
        return new IllegalArgumentException(message + " at character " + at);
    }

    // ---------------------------------------------------------------- writing

    public static String write(Object value) {
        StringBuilder b = new StringBuilder();
        writeValue(b, value);
        return b.toString();
    }

    private static void writeValue(StringBuilder b, Object value) {
        if (value == null) {
            b.append("null");
        } else if (value instanceof String) {
            writeString(b, (String) value);
        } else if (value instanceof Boolean) {
            b.append(value.toString());
        } else if (value instanceof Double || value instanceof Float) {
            double d = ((Number) value).doubleValue();
            if (d == Math.rint(d) && !Double.isInfinite(d)) {
                b.append((long) d);
            } else {
                b.append(d);
            }
        } else if (value instanceof Number) {
            b.append(((Number) value).longValue());
        } else if (value instanceof Map) {
            b.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) value).entrySet()) {
                if (!first) {
                    b.append(',');
                }
                first = false;
                writeString(b, String.valueOf(e.getKey()));
                b.append(':');
                writeValue(b, e.getValue());
            }
            b.append('}');
        } else if (value instanceof Iterable) {
            b.append('[');
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) {
                    b.append(',');
                }
                first = false;
                writeValue(b, item);
            }
            b.append(']');
        } else {
            writeString(b, value.toString());
        }
    }

    private static void writeString(StringBuilder b, String s) {
        b.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) {
                        b.append(String.format("\\u%04x", (int) c));
                    } else {
                        b.append(c);
                    }
            }
        }
        b.append('"');
    }

    // --------------------------------------------------------------- helpers

    @SuppressWarnings("unchecked")
    public static Map<String, Object> asMap(Object value) {
        return value instanceof Map ? (Map<String, Object>) value : new LinkedHashMap<String, Object>();
    }

    @SuppressWarnings("unchecked")
    public static List<Object> asList(Object value) {
        return value instanceof List ? (List<Object>) value : new ArrayList<Object>();
    }

    public static String str(Map<String, Object> map, String key, String fallback) {
        Object v = map.get(key);
        return v instanceof String ? (String) v : fallback;
    }

    public static int integer(Map<String, Object> map, String key, int fallback) {
        Object v = map.get(key);
        if (v instanceof Number) {
            return ((Number) v).intValue();
        }
        if (v instanceof String) {
            try {
                return Integer.parseInt((String) v);
            } catch (NumberFormatException ignored) {
                return fallback;
            }
        }
        return fallback;
    }

    public static boolean bool(Map<String, Object> map, String key) {
        Object v = map.get(key);
        return v instanceof Boolean ? (Boolean) v : false;
    }

    private MiniJson() {
        this.text = "";
    }
}
