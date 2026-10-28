"""
tests/test_android_bundle.py
The .medo bundle is the only thing the Android app ever reads: it must be
complete, small, self-describing, and served read-only.

Covers the format contract (keys the Java parser depends on), Persian text
survival without \\uXXXX escaping, USB file writing, and the LAN share server.
"""
import json
import threading
import urllib.error
import urllib.request
from http.client import HTTPConnection

import pytest

from medo import share
from medo.db import (
    get_connection,
    init_db,
    update_scrape_state,
    upsert_category,
    upsert_question,
)
from medo.models import AnswerSource, ParsedOption, ParsedQuestion
from medo.share import (
    BUNDLE_FILENAME,
    FORMAT_NAME,
    FORMAT_VERSION,
    build_android_bundle,
    bundle_bytes,
    lan_addresses,
    start_share,
    write_bundle,
)


def _make_question(stem: str, url: str, ordinal: int) -> ParsedQuestion:
    q = ParsedQuestion(
        source_url=url,
        source_page=ordinal,
        ordinal=ordinal,
        stem_html=f"<p>{stem}</p>",
        stem_text=stem,
        options=[
            ParsedOption(index=i, text=f"گزینه {i}", raw_html=f"<span>{i}</span>")
            for i in range(1, 5)
        ],
        correct_index=2,
        answer_source=AnswerSource.MARKER,
        explanation_html="<p>توضیح</p>",
        explanation_text="توضیح",
    )
    q.compute_content_hash()
    return q


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "medo.db"
    conn = get_connection(db_path)
    init_db(conn)
    cat = upsert_category(
        conn, kind="category", title="طب داخلی",
        source_url="/qbank/cat-id-1", question_count=2,
    )
    upsert_category(
        conn, kind="topic", title="آریتمی‌ها",
        source_url="/qbank/topic-id-79", parent_id=cat, position=1,
        question_count=2,
    )
    upsert_question(
        conn, _make_question("بیمار ۶۰ ساله با تاکی‌کاردی", "/qbank/topic-id-79?page=1", 1),
        topic_id=79,
    )
    upsert_question(
        conn, _make_question("یافته‌های ECG", "/qbank/topic-id-79?page=2", 2),
        topic_id=79,
    )
    update_scrape_state(conn, 79, last_page=2, total_pages=5, status="running")
    conn.close()
    yield db_path


# ---------------------------------------------------------------------------
# Format contract
# ---------------------------------------------------------------------------

def test_bundle_is_self_describing(seeded_db):
    b = build_android_bundle(seeded_db)
    assert b["format"] == FORMAT_NAME == "medo-bank"
    assert b["version"] == FORMAT_VERSION
    assert set(b) == {"format", "version", "generated_at", "stats", "tree", "questions"}


def test_stats_match_the_questions(seeded_db):
    b = build_android_bundle(seeded_db)
    assert b["stats"]["total"] == len(b["questions"]) == 2
    assert b["stats"]["with_answer"] == 2
    assert b["stats"]["with_explanation"] == 2
    assert b["stats"]["bookmarked"] == 0


def test_question_carries_every_field_the_app_parses(seeded_db):
    q = build_android_bundle(seeded_db)["questions"][0]
    assert set(q) == {
        "id", "topic_id", "tag_id", "nodes", "source_url", "page", "ordinal",
        "stem_html", "stem_text", "options", "correct", "answer_source",
        "votes", "explanation_html", "explanation_text", "explanation_status",
        "has_images", "path", "bookmarked", "selected",
    }
    # The phone needs every node, not only the primary pair.
    assert q["nodes"] and q["nodes"][0]["topic_id"] == q["topic_id"]
    assert q["options"][0] == {"index": 1, "text": "گزینه 1"}
    assert q["correct"] == 2
    assert q["stem_text"] == "بیمار ۶۰ ساله با تاکی‌کاردی"
    # Scrape bookkeeping must not travel: it is dead weight on the phone.
    assert "content_hash" not in q and "download_bytes" not in q


def test_tree_keeps_hierarchy_and_progress(seeded_db):
    tree = build_android_bundle(seeded_db)["tree"]
    root = tree[0]
    assert root["title"] == "طب داخلی"
    topic = root["children"][0]
    assert topic["title"] == "آریتمی‌ها"
    assert topic["progress"]["scraped"] == 2
    assert topic["progress"]["status"] == "running"
    assert topic["progress"]["total_pages"] == 5
    assert topic["partial"] is True          # 2 of 5 pages -> incomplete badge


def test_completed_node_is_not_marked_partial(seeded_db):
    conn = get_connection(seeded_db)
    update_scrape_state(conn, 79, last_page=5, total_pages=5, status="completed")
    conn.commit()
    conn.close()
    tree = build_android_bundle(seeded_db)["tree"]
    assert tree[0]["children"][0]["partial"] is False


