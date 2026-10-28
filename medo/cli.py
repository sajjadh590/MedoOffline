"""
medo/cli.py
Command-line interface for MedoOffline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import re
import sys
import typer
from rich.console import Console
from rich.table import Table

from medo.paths import (
    DEFAULT_DB_PATH,
    DEFAULT_STORAGE_STATE,
    DIST_DIR,
    PROJECT_ROOT,
)

# Reconfigure stdout/stderr to UTF-8 on Windows to safely print Persian characters and filenames
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

app = typer.Typer(
    name="medo",
    help="MedoOffline: Personal offline mirror of medofast.ir medical question bank.",
    add_completion=False,
)
console = Console(safe_box=True)


@app.command()
def login(
    storage_state: Path = typer.Option(
        DEFAULT_STORAGE_STATE,
        "--state",
        "-s",
        help="Path where session storage_state.json is saved",
    ),
):
    """
    Open browser for manual login and save authentication state.
    Never stores or handles your password.
    """
    from medo.auth import login_interactive
    login_interactive(storage_state_path=storage_state)


@app.command()
def scrape(
    topic: Optional[int] = typer.Option(None, "--topic", "-t", help="Topic ID to scrape (e.g. 79)"),
    all_topics: bool = typer.Option(False, "--all", help="Scrape every topic found in the category tree, in order"),
    start_page: Optional[int] = typer.Option(None, "--start", help="Start page (defaults to resume from last)"),
    max_pages: Optional[int] = typer.Option(None, "--max", help="Max pages to scrape in this run"),
    max_minutes: Optional[float] = typer.Option(None, "--max-minutes", help="Stop cleanly after N minutes (time-boxed batch)"),
    max_questions: Optional[int] = typer.Option(None, "--max-questions", help="Stop cleanly after N questions"),
    refill: bool = typer.Option(False, "--refill-explanations", help="Revisit only questions whose explanation_status != full"),
    whole_topic: bool = typer.Option(False, "--whole-topic", help="Download the unfiltered topic instead of its subtopics one by one"),
    delay_min: float = typer.Option(1.5, "--delay-min", help="Minimum uniform random delay in seconds"),
    delay_max: float = typer.Option(3.0, "--delay-max", help="Maximum uniform random delay in seconds"),
    headless: bool = typer.Option(True, "--headless/--no-headless", help="Run browser in headless mode"),
    storage_state: Optional[Path] = typer.Option(None, "--state", help="storage_state.json path (default: data/storage_state.json or $MEDO_STORAGE_STATE)"),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Scrape questions politely: 1 request at a time, random delay 1.5-3 s,
    exponential backoff, resumable from the exact page (--resume is default).
    """
    from medo.db import get_connection, list_topic_ids
    from medo.scraper import Harvester

    harvester = Harvester(
        db_path=db_path,
        storage_state=storage_state,
        delay_range=(delay_min, delay_max),
        headless=headless,
        max_minutes=max_minutes,
        max_questions=max_questions,
    )

    if refill:
        console.print("[bold cyan][*] Refilling explanations (only non-full)...[/bold cyan]")
        result = harvester.refill_explanations([topic] if topic else None)
    elif topic is not None:
        from medo.db import subtopic_targets

        conn = get_connection(db_path)
        subtopics = [] if whole_topic else subtopic_targets(conn, topic)
        conn.close()
        if subtopics:
            console.print(f"[bold cyan][*] Downloading {len(subtopics)} subtopic(s) "
                          f"of topic {topic} one by one (--whole-topic skips "
                          "this)...[/bold cyan]")
            result = {"scraped_count": 0, "status": "completed",
                      "detail": "", "last_page": 0}
            for sub in subtopics:
                part = harvester.scrape_topic(
                    topic_id=topic, start_page=start_page, max_pages=max_pages,
                    tag_id=sub["tag_id"], subtopic_title=sub["subtopic_title"])
                result["scraped_count"] += part["scraped_count"]
                result["last_page"] = part["last_page"]
                result["status"] = part["status"]
                result["detail"] = part.get("detail", "")
                if part["status"] in {"auth_required", "limit", "blocked", "error",
                                     "interrupted", "max_minutes", "max_questions",
                                     "throttled"}:
                    break
        else:
            console.print(f"[bold cyan][*] Starting harvester for topic {topic}...[/bold cyan]")
            result = harvester.scrape_topic(topic_id=topic, start_page=start_page, max_pages=max_pages)
    elif all_topics:
        conn = get_connection(db_path)
        topics = list_topic_ids(conn, include_done=False)
        conn.close()
        if not topics:
            console.print("[yellow][!] No topics in the category tree yet — run `medo discover` first.[/yellow]")
            raise typer.Exit(code=1)
        console.print(f"[bold cyan][*] Scraping {len(topics)} topic(s) in order...[/bold cyan]")
        result = harvester.scrape_many(topics, max_pages_per_topic=max_pages)
    else:
        console.print("[red][!] Pass --topic N, --all, or --refill-explanations.[/red]")
        raise typer.Exit(code=1)

    status = result.get("status", "done")
    color = "bold green" if status in {"completed", "done"} else "bold yellow"
    console.print(
        f"[{color}][+] Stopped ({status}): {result.get('scraped_count', 0)} question(s) "
        f"this run, last page {result.get('last_page', '?')}. "
        f"State saved — rerun the same command to resume.[/{color}]"
    )
    if result.get("detail"):
        console.print(f"    {result['detail']}")


