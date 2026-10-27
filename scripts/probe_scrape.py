"""Live end-to-end scrape test: 3 pages of topic 79 from page 1."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from medo.scraper import Harvester  # noqa: E402


def main() -> int:
    harvester = Harvester(headless=True, fast=True)
    res = harvester.scrape_topic(79, start_page=1, max_pages=3)
    print(f"RESULT: status={res['status']} detail={res['detail']!r} "
          f"scraped={res['scraped_count']} last_page={res['last_page']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
