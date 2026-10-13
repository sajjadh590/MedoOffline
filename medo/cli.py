"""
medo/cli.py
Command-line interface for MedoOffline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import sys
import typer
from rich.console import Console
from rich.table import Table

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
        Path("data/storage_state.json"),
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
    delay_min: float = typer.Option(1.5, "--delay-min", help="Minimum uniform random delay in seconds"),
    delay_max: float = typer.Option(3.0, "--delay-max", help="Maximum uniform random delay in seconds"),
    headless: bool = typer.Option(True, "--headless/--no-headless", help="Run browser in headless mode"),
    storage_state: Optional[Path] = typer.Option(None, "--state", help="storage_state.json path (default: data/storage_state.json or $MEDO_STORAGE_STATE)"),
    db_path: Path = typer.Option(Path("data/medo.db"), "--db", help="SQLite database path"),
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
    db_path: Path = typer.Option(Path("data/medo.db"), "--db", help="SQLite database path"),
):
    """
    Crawl /main-menu once and store the site's exact category tree
    (category → lesson → section → topic, original titles, order, counts).
    """
    from medo.scraper import discover_hierarchy

    console.print("[bold cyan][*] Discovering category hierarchy from /main-menu...[/bold cyan]")
    summary = discover_hierarchy(
        db_path=db_path, headless=headless, storage_state=storage_state)
    console.print(
        f"[bold green][+] Stored {summary['nodes']} node(s): "
        f"{summary['categories']} categories, {summary['topics']} topics."
        f"[/bold green]"
    )


@app.command()
def serve(
    port: int = typer.Option(8080, "--port", "-p", help="Port to listen on"),
    db_path: Path = typer.Option(Path("data/medo.db"), "--db", help="SQLite database path"),
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
        prog = get_category_progress(conn)
        conn.close()
    except Exception:
        return
    partial = [u for u, s in prog.items()
               if s["status"] not in {"completed"} and s["scraped"] > 0]
    never = [u for u, s in prog.items() if s["scraped"] == 0]
    if partial:
        console.print(f"[yellow][!] Partial build — {len(partial)} topic(s) still in progress, marked as partial.[/yellow]")
    if never:
        console.print(f"[yellow][i] {len(never)} topic(s) not scraped yet — skipped.[/yellow]")


@app.command(name="build-pdf", aliases=["export-pdf"])
def export_pdf_cmd(
    output: Path = typer.Option(Path("medo_test_book.pdf"), "--output", "-o", help="Output PDF file path"),
    topic: Optional[int] = typer.Option(None, "--topic", "-t", help="Filter by specific topic ID"),
    title: Optional[str] = typer.Option(None, "--title", help="Custom title for test book"),
    include_answers: bool = typer.Option(True, "--answers/--no-answers", help="Include answer key and explanations"),
    partial: bool = typer.Option(False, "--partial", help="Allow building from a half-finished database (incomplete topics marked)"),
    db_path: Path = typer.Option(Path("data/medo.db"), "--db", help="SQLite database path"),
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


@app.command(name="build-web", aliases=["export-web"])
def export_web_cmd(
    output_dir: Path = typer.Option(Path("dist"), "--output", "-o", help="Output directory"),
    topic: Optional[int] = typer.Option(None, "--topic", "-t", help="Filter by specific topic ID"),
    partial: bool = typer.Option(False, "--partial", help="Allow building from a half-finished database (incomplete topics marked)"),
    db_path: Path = typer.Option(Path("data/medo.db"), "--db", help="SQLite database path"),
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


@app.command()
def stats(
    db_path: Path = typer.Option(Path("data/medo.db"), "--db", help="SQLite database path"),
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
    db_path: Path = typer.Option(Path("data/medo.db"), "--db", help="SQLite database path"),
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


@app.command(name="import-fixtures")
def import_fixtures_cmd(
    db_path: Path = typer.Option(Path("data/medo.db"), "--db", help="SQLite database path"),
):
    """
    Import synthetic or captured MHTML fixtures into the database.
    """
    from medo.db import get_connection, init_db, upsert_question
    from medo.parser import parse_mhtml_file

    conn = get_connection(db_path)
    init_db(conn)

    fixture_dir = Path("tests/fixtures")
    imported = 0
    for mhtml_file in fixture_dir.glob("*.mhtml"):
        console.print(f"[*] Importing {mhtml_file.name}...")
        is_submitted = "after" in mhtml_file.name
        q = parse_mhtml_file(str(mhtml_file), submitted=is_submitted)
        upsert_question(conn, q, topic_id=79)
        imported += 1

    conn.close()
    console.print(f"[bold green][+] Imported {imported} fixtures into {db_path}[/bold green]")


if __name__ == "__main__":
    app()
