"""Orchestrator: search LinkedIn posts -> filter -> diff -> email -> save state.

One-time login (opens a Chrome window; log in by hand):
    python main.py --login

Run:
    python main.py

Dry run (no email, no state write), only the first company batch:
    python main.py --dry-run --limit-batches 1
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime
import traceback
from dataclasses import replace
from pathlib import Path

from config import COMPANY_QUERY_TEMPLATES, GENERIC_QUERIES, company_batches, company_keyword, validate_queries
from location import classify_location
from notify import MissingCredentialsError, send_alert_email, send_new_posts_email
from scraper import (
    NotAuthenticatedError,
    Post,
    check_auth,
    interactive_login,
    is_job_seeker,
    is_person_post,
    is_promoted,
    is_staffing,
    launch_browser,
    search_posts,
    works_at_or_hiring_for,
)

STATE_PATH = Path(__file__).parent / "state" / "seen_posts.json"
PROFILE_DIR = os.environ.get(
    "LINKEDIN_PROFILE_DIR", str(Path.home() / ".config" / "linkedin-posts-profile")
)
MIN_DELAY_MS = int(os.environ.get("LINKEDIN_MIN_DELAY_MS", "20000"))
MAX_DELAY_MS = int(os.environ.get("LINKEDIN_MAX_DELAY_MS", "45000"))
# "soft": drop posts that mention only non-US places; keep ones with no location.
# "strict": keep only posts that explicitly mention a US location.
# "off": no location filtering.
US_FILTER = os.environ.get("US_FILTER", "soft").strip().lower()


def load_seen_ids() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError:
        print(f"[state] WARNING: could not parse {STATE_PATH}, starting fresh")
        return set()
    if isinstance(raw, list):
        return set(raw)
    return set()


def save_seen_ids(ids: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(ids), indent=2) + "\n", encoding="utf-8")


def _pause(page) -> None:
    page.wait_for_timeout(random.randint(MIN_DELAY_MS, MAX_DELAY_MS))


def _region_allowed(region: str) -> bool:
    if US_FILTER == "off":
        return True
    if US_FILTER == "strict":
        return region == "us"
    return region in ("us", "unknown")


def collect_posts(
    page,
    batches: list[dict[str, tuple[str, ...]]],
    parts: list[str],
    skip_generic: bool,
    seen_ids: set[str],
) -> tuple[set[str], list[Post]]:
    """Return (ids of every post seen, new posts that passed the filters).

    Company searches run first so a post found by both gets its company label.
    """
    all_ids: set[str] = set()
    run_keys: set[str] = set()  # keys of posts already kept in this run
    found: dict[str, Post] = {}
    searches: list[tuple[str, str, dict[str, tuple[str, ...]]]] = []
    for i, batch in enumerate(batches, 1):
        for label in parts:
            template = COMPANY_QUERY_TEMPLATES[label]
            searches.append((template.format(company=company_keyword(batch)), f"batch{i}/{label}", batch))
    if not skip_generic:
        searches += [(q, "generic", {}) for q in GENERIC_QUERIES]

    for n, (query, tag, batch) in enumerate(searches, 1):
        if n > 1:
            _pause(page)

        def select(p: Post, batch=batch) -> Post | None:
            keys = p.dedup_keys
            if any(k in seen_ids or k in run_keys for k in keys):
                return None
            if not is_person_post(p) or is_promoted(p) or is_job_seeker(p) or is_staffing(p):
                return None
            region = classify_location(f"{p.headline} {p.card_text}")
            if not _region_allowed(region):
                return None
            company = ""
            if batch:
                company = next((name for name, aliases in batch.items() if works_at_or_hiring_for(p, aliases)), "")
                if not company:
                    return None
            return replace(p, company=company, region=region)

        try:
            ids, kept = search_posts(page, query, section="company" if batch else "generic", select=select)
        except NotAuthenticatedError:
            raise
        except Exception as exc:
            print(f"[{n}/{len(searches)} {tag}] ERROR: {exc}")
            continue

        all_ids.update(ids)
        for p in kept:
            found[p.post_id] = p
            run_keys.update(p.dedup_keys)
        print(f"[{n}/{len(searches)} {tag}] {len(ids):>3} posts, {len(kept):>3} new + matched")

    return all_ids, list(found.values())


def _alert(subject: str, body: str) -> None:
    try:
        send_alert_email(subject, body)
    except MissingCredentialsError as exc:
        print(f"[notify] SKIPPED alert: {exc}")
    except Exception as exc:
        print(f"[notify] ERROR sending alert: {exc}")


def login(profile_dir: str) -> int:
    with launch_browser(profile_dir, headless=False) as page:
        return 0 if interactive_login(page) else 1


def resolve_parts(part: str) -> list[str]:
    """"auto": data searches in morning runs, ai searches in afternoon runs."""
    if part == "all":
        return list(COMPANY_QUERY_TEMPLATES)
    if part == "auto":
        return ["data"] if datetime.now().hour < 12 else ["ai"]
    return [part]


def run(
    dry_run: bool,
    headed: bool,
    part: str,
    limit_batches: int | None,
    skip_generic: bool,
    skip_companies: bool,
) -> int:
    seen_ids = load_seen_ids()
    print(f"[state] Loaded {len(seen_ids)} previously-seen post ids")

    validate_queries()
    batches = [] if skip_companies else company_batches()[:limit_batches]
    parts = resolve_parts(part)
    print(f"[plan] company searches: {', '.join(parts)} x {len(batches)} batches"
          f"{'' if skip_generic else ' + generic'}")

    with launch_browser(PROFILE_DIR, headless=not headed) as page:
        try:
            check_auth(page)
            all_ids, new_posts = collect_posts(page, batches, parts, skip_generic, seen_ids)
        except NotAuthenticatedError as exc:
            print(f"[linkedin] ERROR: {exc}")
            if not dry_run:
                _alert(
                    "LinkedIn login needed",
                    f"{exc}\n\nRun `python main.py --login` and log in again.",
                )
            return 1

    print(f"[scrape] {len(all_ids)} unique posts seen, {len(new_posts)} new after company/US filters")
    for p in new_posts:
        tag = p.company or "generic"
        print(f"  + [{tag}|{p.region}] {p.author} | {p.headline[:60]}\n      {p.url}")

    if dry_run:
        print("[dry-run] Skipping email + state save")
        return 0

    if new_posts:
        try:
            send_new_posts_email(new_posts)
        except MissingCredentialsError as exc:
            # Don't save state, or these posts would never be emailed.
            print(f"[notify] ERROR: {exc} Not saving state.")
            return 1
        except Exception as exc:
            print(f"[notify] ERROR: {exc}")
            return 1

    # Record filtered-out posts too, so they aren't re-checked every run.
    updated = seen_ids | all_ids
    save_seen_ids(updated)
    print(f"[state] Saved {len(updated)} post ids to {STATE_PATH}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Find LinkedIn posts from people hiring for data/AI roles."
    )
    ap.add_argument("--login", action="store_true", help="Open Chrome to log in by hand, then exit.")
    ap.add_argument("--headed", action="store_true", help="Show the browser window while searching.")
    ap.add_argument("--dry-run", action="store_true", help="Print results; don't email or update state.")
    ap.add_argument(
        "--part",
        choices=["auto", "data", "ai", "all"],
        default="auto",
        help="Which company searches to run. auto = data before noon, ai after.",
    )
    ap.add_argument("--limit-batches", type=int, default=None, help="Only search the first N company batches.")
    ap.add_argument("--skip-generic", action="store_true", help="Skip the generic (no-company) searches.")
    ap.add_argument("--skip-companies", action="store_true", help="Skip the per-company searches.")
    args = ap.parse_args()
    if args.login:
        return login(PROFILE_DIR)
    try:
        return run(args.dry_run, args.headed, args.part, args.limit_batches, args.skip_generic, args.skip_companies)
    except Exception:
        err = traceback.format_exc()
        print(err)
        if not args.dry_run:
            _alert("Run failed", f"The scraper crashed:\n\n{err[-3000:]}\n\nLog: logs/run.log")
        return 1


if __name__ == "__main__":
    sys.exit(main())
