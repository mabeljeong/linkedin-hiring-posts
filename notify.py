"""Email notifications via Gmail SMTP.

Reads credentials from env vars (injected by GitHub Secrets in CI):
  - EMAIL_SENDER        Gmail address sending the alert
  - EMAIL_APP_PASSWORD  16-char Gmail app password
  - EMAIL_RECIPIENT     Where to send alerts (defaults to EMAIL_SENDER)
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from html import escape
from typing import Iterable

from relevance import is_hiring_manager
from scraper import Post

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SNIPPET_CHARS = 350


class MissingCredentialsError(RuntimeError):
    pass


def _get_creds() -> tuple[str, str, str]:
    sender = os.environ.get("EMAIL_SENDER", "").strip()
    password = os.environ.get("EMAIL_APP_PASSWORD", "").strip()
    recipient = os.environ.get("EMAIL_RECIPIENT", "").strip() or sender
    if not sender or not password:
        raise MissingCredentialsError(
            "EMAIL_SENDER and EMAIL_APP_PASSWORD env vars must be set."
        )
    return sender, password, recipient


def _send(subject: str, text: str, html: str | None = None) -> None:
    sender, password, recipient = _get_creds()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as server:
        server.login(sender, password)
        server.send_message(msg)
    print(f"[notify] Sent '{subject}' to {recipient}")


def _snippet(text: str) -> str:
    return text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS].rstrip() + "..."


def _group(posts: list[Post]) -> list[tuple[str, list[Post]]]:
    """Target companies first (alphabetical), then generic results from people
    hiring for their own team, then other generic results. US posts come first."""
    last = ("Hiring managers", "Recruiters and others")
    groups: dict[str, list[Post]] = {}
    for p in posts:
        key = p.company if p.section == "company" else last[0 if is_hiring_manager(p) else 1]
        groups.setdefault(key, []).append(p)
    ordered = sorted(k for k in groups if k not in last) + [k for k in last if k in groups]
    return [(k, sorted(groups[k], key=lambda p: p.region != "us")) for k in ordered]


def _format_body(posts: list[Post]) -> tuple[str, str]:
    lines = [f"{len(posts)} new LinkedIn hiring post(s) found:\n"]
    html_parts = [f"<p><b>{len(posts)} new LinkedIn hiring post(s) found.</b></p>"]

    for group, items in _group(posts):
        lines.append(f"== {group} ({len(items)}) ==")
        html_parts.append(f"<h3>{escape(group)} ({len(items)})</h3><ul>")
        for p in items:
            who = p.author or "Unknown author"
            where = {"us": "US", "non-us": "Non-US", "unknown": "Location not stated"}.get(p.region, "")
            meta = " | ".join(x for x in (p.headline, p.posted_label, where) if x)
            lines.append(f"- {who}" + (f" ({meta})" if meta else ""))
            lines.append(f"  {_snippet(p.text)}")
            lines.append(f"  Post: {p.url}")
            if p.job_url:
                lines.append(f"  Job: {p.job_url}")
            lines.append("")
            job = f'<br><a href="{escape(p.job_url)}">Attached job</a>' if p.job_url else ""
            html_parts.append(
                f'<li><a href="{escape(p.url)}"><b>{escape(who)}</b></a>'
                f'{"<br><small>" + escape(meta) + "</small>" if meta else ""}'
                f"<br>{escape(_snippet(p.text))}{job}"
                f'<br><small style="color:#888">query: {escape(p.query)}</small></li>'
            )
        html_parts.append("</ul>")

    html = "<html><body>" + "".join(html_parts) + "</body></html>"
    return "\n".join(lines), html


def send_new_posts_email(posts: Iterable[Post]) -> None:
    posts = list(posts)
    if not posts:
        return
    text, html = _format_body(posts)
    _send(f"[Hiring Posts] {len(posts)} new LinkedIn post(s)", text, html)


def send_alert_email(subject: str, body: str) -> None:
    _send(f"[Hiring Posts] {subject}", body)
