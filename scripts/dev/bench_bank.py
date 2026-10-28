#!/usr/bin/env python
"""Measure the web UI and the Android bank at 20,000 synthetic questions.

    python scripts/dev/bench_bank.py --db build/stress/medo.db \
        --bundle build/stress/medo.medo

Three numbers, each from a real run:

* HTTP: every `/api/questions` page the browser fetches, plus full questions.
* web UI: time from navigation to a usable list, and one search, measured in
  Chrome (the project's own `channel="chrome"` launch, no downloaded browser).
* Android: load + search + peak heap on the desktop JVM with `-Xmx64m`/`128m`.
  There is no emulator or device here, so this is a **proxy**, not a phone
  measurement; ART and the JVM differ.  If a device is attached, run the same
  bundle on it and read `adb shell dumpsys meminfo ir.medo.app`.

The generator is `scripts/dev/make_big_bank.py`; `build/stress` is git-ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAGE_SIZE = 400
ANDROID_SOURCES = ("MiniJson.java", "Question.java", "Node.java", "Bank.java",
                   "Text.java", "Io.java")

HARNESS = r"""
import ir.medo.app.Bank;
import java.io.File;
import java.lang.management.ManagementFactory;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

public final class BenchMain {
    public static void main(String[] args) throws Exception {
        final AtomicLong peak = new AtomicLong();
        Thread sampler = new Thread(new Runnable() {
            @Override public void run() {
                while (true) {
                    long used = ManagementFactory.getMemoryMXBean()
                            .getHeapMemoryUsage().getUsed();
                    peak.accumulateAndGet(used, Math::max);
                    try { Thread.sleep(20); } catch (InterruptedException e) { return; }
                }
            }
        });
        sampler.setDaemon(true);
        sampler.start();

        long t0 = System.nanoTime();
        Bank bank = Bank.load(new File(args[0]));
        long loadMs = (System.nanoTime() - t0) / 1_000_000;

        String query = args.length > 1 ? args[1] : "واژه123";
        long t1 = System.nanoTime();
        Set<Integer> hits = bank.searchIds(query);
        long searchMs = (System.nanoTime() - t1) / 1_000_000;

        System.gc();
        Thread.sleep(300);
        System.gc();
        long used = ManagementFactory.getMemoryMXBean()
                .getHeapMemoryUsage().getUsed();
        System.out.println("BENCH load_ms=" + loadMs
                + " questions=" + bank.questions.size()
                + " search_ms=" + searchMs
                + " matches=" + hits.size()
                + " peak_mb=" + (peak.get() / 1048576)
                + " used_mb=" + (used / 1048576)
                + " max_mb=" + (Runtime.getRuntime().maxMemory() / 1048576));
    }
}
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_server(url: str, seconds: float = 30.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/stats", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def api_bench(base: str, total: int) -> dict:
    times: list[float] = []
    payload = 0
    offset = 0
    fetched = 0
    started = time.perf_counter()
    while offset < total + PAGE_SIZE:
        t0 = time.perf_counter()
        with urllib.request.urlopen(
                f"{base}/api/questions?offset={offset}&limit={PAGE_SIZE}",
                timeout=60) as resp:
            body = resp.read()
        times.append((time.perf_counter() - t0) * 1000)
        payload += len(body)
        rows = len(json.loads(body))
        fetched += rows
        offset += PAGE_SIZE
        if rows < PAGE_SIZE:
            break
    list_ms = (time.perf_counter() - started) * 1000

    full: list[float] = []
    for qid in range(1, min(total, 20) + 1):
        t0 = time.perf_counter()
        with urllib.request.urlopen(f"{base}/api/question/{qid}", timeout=30) as resp:
            resp.read()
        full.append((time.perf_counter() - t0) * 1000)
    return {
        "pages": len(times), "rows": fetched,
        "list_ms": list_ms, "max_page_ms": max(times or [0]),
        "mb": payload / 1048576,
        "full_avg_ms": sum(full) / len(full) if full else 0,
        "full_max_ms": max(full or [0]),
    }


def web_bench(base: str, query: str) -> dict | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(f"[bench] web UI skipped: playwright unavailable ({exc})")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            try:
                t0 = time.perf_counter()
                page.goto(base, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_function(
                    "document.getElementById('countAll') && "
                    "document.getElementById('countAll').textContent.trim() !== '0'",
                    timeout=180000)
                ready_ms = (time.perf_counter() - t0) * 1000
                heap_mb = page.evaluate(
                    "performance.memory ? performance.memory.usedJSHeapSize / 1048576 : 0")

                before = page.inner_text("#indexBadge")
                t0 = time.perf_counter()
                page.fill("#searchInput", query)
                page.wait_for_function(
                    "b => document.getElementById('indexBadge').textContent.trim() !== b",
                    arg=before, timeout=30000)
                search_ms = (time.perf_counter() - t0) * 1000
                badge = page.inner_text("#indexBadge")
                heap_after = page.evaluate(
                    "performance.memory ? performance.memory.usedJSHeapSize / 1048576 : 0")
                return {"ready_ms": ready_ms, "search_ms": search_ms,
                        "heap_mb": heap_mb, "heap_after_mb": heap_after,
                        "badge": badge}
            finally:
                browser.close()
    except Exception as exc:
        print(f"[bench] web UI skipped: {str(exc).splitlines()[0][:160]}")
        return None


def android_bench(bundle: Path, work: Path) -> list[dict]:
    jdk = ROOT / "tools" / "jdk8"
    javac = jdk / "bin" / "javac.exe"
    java = jdk / "bin" / "java.exe"
    if not javac.is_file() or not java.is_file():
        print("[bench] android skipped: tools/jdk8 is not present")
        return []
    out = work / "jvm"
    out.mkdir(parents=True, exist_ok=True)
    harness = work / "BenchMain.java"
    harness.write_text(HARNESS, encoding="utf-8")
    sources = [str(ROOT / "android" / "src" / "ir" / "medo" / "app" / name)
               for name in ANDROID_SOURCES]
    compiled = subprocess.run(
        [str(javac), "-encoding", "UTF-8", "-nowarn", "-d", str(out),
         *sources, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if compiled.returncode != 0:
        print("[bench] android compile failed:", compiled.stderr[:300])
        return []

    results = []
    for heap in ("64m", "128m", "256m"):
        run = subprocess.run(
            [str(java), f"-Xmx{heap}", "-cp", str(out), "BenchMain",
             str(bundle), "واژه123"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
        line = next((l for l in run.stdout.splitlines() if l.startswith("BENCH ")),
                    "")
        if run.returncode == 0 and line:
            values = dict(part.split("=", 1) for part in line[6:].split())
            values["heap"] = heap
            results.append(values)
        else:
            print(f"[bench] android -Xmx{heap}: exit {run.returncode} "
                  f"{(run.stderr or run.stdout).splitlines()[-1][:120] if (run.stderr or run.stdout) else ''}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "build/stress/medo.db")
    parser.add_argument("--bundle", type=Path,
                        default=ROOT / "build/stress/medo.medo")
    parser.add_argument("--query", default="واژه123")
    args = parser.parse_args()
    if not args.db.is_file():
        print(f"[!] no stress bank at {args.db}; run make_big_bank.py first")
        return 2

    from medo.db import get_connection

    conn = get_connection(args.db)
    total = int(conn.execute("SELECT count(*) FROM questions").fetchone()[0])
    conn.close()
    db_mb = args.db.stat().st_size / 1048576
    bundle_mb = args.bundle.stat().st_size / 1048576 if args.bundle.is_file() else 0
    print(f"[bench] {total} questions, db {db_mb:.1f} MB, "
          f"bundle {bundle_mb:.1f} MB")

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "medo.cli", "serve", "--db", str(args.db),
         "--port", str(port)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": str(ROOT)})
    try:
        if not wait_server(base):
            print("[!] server did not start")
            return 2
        api = api_bench(base, total)
        print(f"[bench] HTTP list: {api['pages']} page(s), {api['rows']} rows, "
              f"{api['mb']:.1f} MB, {api['list_ms']:.0f} ms total, "
              f"max page {api['max_page_ms']:.0f} ms")
        print(f"[bench] HTTP full question: avg {api['full_avg_ms']:.0f} ms, "
              f"max {api['full_max_ms']:.0f} ms")

        web = web_bench(base, args.query)
        if web:
            print(f"[bench] web UI: ready {web['ready_ms'] / 1000:.1f} s, "
                  f"heap {web['heap_mb']:.0f} MB; search «{args.query}» "
                  f"{web['search_ms']:.0f} ms → {web['badge']} "
                  f"(heap {web['heap_after_mb']:.0f} MB)")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    if args.bundle.is_file():
        for values in android_bench(args.bundle, ROOT / "build/stress"):
            print(f"[bench] Android JVM (-Xmx{values['heap']}): load "
                  f"{int(values['load_ms']) / 1000:.1f} s, "
                  f"{values['questions']} questions, search "
                  f"{values['search_ms']} ms ({values['matches']} hits), "
                  f"peak {values['peak_mb']} MB, settled {values['used_mb']} MB")
        print("[bench] no emulator/device attached: JVM numbers are a proxy; "
              "on a phone use `adb shell dumpsys meminfo ir.medo.app`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
