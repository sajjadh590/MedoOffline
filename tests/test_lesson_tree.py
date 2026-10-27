"""Tests for the real medofast tree markup (category → lesson → topic).

The live site looks like this (captured 2026-10-19):

* a category page renders one card per lesson — ``<a href="/lobey/3">`` with
  ``<h2>ریه</h2>`` and a ``data-topic-id="3"`` div;
* a lesson page has ``onclick="window.location.href='/qbank/topic-id-3'"`` and
  a table of micro-topics pointing at ``/qbank/topic-id-3?tag=213``.

Discovery used to read only ``<a href>`` for cat-id/topic-id URLs, which is
why only the top categories ever arrived.
"""
from __future__ import annotations

from medo import netfix
from medo.scraper import (
    extract_lesson_cards,
    extract_lesson_page,
    extract_menu_links,
)

CATEGORY_PAGE = """
<div id="mabhasi">
  <div class="col">
    <a href="/lobey/3" class="single-service-inner text-center pb-0">
      <div class="thumb"><img src="x.svg" alt="بانک جامع سوالات ریه"></div>
      <div class="details mb-3">
        <h2>ریه</h2>
        <div class="topic-progress-container" data-topic-id="3"></div>
      </div>
    </a>
  </div>
  <div class="col">
    <a href="/lobey/1/" class="single-service-inner text-center pb-0">
      <div class="details mb-3"><h2>نفرولوژی</h2></div>
    </a>
  </div>
  <a href="/qbank/cat-id-2/by-year/" class="badge">به تفکیک دوره و سال</a>
  <a href="/qbank/cat-id-2/#title">پره انترنی</a>
</div>
"""

LESSON_PAGE = """
<button class="btn" onclick="window.location.href='/qbank/topic-id-3'">شروع مطالعه جامع</button>
<p>تعداد سوال در مجموعه تست مدوفست: 298</p>
<table id="activeExams">
  <tr class="align-middle">
    <th class="px-0"><h3 style="font-size:12px">آسم</h3></th>
    <td class="px-0"><span class="fw-bold">54</span></td>
    <td><button onclick="window.location.href='/qbank/topic-id-3?tag=213'">شروع</button></td>
  </tr>
  <tr class="align-middle">
    <th class="px-0"><h3 style="font-size:12px">آمبولی ریه و DVT</h3></th>
    <td class="px-0"><span class="fw-bold">46</span></td>
    <td><button onclick="window.location.href='/qbank/topic-id-3?tag=214'">شروع</button></td>
  </tr>
  <tr class="align-middle">
    <th class="px-0"><h3 style="font-size:12px">آسم (تکرار)</h3></th>
    <td class="px-0"><span class="fw-bold">1</span></td>
    <td><button onclick="window.location.href='/qbank/topic-id-3?tag=213'">شروع</button></td>
  </tr>
</table>
"""


def test_lesson_cards_read_lobey_links_titles_and_topic_ids():
    cards = extract_lesson_cards(CATEGORY_PAGE)
    assert [c["lesson_id"] for c in cards] == [3, 1]
    assert cards[0] == {
        "lesson_id": 3,
        "href": "/lobey/3",
        "title": "ریه",
        "topic_id": 3,
    }
    assert cards[1]["title"] == "نفرولوژی"
    assert cards[1]["topic_id"] is None
    assert cards[1]["href"] == "/lobey/1"


def test_menu_links_skip_dead_by_year_and_strip_fragments():
    links = extract_menu_links(CATEGORY_PAGE)
    cats = [l for l in links if l["kind"] == "cat"]
    assert [l["href"] for l in cats] == ["/qbank/cat-id-2"]
    assert cats[0]["title"] == "پره انترنی"


def test_lesson_page_yields_topic_bank_size_and_micro_topics():
    info = extract_lesson_page(LESSON_PAGE)
    assert info["topic_id"] == 3
    assert info["total"] == 298
    assert [(m["tag_id"], m["title"], m["count"]) for m in info["micro_topics"]] == [
        (213, "آسم", 54),
        (214, "آمبولی ریه و DVT", 46),
    ]


def test_lesson_page_without_topics_is_harmless():
    info = extract_lesson_page("<html><body>Bad Gateway</body></html>")
    assert info == {"topic_id": None, "total": 0, "micro_topics": []}


def test_dead_windows_proxy_is_reported(monkeypatch):
    """Chromium follows the system proxy: a dead one kills every page."""

    def refuse(*_args, **_kwargs):
        raise OSError("connection refused")

    import contextlib

    monkeypatch.setattr(netfix, "_wininet_proxy", lambda: ("1", "127.0.0.1:10808"))
    message = netfix.proxy_problem(connect=refuse)
    assert message and "127.0.0.1:10808" in message
    # A proxy that answers, and no proxy at all, are both fine.
    assert netfix.proxy_problem(
        connect=lambda *a, **k: contextlib.nullcontext()) is None
    monkeypatch.setattr(netfix, "_wininet_proxy", lambda: ("0", "127.0.0.1:10808"))
    assert netfix.proxy_problem(connect=refuse) is None
    monkeypatch.setattr(netfix, "_wininet_proxy", lambda: ("1", ""))
    assert netfix.proxy_problem(connect=refuse) is None


def test_fake_ip_detection_and_public_resolver_short_circuit(monkeypatch):
    # Pinned addresses are cached for a few minutes; start from a clean slate
    # so an earlier browser launch in this session cannot leak in.
    netfix.clear_cache()
    assert netfix.is_fake_ip("198.18.0.94")
    assert not netfix.is_fake_ip("185.143.233.132")
    assert not netfix.is_fake_ip("not-an-ip")
    # A normal OS answer means the browser must be left untouched.
    monkeypatch.setattr(netfix, "system_ips", lambda host: ["185.143.233.132"])
    assert netfix.resolve_public_ip() is None
    assert netfix.host_resolver_args() == []
    # A fake answer makes us ask DoH and pin the real address — but only when
    # that address really answers while the fake one does not.
    monkeypatch.setattr(netfix, "system_ips", lambda host: ["198.18.0.94"])
    monkeypatch.setattr(netfix, "doh_ip", lambda host: "185.143.234.132")
    monkeypatch.setattr(netfix, "reachable",
                        lambda target, *a, **k: target == "185.143.234.132")
    assert netfix.host_resolver_args() == [
        "--host-resolver-rules=MAP medofast.ir 185.143.234.132"
    ]
    # A VPN that carries the fake address itself needs no pinning.
    netfix.clear_cache()
    monkeypatch.setattr(netfix, "reachable", lambda target, *a, **k: True)
    assert netfix.host_resolver_args() == []
    # Nor is an address that cannot be reached at all.
    netfix.clear_cache()
    monkeypatch.setattr(netfix, "reachable", lambda target, *a, **k: False)
    assert netfix.host_resolver_args() == []
