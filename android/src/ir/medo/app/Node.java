package ir.medo.app;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** One row of the site's category tree (category -> lesson -> section -> topic). */
public final class Node {

    public final int id;
    public final int parentId;
    public final String kind;
    public final String title;
    public final String sourceUrl;
    public final int position;
    public final int declared;          // question_count advertised by the site
    public final int scraped;           // questions actually stored for this node
    public final String status;         // completed | running | paused | null
    public final boolean partial;
    public final List<Node> children = new ArrayList<>();

    /** UI state, not part of the file. */
    public int depth;
    public boolean expanded;
    /** The row above this one, so an empty subtopic can point at its topic. */
    public Node parent;

    private Node(Map<String, Object> m) {
        this.id = MiniJson.integer(m, "id", 0);
        this.parentId = MiniJson.integer(m, "parent_id", 0);
        this.kind = MiniJson.str(m, "kind", "topic");
        this.title = MiniJson.str(m, "title", "");
        this.sourceUrl = MiniJson.str(m, "source_url", "");
        this.position = MiniJson.integer(m, "position", 0);
        this.declared = MiniJson.integer(m, "question_count", 0);
        Map<String, Object> progress = MiniJson.asMap(m.get("progress"));
        this.scraped = MiniJson.integer(progress, "scraped", 0);
        this.status = MiniJson.str(progress, "status", null);
        this.partial = MiniJson.bool(m, "partial");
    }

    static Node from(Map<String, Object> map, int depth) {
        Node node = new Node(map);
        node.depth = depth;
        for (Object raw : MiniJson.asList(map.get("children"))) {
            Node child = Node.from(MiniJson.asMap(raw), depth + 1);
            child.parent = node;
            node.children.add(child);
        }
        node.expanded = depth < 1;      // first level open, everything else folded
        return node;
    }

    public boolean hasChildren() {
        return !children.isEmpty();
    }

    public String label() {
        return title == null || title.isEmpty() ? "بدون عنوان" : title;
    }
}
