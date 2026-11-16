"""LinkedIn Posts (content) search scraper.

Posts search requires a logged-in session. This drives real Google Chrome with
a persistent profile on disk: you log in once by hand (`python main.py --login`)
and later runs reuse that session. LinkedIn revoked pasted li_at cookies used
from an automated browser, so cookie auth is deliberately not supported.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Callable
from pathlib import Path
from urllib.parse import quote, urlencode

from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeout, sync_playwright
from playwright_stealth import Stealth

SEARCH_BASE = "https://www.linkedin.com/search/results/content/"
MAX_SCROLLS = int(os.environ.get("LINKEDIN_MAX_SCROLLS", "12"))
MAX_RESULTS_PER_QUERY = int(os.environ.get("LINKEDIN_MAX_RESULTS", "100"))
DEBUG_DIR = os.environ.get("DEBUG_DIR", "").strip()

AUTH_WALL_MARKERS = ("/login", "/authwall", "/checkpoint/", "/uas/login")


class NotAuthenticatedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Post:
    author: str
    headline: str
    text: str  # post body
    card_text: str  # everything on the card, incl. attached job (title, company, location)
    posted_label: str
    query: str
    section: str  # "company" or "generic"
    profile_url: str = ""
    job_url: str = ""
    link: str = ""  # post link from "Copy link to post"; only fetched for posts we email
    company: str = ""
    region: str = ""  # "us", "non-us", or "unknown"; set by main.py

    @property
    def post_id(self) -> str:
        # The search page no longer exposes post URNs, so identify a post by
        # its author plus the start of its text.
        body = re.sub(r"\W+", "", self.text.lower())[:200]
        key = f"{self.author}|{body}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    @property
    def dedup_keys(self) -> list[str]:
        """Every key that identifies this post. A post counts as already sent
        if ANY key was seen before, so it isn't re-sent when the author edits
        it or the "... more" cut-off shifts between page loads."""
        who = self.profile_url.rstrip("/") or self.author
        opening = re.sub(r"\W+", "", re.sub(r"(?:\.\.\.|…)\s*(?:see )?more$", "", self.text).lower())[:80]
        keys = [self.post_id, "p:" + hashlib.sha1(f"{who}|{opening}".encode("utf-8")).hexdigest()[:16]]
        if self.job_url:
            # Same author + same attached job = same announcement.
            keys.append("j:" + hashlib.sha1(f"{who}|{self.job_url}".encode("utf-8")).hexdigest()[:16])
        return keys

    @property
    def url(self) -> str:
        if self.link:
            return self.link
        if self.profile_url:
            return self.profile_url.rstrip("/") + "/recent-activity/all/"
        return ""


def build_search_url(keywords: str) -> str:
    params = {
        "keywords": keywords,
        "datePosted": '"past-week"',
        "sortBy": '"date_posted"',
        "origin": "FACETED_SEARCH",
    }
    return f"{SEARCH_BASE}?{urlencode(params, quote_via=quote)}"


def _clear_stale_lock(profile: Path) -> None:
    """Remove Chrome's SingletonLock left behind by a crashed/killed run.

    The lock is a symlink to "<hostname>-<pid>"; it's stale if that pid is gone.
    """
    lock = profile / "SingletonLock"
    if not lock.is_symlink():
        return
    pid = os.readlink(lock).rsplit("-", 1)[-1]
    if pid.isdigit() and Path(f"/proc/{pid}").exists():
        return  # a live Chrome really is using the profile
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink()
        except FileNotFoundError:
            pass
    print("[linkedin] Removed stale Chrome profile lock")


@contextmanager
def launch_browser(profile_dir: str, headless: bool = True):
    """Yield a page from real Chrome using a persistent on-disk profile."""
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    _clear_stale_lock(Path(profile_dir))
    with Stealth().use_sync(sync_playwright()) as p:
        context = p.chromium.launch_persistent_context(
            profile_dir,
            channel="chrome",
            headless=headless,
            permissions=["clipboard-read", "clipboard-write"],
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
            locale="en-US",
            timezone_id="America/Los_Angeles",
            viewport={"width": 1365, "height": 900},
            color_scheme="light",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(60000)
        try:
            yield page
        finally:
            context.close()


def _on_auth_wall(page: Page) -> bool:
    return any(marker in page.url for marker in AUTH_WALL_MARKERS)


def _goto(page: Page, url: str) -> None:
    """Navigate; an invalid session makes LinkedIn loop redirects."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightTimeout:
        raise
    except PlaywrightError as exc:
        if "ERR_TOO_MANY_REDIRECTS" in str(exc):
            raise NotAuthenticatedError(
                "LinkedIn redirect loop; the saved session is no longer valid."
            ) from exc
        raise


