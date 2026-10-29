"""tests/test_scraper_helpers.py

Unit tests for the pure page-classification helpers in medo.scraper.

The live site serves a login/redirect shell once it decides a client looks
automated, and it writes some notices in Arabic presentation forms
("در ﺣﺎل اﻧﺘﻘﺎل..."), which a naive substring check misses.
"""
from medo.scraper import classify_page, is_guest_quota, topic_url


class TestClassifyPage:
    def test_question_wins(self):
        assert classify_page("هرچیزی", has_question=True) == "question"

    def test_limit_notice(self):
        assert classify_page("برای ادامه باید خرید اشتراک کنید", False) == "limit"

    def test_auth_notice_plain(self):
        assert classify_page("ورود به حساب کاربری", False) == "auth"

    def test_auth_notice_sms_login(self):
        body = "ورود\n\n یک کد تایید به شما پیامک خواهد شد\n\n قوانین و سیاست حریم خصوصی را می‌پذیرم"
        assert classify_page(body, False) == "auth"

    def test_auth_notice_shaped_redirect_shell(self):
        """Presentation-form text must still be recognised."""
        shaped = "در ﺣﺎل اﻧﺘﻘﺎل ﺑﻪ ﺳﺎﯾﺖ ﻣﻮرد ﻧﻈﺮ ﻫﺴﺘﯿﺪ..."
        assert classify_page(shaped, False) == "auth"

    def test_auth_notice_logged_out_widget(self):
        """A topic page that never decrypted shows the logged-out stats
        widget ("لاگین شوید").  That must classify as auth, not empty —
        empty used to end the whole run as 'completed, 0 questions'."""
        body = (
            "سوال 1 از 99\n"
            "برای فعال شدن محاسبه هوشمند آیتم های بالا برای شما به صورت "
            "رایگان، نیاز است با زدن دکمه زیر، لاگین شوید:\n"
            "لاگین سریع و بازگشت به همین سوال"
        )
        assert classify_page(body, False) == "auth"

    def test_waf_rejected_browser_is_blocked(self):
        """HTTP 403 «مرورگر شما برای این صفحه پشتیبانی نمی شود» is a WAF
        rejection, not an empty topic: it used to finish as 'completed, 0
        saved'."""
        body = ("مرورگر شما برای این صفحه پشتیبانی نمی شود لطفا از مرورگر کروم "
                "یا سافاری استفاده کنید آیدی پشتیبانی: MedofastSupport")
        assert classify_page(body, False) == "blocked"

    def test_guest_quota_notice_is_limit_not_auth(self):
        """The guest allowance also redirects to /login?ddos_redirect=true,
        so the URL lies; the text says it is a daily quota."""
        body = ("درخواست‌های مجاز امروز شما به عنوان کاربر میهمان به پایان رسیده. "
                "برای ادامه استفاده از امکانات رایگان مدوفست لطفا وارد حساب "
                "کاربری خود شوید")
        assert is_guest_quota(body) is True
        assert classify_page(body, False) == "limit"

    def test_unknown_is_empty(self):
        assert classify_page("هیچ چیز خاصی اینجا نیست", False) == "empty"


class TestTopicUrl:
    def test_first_page_has_no_query(self):
        assert topic_url(79) == "https://medofast.ir/qbank/topic-id-79"

    def test_page_query(self):
        assert topic_url(79, page=3).endswith("topic-id-79?page=3")


class TestFinalizeState:
    """The stored cursor, not `current_page - 1`, is what a run ends with."""

    @staticmethod
    def _harvester(tmp_path):
        from medo.scraper import Harvester
        return Harvester(db_path=tmp_path / "medo.db")

    @staticmethod
    def _conn(tmp_path):
        from medo.db import get_connection, init_db
        conn = get_connection(tmp_path / "medo.db")
        init_db(conn)
        return conn

    def test_completed_keeps_the_last_stored_page(self, tmp_path):
        from medo.db import get_scrape_state

        conn = self._conn(tmp_path)
        # A resumed run that started at page 5 and finished there.
        from medo.db import update_scrape_state
        update_scrape_state(conn, topic_id=83, last_page=5, status="running")

        self._harvester(tmp_path)._finalize_state(conn, 83, "completed")
        state = get_scrape_state(conn, 83)
        conn.close()

        assert state["last_page"] == 5   # was 4 before the fix
        assert state["status"] == "completed"

    def test_stop_statuses_are_recorded_with_the_saved_cursor(self, tmp_path):
        from medo.db import get_scrape_state, update_scrape_state

        conn = self._conn(tmp_path)
        update_scrape_state(conn, topic_id=83, last_page=7, status="running")
        self._harvester(tmp_path)._finalize_state(conn, 83, "limit",
                                                  "guest quota exhausted")
        state = get_scrape_state(conn, 83)
        conn.close()

        assert state["last_page"] == 7
        assert state["status"] == "limit"
        assert state["error_message"] == "guest quota exhausted"