@app.command()
def discover(
    headless: bool = typer.Option(True, "--headless/--no-headless"),
    storage_state: Optional[Path] = typer.Option(None, "--state", help="storage_state.json path"),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Crawl /main-menu and its qbank entry pages (/qbank-keshvari,
    /qbank-talifi, ...) once and store the site's exact category tree
    (category → lesson → section → topic, original titles, order, counts).
    """
    from medo.scraper import discover_hierarchy

    console.print("[bold cyan][*] Discovering category hierarchy from /main-menu "
                  "+ qbank entry pages...[/bold cyan]")
    summary = discover_hierarchy(
        db_path=db_path, headless=headless, storage_state=storage_state)
    console.print(
        f"[bold green][+] Stored {summary['nodes']} node(s): "
        f"{summary['categories']} categories, {summary['topics']} topics, "
        f"{summary.get('micro_topics', 0)} micro-topics."
        f"[/bold green]"
    )


@app.command()
def serve(
    port: int = typer.Option(8080, "--port", "-p", help="Port to listen on"),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Start local offline web server for interactive question practice.
    """
    from medo.server import serve as start_server
    start_server(port=port, db_path=db_path)


def _print_partial_note(db_path: Path) -> None:
    """Warn which topics are still incomplete in a partial build."""
    from medo.db import get_category_progress, get_connection

    try:
        conn = get_connection(db_path)
        # Micro-topics (?tag=) share their parent's progress row; counting
        # them here would report ~2,400 "topics" for a 150-topic tree.
        prog = {url: state for url, state in get_category_progress(conn).items()
                if "?tag=" not in url}
        conn.close()
    except Exception:
        return
    partial = [u for u, s in prog.items()
               if "topic-id-" in u and s["status"] not in {"completed"} and s["scraped"] > 0]
    never = [u for u, s in prog.items() if "topic-id-" in u and s["scraped"] == 0]
    if partial:
        console.print(f"[yellow][!] Partial build — {len(partial)} topic(s) still in progress, marked as partial.[/yellow]")
    if never:
        console.print(f"[yellow][i] {len(never)} topic(s) not scraped yet — skipped.[/yellow]")


@app.command(name="build-pdf")
def export_pdf_cmd(
    output: Path = typer.Option(Path("medo_test_book.pdf"), "--output", "-o", help="Output PDF file path"),
    topic: Optional[int] = typer.Option(None, "--topic", "-t", help="Filter by specific topic ID"),
    title: Optional[str] = typer.Option(None, "--title", help="Custom title for test book"),
    include_answers: bool = typer.Option(True, "--answers/--no-answers", help="Include answer key and explanations"),
    partial: bool = typer.Option(False, "--partial", help="Allow building from a half-finished database (incomplete topics marked)"),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Generate a printable PDF test book with Persian RTL formatting.
    """
    from medo.pdf import export_pdf

    console.print(f"[cyan][*] Generating PDF test book: {output}...[/cyan]")
    try:
        out_file = export_pdf(
            output_path=output,
            db_path=db_path,
            topic_id=topic,
            title=title,
            include_answers=include_answers,
            partial=partial,
        )
        console.print(f"[bold green][+] PDF successfully generated: {out_file}[/bold green]")
        if partial:
            _print_partial_note(db_path)
    except Exception as ex:
        console.print(f"[bold red][!] Error generating PDF: {ex}[/bold red]")
        raise typer.Exit(code=1)


# Old command names kept as aliases (typer has no `aliases=` kwarg; register twice)
app.command(name="export-pdf")(export_pdf_cmd)


@app.command(name="build-web")
def export_web_cmd(
    output_dir: Path = typer.Option(DIST_DIR, "--output", "-o", help="Output directory"),
    topic: Optional[int] = typer.Option(None, "--topic", "-t", help="Filter by specific topic ID"),
    partial: bool = typer.Option(False, "--partial", help="Allow building from a half-finished database (incomplete topics marked)"),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Export self-contained static web bundle to run without Python or server.
    """
    from medo.exporter import export_web_bundle

    console.print(f"[cyan][*] Exporting web viewer to {output_dir}...[/cyan]")
    out_dir = export_web_bundle(output_dir=output_dir, db_path=db_path,
                                topic_id=topic, partial=partial)
    console.print(f"[bold green][+] Web bundle exported to {out_dir}. Double-click index.html to view.[/bold green]")
    if partial:
        _print_partial_note(db_path)


app.command(name="export-web")(export_web_cmd)


@app.command(name="build-bank")
def export_android_cmd(
    output: Path = typer.Option(DIST_DIR / "medo.medo", "--output", "-o", help="Output .medo file"),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
    category: Optional[list[int]] = typer.Option(
        None, "--category", "-c",
        help="Only these categories/topics (repeatable; default = whole bank)"),
    images: bool = typer.Option(
        False, "--images/--no-images",
        help="Embed small WebP mind-maps in the lessons (much larger file)"),
):
    """
    Package the archive for the Android app (USB copy or system file picker).
    """
    from medo.share import write_bundle

    out = write_bundle(output, db_path=db_path, categories=category,
                       include_images=images)
    size = out.stat().st_size / 1048576
    console.print(f"[bold green][+] Android bank written: {out} ({size:.1f} MB)[/bold green]")
    console.print("[dim]Copy it to the phone, then: home screen -> دریافت بانک -> انتخاب فایل[/dim]")
    if not images:
        console.print("[dim]بدون تصویر ساخته شد؛ برای درس‌نامه‌های تصویری: --images[/dim]")


app.command(name="export-android")(export_android_cmd)


@app.command()
def share(
    port: int = typer.Option(8765, "--port", "-p", help="Port to listen on"),
    bind: str = typer.Option("0.0.0.0", "--bind", help="Interface (0.0.0.0 = reachable on the LAN)"),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Serve the Android bank over Wi-Fi (or USB with adb reverse). Read-only.
    """
    from medo.share import share_urls, start_share

    httpd = start_share(db_path=db_path, port=port, bind=bind)
    console.print("[bold cyan][*] Android bank ready for import[/bold cyan]")
    console.print("    Open the app: دریافت بانک ← دریافت از وای‌فای, then enter one of:")
    for url in share_urls(port):
        console.print(f"      [bold]{url}[/bold]")
    console.print(f"[dim]USB instead: adb reverse tcp:{port} tcp:{port}  ->  http://127.0.0.1:{port}[/dim]")
    console.print("[dim]Ctrl+C to stop[/dim]")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("[yellow][-] share server stopped[/yellow]")
    finally:
        httpd.server_close()


@app.command()
def stats(
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Display summary statistics of scraped questions and database health.
    """
    from medo.db import get_connection, get_database_stats, init_db

    conn = get_connection(db_path)
    init_db(conn)
    s = get_database_stats(conn)
    conn.close()

    table = Table(title="MedoOffline Database Statistics", header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")

    table.add_row("Total Questions", str(s["total_questions"]))
    table.add_row("With Correct Answer", str(s["with_answer"]))
    table.add_row("With Full Explanation", str(s["with_explanation"]))
    table.add_row("With Media / Mindmaps", str(s["with_images"]))
    table.add_row("Media Assets Downloaded", str(s["total_assets"]))
    table.add_row("Distinct Topics", str(s["total_topics"]))
    table.add_row("Bookmarked by User", str(s["bookmarked"]))
    table.add_row("Attempted Questions", str(s["attempted"]))

    console.print(table)


@app.command()
def verify(
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Verify integrity of questions database (duplicates, missing answers, format checks).
    """
    from medo.db import get_all_questions, get_connection

    conn = get_connection(db_path)
    questions = get_all_questions(conn)
    conn.close()

    if not questions:
        console.print("[yellow][!] Database contains no questions yet.[/yellow]")
        return

    missing_answers = sum(1 for q in questions if q.get("correct_index") is None)
    missing_expl = sum(1 for q in questions if not q.get("explanation_text"))
    missing_options = sum(1 for q in questions if len(q.get("options", [])) < 4)

    console.print(f"[bold]Total questions analyzed:[/bold] {len(questions)}")
    console.print(f"- Questions missing correct answer: {missing_answers}")
    console.print(f"- Questions missing explanation: {missing_expl}")
    console.print(f"- Questions with < 4 options: {missing_options}")

    if missing_answers == 0 and missing_options == 0:
        console.print("[bold green][+] Database integrity check: ALL PASSED![/bold green]")
    else:
        console.print("[bold yellow][!] Some questions have warnings above.[/bold yellow]")


@app.command(name="diagnose-tags")
def diagnose_tags(
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Read-only: where questions are stored without their subtopic, and which
    subtopics claim to be complete while holding fewer questions than the tree
    declares (a truncated download).
    """
    from medo.db import (count_node_questions, declared_questions,
                         get_connection, init_db, tag_id_of)

    conn = get_connection(db_path)
    init_db(conn)

    # question_nodes, not questions.tag_id: a question read through two
    # subtopics has one row there and only one tag in the old column.
    per_topic = {
        int(r["topic_id"]): {
            "stored": int(r["stored"] or 0),
            "untagged": int(r["stored"] or 0) - int(r["tagged"] or 0),
        }
        for r in conn.execute(
            "SELECT topic_id, count(DISTINCT question_id) AS stored, "
            "  count(DISTINCT CASE WHEN tag_id > 0 THEN question_id END) AS tagged "
            "FROM question_nodes GROUP BY topic_id"
        ).fetchall()
    }

    # A subtopic is a «?tag=» slice; the tree row carries its declared size and
    # micro_state carries the cursor (status + last page).
    subtopics: dict[int, list[dict]] = {}
    for node in conn.execute(
        "SELECT source_url, title FROM categories WHERE source_url LIKE '%?tag=%'"
    ).fetchall():
        tag = tag_id_of(node["source_url"])
        match = re.search(r"topic-id-(\d+)", node["source_url"] or "")
        if tag is None or not match:
            continue
        tid = int(match.group(1))
        stored = count_node_questions(conn, tid, tag)
        declared = declared_questions(conn, tid, tag)
        state = conn.execute(
            "SELECT status FROM micro_state WHERE topic_id = ? AND tag_id = ?",
            (tid, tag),
        ).fetchone() if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='micro_state'"
        ).fetchone() else None
        subtopics.setdefault(tid, []).append({
            "title": node["title"], "tag": tag, "stored": stored,
            "declared": declared, "status": (state["status"] if state else None),
        })

    rows = []
    for tid in sorted(set(per_topic) | set(subtopics)):
        subs = subtopics.get(tid, [])
        row = per_topic.get(tid, {})
        if not row.get("stored") and not any(s.get("stored") for s in subs):
            continue  # never downloaded: nothing to diagnose
        rows.append({
            "topic": tid,
            "stored": row.get("stored", 0),
            "untagged": row.get("untagged", 0),
            "declared": declared_questions(conn, tid),
            "subtopic_sum": sum(int(s.get("declared") or 0) for s in subs),
            "truncated": [s for s in subs
                          if s.get("status") == "completed" and s.get("declared")
                          and int(s.get("stored") or 0) < int(s.get("declared") or 0)],
        })
    conn.close()

    if not rows and not per_topic and not subtopics:
        console.print("[yellow][!] No topics in the database yet.[/yellow]")
        return

    table = Table(title="Subtopics and questions", header_style="bold magenta")
    table.add_column("topic", style="cyan")
    table.add_column("stored", justify="right")
    table.add_column("no subtopic", justify="right", style="yellow")
    table.add_column("declared", justify="right")
    table.add_column("subtopic sum", justify="right")
    table.add_column("truncated subtopics", style="red")

    for row in rows:
        table.add_row(
            str(row["topic"]),
            str(row["stored"]),
            str(row["untagged"]),
            str(row["declared"]),
            str(row["subtopic_sum"]),
            ", ".join(f"{s['title']} ({s.get('stored', 0)}/{s.get('declared')})"
                      for s in row["truncated"]) or "—",
        )
    console.print(table)


@app.command(name="diagnose-live")
def diagnose_live(
    topic: int = typer.Option(..., "--topic", "-t", help="Topic ID to probe (e.g. 15)"),
    tag: Optional[int] = typer.Option(None, "--tag", help="Subtopic (?tag=) to probe"),
    headless: bool = typer.Option(True, "--headless/--no-headless"),
    storage_state: Optional[Path] = typer.Option(None, "--state", help="storage_state.json path"),
):
    """
    One polite live probe: what the site's own counter shows when ?page=1 is
    requested and whether the ?tag= view filters. Run with the VPN routed
    direct; findings go to logs/medo.log.
    """
    from playwright.sync_api import sync_playwright

    from medo.scraper import Harvester, read_question_position, topic_url

    harvester = Harvester(db_path=DEFAULT_DB_PATH, storage_state=storage_state,
                          headless=headless)
    with sync_playwright() as p:
        browser, page = harvester._new_page(p)
        try:
            for label, url in (
                ("with page=1", topic_url(topic, page=1, tag_id=tag)),
                ("without page", topic_url(topic, tag_id=tag)),
            ):
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                harvester._wait_for_question(page)
                cur, total = read_question_position(
                    page.locator("body").inner_text(timeout=8000))
                site_id = page.locator("#question-meta").get_attribute(
                    "data-pqrfhskdjf") if page.locator("#question-meta").count() else None
                console.print(f"  {label}: counter {cur} of {total} · site id {site_id}")
                harvester._polite_delay()
        finally:
            browser.close()

    console.print("[dim]Logged to logs/medo.log. A sample must be saved by hand "
                  "(data/diag is git-ignored).[/dim]")


@app.command(name="doctor")
def doctor_cmd(
    deep: bool = typer.Option(False, "--deep", help="Also open one question page and check the site's question id"),
    topic: int = typer.Option(1, "--topic", help="Topic used by --deep"),
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """One Persian verdict: can a download work right now, and if not why."""
    from medo import doctor

    result = doctor.run_checks(db_path, deep=deep, topic=topic)
    table = Table(title="بررسی آمادگی", header_style="bold magenta")
    table.add_column("بررسی", style="cyan")
    table.add_column("وضعیت", justify="center")
    table.add_column("توضیح")
    for check in result["checks"]:
        table.add_row(check["name"], "✓" if check["ok"] else "×", check["detail"])
    console.print(table)

    color = {"ready": "green", "auth": "yellow"}.get(result["verdict"], "red")
    console.print(f"[bold {color}]{result['message']}[/bold {color}]")
    if result["verdict"] == "network":
        console.print(f"[dim]{result['help']}[/dim]")
        raise typer.Exit(code=1)


@app.command(name="check-site-ids")
def check_site_ids(
    topic: int = typer.Option(..., "--topic", "-t", help="Topic ID to open (e.g. 15)"),
    tag: Optional[int] = typer.Option(None, "--tag", help="Subtopic (?tag=) to open"),
    headless: bool = typer.Option(True, "--headless/--no-headless"),
    storage_state: Optional[Path] = typer.Option(None, "--state", help="storage_state.json path"),
):
    """
    Fail loudly when the site stops exposing the question id.

    De-duplication, the repair queue and every re-location of a question rely
    on #question-meta's id; a silent NULL would quietly create duplicate rows,
    so this command exists to be run (and to be called by `medo doctor`).
    """
    from playwright.sync_api import sync_playwright

    from medo import applog
    from medo.parser import parse_question_html
    from medo.scraper import Harvester, topic_url

    harvester = Harvester(db_path=DEFAULT_DB_PATH, storage_state=storage_state,
                          headless=headless)
    url = topic_url(topic, page=1, tag_id=tag)
    site_id: Optional[int] = None
    with sync_playwright() as p:
        browser, page = harvester._new_page(p)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            harvester._wait_for_question(page)
            parsed = parse_question_html(page.content(), source_url=url,
                                         source_page=1, ordinal=1)
            site_id = parsed.site_question_id
        finally:
            browser.close()

    if not site_id:
        applog.log(f"[site-id] {url}: #question-meta carried no usable id")
        console.print("[bold red]× سایت شناسهٔ سوال را برنگرداند.[/bold red]")
        console.print("[red]#question-meta دیگر data-pqrfhskdjf ندارد. تا این "
                      "درست شود، هر دانلود ممکن است سوال تکراری بسازد.[/red]")
        raise typer.Exit(code=1)

    applog.log(f"[site-id] {url}: site id {site_id}")
    console.print(f"[green]✓[/green] شناسهٔ سوال در سایت: [bold]{site_id}[/bold]")


@app.command(name="import-fixtures")
def import_fixtures_cmd(
    db_path: Path = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
):
    """
    Import synthetic or captured MHTML fixtures into the database.
    """
    from medo.db import get_connection, init_db, upsert_question
    from medo.parser import parse_mhtml_file

    conn = get_connection(db_path)
    init_db(conn)

    fixture_dir = PROJECT_ROOT / "tests" / "fixtures"
    imported = 0
    for mhtml_file in fixture_dir.glob("*.mhtml"):
        console.print(f"[*] Importing {mhtml_file.name}...")
        is_submitted = "after" in mhtml_file.name
        q = parse_mhtml_file(str(mhtml_file), submitted=is_submitted)
        upsert_question(conn, q, topic_id=79)
        imported += 1

    conn.close()
    console.print(f"[bold green][+] Imported {imported} fixtures into {db_path}[/bold green]")


@app.command(name="keystore-backup")
def keystore_backup(
    keystore: Path = typer.Option(
        PROJECT_ROOT / "android" / "medo-debug.keystore",
        "--keystore", help="Android signing key to back up"),
    output_dir: Path = typer.Option(
        DIST_DIR / "keystore-backup", "--output",
        help="Folder that receives the zip (dist/ is git-ignored)"),
):
    """Zip the Android signing key with its SHA-256 so a lost PC cannot break updates."""
    import hashlib
    import time
    import zipfile

    if not keystore.is_file():
        console.print(f"[bold red][!] کلید امضا پیدا نشد: {keystore}[/bold red]")
        raise typer.Exit(code=1)

    blob = keystore.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    target = output_dir / f"medo-debug-keystore-{stamp}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("medo-debug.keystore", blob)
        zf.writestr("SHA-256.txt", digest + "\n")

    console.print(f"[bold green][+] پشتیبان ساخته شد: {target}[/bold green]")
    console.print(f"    SHA-256: {digest}")
    console.print("[yellow]این فایل را از این رایانه بیرون ببر (فلش/درایو شخصی). "
                  "بدون همین کلید، نسخهٔ بعدی برنامه روی گوشی به‌روزرسانی "
                  "نمی‌شود.[/yellow]")


if __name__ == "__main__":
    app()