def check_auth(page: Page) -> None:
    _goto(page, "https://www.linkedin.com/feed/")
    page.wait_for_timeout(2000)
    if _on_auth_wall(page):
        raise NotAuthenticatedError(
            f"LinkedIn redirected to {page.url}; not logged in."
        )
    print("[linkedin] Logged-in session OK")


def _has_session(page: Page) -> bool:
    return any(c["name"] == "li_at" for c in page.context.cookies("https://www.linkedin.com"))


def interactive_login(page: Page, timeout_s: int = 1200) -> bool:
    """Open the login page and wait for the user to finish logging in by hand."""
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    page.bring_to_front()
    print(f"[login] Log in to LinkedIn in the browser window (waiting up to {timeout_s // 60} min)...")
    for _ in range(timeout_s):
        if _has_session(page) and not _on_auth_wall(page):
            page.wait_for_timeout(3000)
            print("[login] Logged in; session saved to the profile.")
            return True
        page.wait_for_timeout(1000)
    print("[login] Timed out waiting for login.")
    return False


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _first_line(text: str) -> str:
    return _clean((text or "").strip().split("\n", 1)[0])


MENU_BUTTON = "[aria-label^='Open control menu for post by']"
CARD = f"[role=listitem]:has({MENU_BUTTON})"
POSTED_RE = re.compile(r"^\d+\s*(?:s|m|h|d|w|mo|yr)s?\b", re.I)

_EXTRACT_JS = r"""(sel) => Array.from(document.querySelectorAll(sel.card)).map(li => {
    const btn = li.querySelector(sel.menu);
    const href = (pred) => {
        const a = Array.from(li.querySelectorAll("a[href]")).find(a => pred(a.getAttribute("href") || ""));
        return a ? a.getAttribute("href").split("?")[0] : "";
    };
    const body = li.querySelector("[data-testid=expandable-text-box]");
    return {
        author: (btn.getAttribute("aria-label") || "").replace("Open control menu for post by ", ""),
        lines: li.innerText.split("\n").map(s => s.trim()).filter(Boolean).slice(0, 8),
        card: li.innerText || "",
        text: body ? body.innerText : "",
        profile: href(h => h.includes("/in/") || h.includes("/company/")),
        job: href(h => h.includes("/jobs/view/")),
    };
})"""


def _parse_header(author: str, lines: list[str]) -> tuple[str, str]:
    """Return (headline, posted_label) from the card's first lines:
    ['Feed post', author, '• 2nd', headline, '11h', 'Follow', ...]"""
    headline = posted = ""
    try:
        start = lines.index(author) + 1
    except ValueError:
        start = 1
    for line in lines[start:]:
        if line.startswith("•") or line in ("Follow", "Feed post") or line == author:
            continue
        if POSTED_RE.match(line):
            posted = line.split("•")[0].strip()
            break
        if not headline:
            headline = line
    return headline, posted


def _save_debug(page: Page, label: str) -> None:
    if not DEBUG_DIR:
        return
    out = Path(DEBUG_DIR)
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", label)[:80]
    try:
        page.screenshot(path=str(out / f"{safe}.png"), full_page=False)
    except Exception:
        pass


def _copy_post_link(page: Page, index: int) -> str:
    """Open the post's "..." menu, click "Copy link to post", read the clipboard."""
    try:
        page.evaluate("navigator.clipboard.writeText('')")
        page.locator(CARD).nth(index).locator(MENU_BUTTON).click(timeout=5000)
        page.wait_for_timeout(random.randint(600, 1200))
        page.locator("text=Copy link to post").locator("visible=true").first.click(timeout=5000)
        page.wait_for_timeout(800)
        link = page.evaluate("navigator.clipboard.readText()") or ""
    except Exception as exc:
        print(f"[linkedin] Could not copy post link: {exc}")
        link = ""
    page.keyboard.press("Escape")
    return link.strip() if link.strip().startswith("http") else ""