class TestLiveStats:
    """The UI/CLI numbers must come from counted documents, not guesses."""

    def test_request_rate_delay_429_and_hourly_rate(self, tmp_path):
        import time

        from medo.scraper import Harvester

        h = Harvester(db_path=tmp_path / "medo.db", delay_range=(2.0, 3.0))
        now = time.monotonic()
        h._request_times.extend([now - 70, now - 20, now - 5, now])
        h.requests_total = 4
        h.questions_total = 30
        h.run_started = now - 1800          # 30 minutes
        h.expected_total = 60
        stats = h.stats()
        assert stats["requests_per_minute"] == 3   # the 70 s sample expired
        assert stats["delay_low"] == 2.0 and stats["delay_high"] == 3.0
        assert stats["questions_per_hour"] == 60
        assert stats["eta_seconds"] == 1800

    def test_a_learned_range_is_used_on_the_next_run(self, tmp_path):
        from medo.db import get_connection, init_db, save_pace_state
        from medo.scraper import Harvester

        db = tmp_path / "medo.db"
        conn = get_connection(db)
        init_db(conn)
        conn.close()
        save_pace_state(db, 2.6, 4.0, ceiling=2.6)
        h = Harvester(db_path=db)
        assert h.pace.range == (2.6, 4.0)


class TestMeasuredBytes:
    """Per-question traffic must be a typical question, not the app shell."""

    @staticmethod
    def _harvester(tmp_path):
        from medo.scraper import Harvester
        return Harvester(db_path=tmp_path / "medo.db")

    def test_average_amortises_the_first_page_startup(self, tmp_path):
        h = self._harvester(tmp_path)
        h._question_bytes = [425_000, 26_000, 25_000]
        h.bytes_total = sum(h._question_bytes)
        assert round(h.measured_avg_bytes) == round((425_000 + 26_000 + 25_000) / 3)

    def test_fewer_than_three_samples_is_unknown(self, tmp_path):
        h = self._harvester(tmp_path)
        h._question_bytes = [425_000, 26_000]
        assert h.measured_samples == 2
        assert h.measured_avg_bytes == 0


class TestTrafficMeasurement:
    """The live counter must see HTTP/2 responses without Content-Length."""

    @staticmethod
    def _harvester(tmp_path):
        from medo.scraper import Harvester
        return Harvester(db_path=tmp_path / "medo.db", delay_range=(0, 0))

    def test_sizes_counts_body_and_headers(self, tmp_path):
        from types import SimpleNamespace
        h = self._harvester(tmp_path)

        class Req:
            resource_type = "fetch"

            def sizes(self):
                return {"responseBodySize": 40_000, "responseHeadersSize": 700}

        resp = SimpleNamespace(headers={}, request=Req(), body=lambda: b"")
        assert h._response_bytes(resp) == 40_700

    def test_content_length_is_the_fallback(self, tmp_path):
        from types import SimpleNamespace
        h = self._harvester(tmp_path)

        class Req:
            resource_type = "document"

            def sizes(self):
                raise RuntimeError("not finished")

        def no_body():
            raise RuntimeError("body unavailable")

        resp = SimpleNamespace(headers={"content-length": "12345"},
                               request=Req(), body=no_body)
        assert h._response_bytes(resp) == 12_345

    def test_polite_delay_never_sleeps_a_negative_time(self, tmp_path):
        """The while-check and the sleep used to race at the end of a delay:
        `end - now` went negative and killed the whole run."""
        h = self._harvester(tmp_path)
        h._polite_delay()          # must not raise
        h._polite_delay()

    def test_cached_page_payload_is_counted_from_the_body(self, tmp_path):
        """A Cache-API hit has a negative network body but a real payload."""
        from types import SimpleNamespace
        h = self._harvester(tmp_path)

        class Req:
            resource_type = "fetch"

            def sizes(self):
                return {"responseBodySize": -1394, "responseHeadersSize": 1394}

        payload = b"x" * 22_562
        resp = SimpleNamespace(headers={}, request=Req(), body=lambda: payload)
        assert h._response_bytes(resp) == 22_562