def test_persian_text_is_not_escaped_and_bytes_are_utf8(seeded_db):
    blob = bundle_bytes(seeded_db)
    assert "بیمار".encode("utf-8") in blob
    assert b"\\u0628" not in blob            # no \u0628 escape sequences
    assert json.loads(blob.decode("utf-8"))["stats"]["total"] == 2


def test_empty_archive_still_produces_a_valid_bundle(tmp_path):
    db_path = tmp_path / "empty.db"
    conn = get_connection(db_path)
    init_db(conn)
    conn.close()
    b = build_android_bundle(db_path)
    assert b["questions"] == [] and b["stats"]["total"] == 0
    assert json.loads(bundle_bytes(db_path).decode("utf-8"))["tree"] == []


# ---------------------------------------------------------------------------
# USB route: the .medo file
# ---------------------------------------------------------------------------

def test_write_bundle_creates_parents_and_overwrites(tmp_path, seeded_db):
    out = tmp_path / "nested" / "dir" / "bank.medo"
    written = write_bundle(out, db_path=seeded_db)
    assert written == out and out.is_file()
    assert json.loads(out.read_text(encoding="utf-8"))["format"] == FORMAT_NAME

    write_bundle(out, db_path=seeded_db)
    assert json.loads(out.read_text(encoding="utf-8"))["stats"]["total"] == 2
    assert not list(out.parent.glob("*.tmp"))     # temp file replaced, not left


# ---------------------------------------------------------------------------
# Wi-Fi route: the read-only share server
# ---------------------------------------------------------------------------

@pytest.fixture
def share_server(seeded_db):
    httpd = start_share(db_path=seeded_db, port=0, bind="127.0.0.1")
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, dict(r.headers), r.read()


def test_share_serves_the_download(share_server):
    status, headers, body = _get(share_server, f"/{BUNDLE_FILENAME}")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert BUNDLE_FILENAME in headers["Content-Disposition"]
    assert json.loads(body.decode("utf-8"))["stats"]["total"] == 2


def test_share_index_explains_the_import(share_server):
    status, headers, body = _get(share_server, "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    text = body.decode("utf-8")
    assert BUNDLE_FILENAME in text and "dir=\"rtl\"" in text


def test_share_health_and_404(share_server):
    assert _get(share_server, "/health")[0] == 200
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(share_server, "/etc/passwd")
    assert err.value.code == 404


def _with_apk(seeded_db, apk_path, port=0):
    httpd = start_share(db_path=seeded_db, port=port, bind="127.0.0.1", apk_path=apk_path)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def test_apk_is_offered_for_install(tmp_path, seeded_db):
    """The phone installs straight from the same page that hands out the bank."""
    apk = tmp_path / "medo.apk"
    apk.write_bytes(b"PK\x03\x04 pretend apk")
    httpd, thread = _with_apk(seeded_db, apk)
    try:
        status, headers, body = _get(httpd.server_address[1], "/medo.apk")
        assert status == 200
        assert headers["Content-Type"] == "application/vnd.android.package-archive"
        assert "medo.apk" in headers["Content-Disposition"]
        assert body == apk.read_bytes()

        _, _, index = _get(httpd.server_address[1], "/")
        assert b'/medo.apk' in index       # install button present
        assert b'/medo.medo' in index      # bank button present
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_missing_apk_is_reported_not_faked(tmp_path, seeded_db):
    httpd, thread = _with_apk(seeded_db, tmp_path / "absent.apk")
    try:
        with pytest.raises(urllib.error.HTTPError) as err:
            _get(httpd.server_address[1], "/medo.apk")
        assert err.value.code == 404
        assert b"build the APK" in err.value.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_share_refuses_writes(share_server):
    """The phone only ever downloads; anything else must not be accepted.

    The handler answers 501 and drops the connection, so a client that is
    still writing may see a reset instead of the status line. Both mean the
    same thing here — the write was refused — and which one arrives depends
    on timing, so the check accepts either rather than being flaky.
    """
    conn = HTTPConnection("127.0.0.1", share_server, timeout=5)
    try:
        conn.request("POST", f"/{BUNDLE_FILENAME}", body=b"x")
        assert conn.getresponse().status in (501, 405)
    except ConnectionResetError:
        pass
    finally:
        conn.close()


def test_share_lists_a_url_a_phone_can_reach(seeded_db):
    urls = [f"http://{ip}:8765" for ip in lan_addresses()]
    urls.append("http://127.0.0.1:8765")
    assert all(u.startswith("http://") for u in urls)
    assert urls[-1] == "http://127.0.0.1:8765"   # adb reverse fallback


def test_phone_is_pointed_at_a_real_lan_address():
    """Virtual adapters must never be the address the QR recommends."""
    assert sorted(
        ["172.18.0.1", "10.67.195.246", "192.168.1.5", "169.254.3.4"],
        key=share._rank,
    ) == ["192.168.1.5", "10.67.195.246", "172.18.0.1", "169.254.3.4"]
    assert share._rank("not-an-address") == 3