def search_posts(
    page: Page,
    keywords: str,
    section: str,
    select: Callable[[Post], Post | None],
) -> tuple[list[str], list[Post]]:
    """Run one search. `select` returns the (possibly annotated) post to keep,
    or None to skip it. Kept posts get their link copied while still on the page.

    Returns (dedup keys of every post seen, kept posts).
    """
    try:
        _goto(page, build_search_url(keywords))
    except PlaywrightTimeout:
        print(f"[linkedin] Timeout loading search: {keywords}")
        return [], []
    if _on_auth_wall(page):
        raise NotAuthenticatedError(f"Session lost mid-run (redirected to {page.url})")

    page.wait_for_timeout(random.randint(4000, 6000))
    # Scroll while new posts keep loading; stop after two scrolls with nothing new.
    count, stale = page.locator(CARD).count(), 0
    for _ in range(MAX_SCROLLS):
        page.mouse.wheel(0, random.randint(1200, 2000))
        page.wait_for_timeout(random.randint(1500, 2600))
        new_count = page.locator(CARD).count()
        stale = stale + 1 if new_count == count else 0
        count = new_count
        if stale >= 2 or count >= MAX_RESULTS_PER_QUERY:
            break

    raw = page.evaluate(_EXTRACT_JS, {"card": CARD, "menu": MENU_BUTTON})
    if not raw:
        _save_debug(page, f"empty_{section}_{keywords}")

    seen_keys: list[str] = []
    kept: list[Post] = []
    for index, item in enumerate(raw[:MAX_RESULTS_PER_QUERY]):
        author = _clean(item.get("author", ""))
        headline, posted = _parse_header(author, item.get("lines", []))
        post = Post(
            author=author,
            headline=headline,
            text=_clean(item.get("text", "")),
            card_text=_clean(item.get("card", "")),
            posted_label=posted,
            query=keywords,
            section=section,
            profile_url=item.get("profile", ""),
            job_url=item.get("job", ""),
        )
        seen_keys.extend(post.dedup_keys)
        chosen = select(post)
        if chosen is None:
            continue
        kept.append(replace(chosen, link=_copy_post_link(page, index)))
    return seen_keys, kept


HIRING_CONTEXT = r"(?:team|join|joining|hiring|hire|role|roles|position|opening|openings|opportunity)"
JOB_SEEKER_RE = re.compile(
    r"open to work|#opentowork|immediate joiner|immediately available|"
    r"seeking (?:new |a new |full[- ]time )?(?:opportunit|role|position)|"
    r"looking for (?:new |a new |my next )?(?:opportunit|role|position)|"
    r"actively (?:looking|seeking)|i was (?:laid off|impacted)|lost my job|"
    r"my role at .{0,40}was (?:impacted|eliminated)",
    re.I,
)


def _alias_re(alias: str) -> str:
    return rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])"


def works_at_or_hiring_for(post: Post, aliases: tuple[str, ...]) -> bool:
    """True if the author's headline names the company, or the post talks about
    hiring for it ("my team at X", "join X", "X is hiring"). A bare mention
    (e.g. "5 years of PayPal experience") doesn't count."""
    for alias in aliases:
        a = _alias_re(alias)
        if re.search(a, post.headline, re.I):
            return True
        if re.search(rf"{HIRING_CONTEXT}\b.{{0,40}}{a}|{a}.{{0,25}}\b(?:is |are )?hiring", post.text, re.I):
            return True
    return False


def is_job_seeker(post: Post) -> bool:
    return bool(JOB_SEEKER_RE.search(f"{post.headline} {post.text}"))


STAFFING_RE = re.compile(
    r"\bC2C\b|\bcorp[- ]to[- ]corp\b|\bW2\b|\bbench sales\b|\bhotlist\b|"
    r"\bjob scout|\bjob board\b|\bjob alerts?\b|\bjobs? (?:portal|aggregator)\b",
    re.I,
)


def is_person_post(post: Post) -> bool:
    """Hiring managers and recruiters post from personal profiles (/in/);
    company pages and job boards post from /company/."""
    return "/in/" in post.profile_url


def is_staffing(post: Post) -> bool:
    return bool(STAFFING_RE.search(f"{post.headline} {post.text}"))


def is_promoted(post: Post) -> bool:
    return post.headline in ("Visit website", "Promoted") or "Promoted" in post.card_text[:200]
