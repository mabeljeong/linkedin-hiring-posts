# LinkedIn Hiring-Post Alerts

Many roles are announced first in a hiring manager's LinkedIn **post**
("I'm hiring a data scientist for my team…") before they reach the Jobs board,
and messaging that person directly is often the best way in. This tool finds
those posts automatically. Twice a day it searches LinkedIn posts for data and
AI hiring announcements, filters out the noise, and emails a digest of new ones.

```
[Hiring Posts] 38 new LinkedIn post(s)

== Mastercard (1) ==
- Jane Doe (Senior Talent Acquisition Specialist @ Mastercard | 2d | US)
  We're hiring Data Engineers in O'Fallon, MO…
  Post: https://lnkd.in/p/…
  Job:  https://www.linkedin.com/jobs/view/…

== General search (29) ==
- John Smith (Sr. Data Science Manager at PlayStation | 11h | US)
  I'm hiring a Data Science Manager to join my team…
```

> **Disclaimer:** This is a personal, educational project. Automated access to
> LinkedIn is against its User Agreement and can get an account restricted. Use
> it at your own risk, at low volume, on your own account. It is not affiliated
> with LinkedIn.

## How it works

```
Task Scheduler (6am / 1pm)
  └─ run.sh  (lock, 45-min timeout, alerts)
       └─ main.py
            ├─ scraper.py   real Chrome + saved login profile
            │                 → Posts search (past week, latest first)
            │                 → read post cards from the page
            ├─ filters      person-posted? hiring for a target company?
            │               job seeker / staffing / ad? US-based?
            ├─ dedup        3 fingerprints per post vs state/seen_posts.json
            ├─ notify.py    HTML + plain-text email digest (Gmail SMTP)
            └─ state        save everything seen, only after the email is sent
```

**Searches.** Company searches group the target companies 4 at a time, for example:

```
("Stripe" OR "Airbnb" OR "Netflix" OR "Spotify") hiring ("data engineer" OR "data scientist" OR "analytics engineer")
("Stripe" OR "Airbnb" OR "Netflix" OR "Spotify") hiring ("machine learning" OR "evals" OR "AI quality")
```

Three generic searches also run every time, such as
`hiring ("data engineer" OR "data scientist") "my team"`. Data searches run in
the morning and AI searches in the afternoon. Every search covers the past week,
so alternating halves the load without missing anything.

**Filters.** A post is emailed only if all of these hold:

- It's posted by a person (`/in/` profile), not a company page or job board.
- It's not a job seeker ("open to work", "immediate joiner", "I was laid off").
- It's not contract staffing (C2C/W2, bench sales) or an ad.
- For company searches, the author's headline names the company, or the post
  is about hiring for it ("my team at X", "X is hiring"). A passing mention,
  such as "5 years of X experience", doesn't count.
- It's US-based, judged from places in the post, the headline and any attached
  job card (`US_FILTER=soft|strict|off`).

## Engineering notes

Making this reliable meant working around several undocumented behaviors.

| Problem | What I found | Solution |
|---|---|---|
| Session cookie got revoked | A pasted `li_at` cookie used from a headless browser was invalidated within minutes | Use a real Chrome **persistent profile** with a one-time manual login (`--login`) |
| Searches silently returned 0 | Posts search returns nothing once a query has **more than 7 OR'd quoted terms**: 7 works, 8 and 9 return 0 | Groups of 4 companies + 3 role terms, plus `validate_queries()` to fail fast at startup |
| No post IDs in the page | The new search page uses generated class names and has no post URNs in its markup or component data | Read posts through stable hooks (`role="listitem"`, the "Open control menu for post by…" label, `data-testid="expandable-text-box"`). Get the real link with "Copy link to post" via the clipboard, **only for posts that will be emailed** |
| Duplicate emails | Without IDs, edits or a shifting "…more" cut-off change a post's text | 3 fingerprints per post (author + opening, profile + first 80 chars, profile + attached job). Any match counts as seen |
| Bot checks | Company pages showed a "Just a moment…" challenge | Only search pages are used. Random 20–45 s pauses and human-like scrolling |
| Laptop sleep froze a run | A frozen run held the Chrome profile lock, so the next run crashed silently | `flock`, a 45-minute `timeout`, stale-lock cleanup, and alert emails on crash or timeout |
| Busy searches truncated | Only the first page of results was read | Keep scrolling while new posts load (up to 100) |

## Setup

Requirements: Python 3.11+, Google Chrome, and a Gmail account for sending the digest.

```bash
git clone https://github.com/<you>/linkedin-hiring-posts.git
cd linkedin-hiring-posts
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**1. Log in to LinkedIn once.** A Chrome window opens; log in by hand. The
session is saved in `~/.config/linkedin-posts-profile`.

```bash
.venv/bin/python main.py --login
```

**2. Add email credentials** in `.env`, which is git-ignored. Create the app
password at <https://myaccount.google.com/apppasswords>.

```
EMAIL_SENDER="you@gmail.com"
EMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
EMAIL_RECIPIENT="you@gmail.com"
```

**3. Choose companies (optional)** in `companies.json`, which is git-ignored.
Without it, the example list in `config.py` is used.

```json
{
  "PayPal": ["PayPal"],
  "Bristol Myers Squibb": ["Bristol Myers Squibb", "BMS"]
}
```

**4. Test it, then schedule it.**

```bash
.venv/bin/python main.py --dry-run --limit-batches 1   # print matches; no email, no state
./run.sh                                               # real run; output goes to logs/run.log
```

To schedule it on Linux or macOS, add a cron entry: `0 6,13 * * * /path/to/run.sh`.
On Windows with WSL, use Task Scheduler with the program `conhost.exe` and the
arguments `--headless wsl.exe -e /path/to/run.sh`, and enable "Run task as soon
as possible after a scheduled start is missed".

## Options

| Flag / env var | Effect |
|---|---|
| `--dry-run` | Print matches; don't email or save state |
| `--part auto/data/ai/all` | Which company searches to run (auto: data before noon, ai after) |
| `--limit-batches N` | Only search the first N company groups |
| `--skip-generic` / `--skip-companies` | Skip one part of the search |
| `--headed` | Show the browser window |
| `US_FILTER` | `soft` (default), `strict`, or `off` |
| `LINKEDIN_MIN_DELAY_MS` / `LINKEDIN_MAX_DELAY_MS` | Pause between searches (default 20000–45000) |
| `DEBUG_DIR=debug` | Save a screenshot when a search returns no posts |

## Project layout

```
main.py       orchestration: searches, filters, dedup, email, state
scraper.py    browser session, search, post extraction, filters
config.py     queries, 7-term validation, company list loading
location.py   US / non-US classification from free text
notify.py     email digest and alerts
run.sh        scheduler entry point: lock, timeout, alerts
```
