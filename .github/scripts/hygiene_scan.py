#!/usr/bin/env python3
"""Whole-repo hygiene scan for the public matt-loop repo.

Scans every git-tracked text file for privacy-leak shapes. Reports
file:line:category ONLY — never the matched content (CI logs are public).
Exit 1 on any hit, 0 when clean. Stdlib only; run locally with:

    python3 .github/scripts/hygiene_scan.py

Categories:
  home_path        machine-specific absolute paths (/Users/<name>, /home/<name>)
  private_ip       RFC1918 addresses (10.x, 192.168.x, 172.16-31.x)
  secret_shape     common key/token shapes (sk-, ghp_, github_pat_, AKIA, xox*, PEM)
  webhook_url      webhook endpoints with token-bearing paths
  email            real-looking email addresses (placeholder domains allowed)
  tracked_forbidden  webhook.env / .loop/ must never be git-tracked

Allowlist entries are (path, category, exact matched substring) triples for
documented expected false positives; keep them minimal and explicit.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# This script's own source contains the pattern literals; skip it.
SELF = ".github/scripts/hygiene_scan.py"

PATTERNS: dict[str, re.Pattern[str]] = {
    "home_path": re.compile(r"/Users/[A-Za-z0-9_.-]+|/home/[A-Za-z0-9_.-]+"),
    "private_ip": re.compile(
        r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
        r"|\b192\.168\.\d{1,3}\.\d{1,3}\b"
        r"|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"
    ),
    "secret_shape": re.compile(
        r"\bsk-[A-Za-z0-9_-]{20,}"
        r"|\bghp_[A-Za-z0-9]{36}\b"
        r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
        r"|\bAKIA[0-9A-Z]{16}\b"
        r"|\bxox[baprs]-[A-Za-z0-9-]{10,}"
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    ),
    "webhook_url": re.compile(
        r"hooks\.slack\.com/services/[A-Za-z0-9/]+"
        r"|discord\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"
    ),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}

EMAIL_ALLOWED_DOMAINS = (
    "example.com", "example.invalid", "example.org",
    "users.noreply.github.com",
)

# (path, category, exact matched substring) — documented expected hits only.
ALLOWLIST: list[tuple[str, str, str]] = [
    # Fake proxy credential in a test fixture (fictional .corp domain,
    # joke password); the test asserts it never leaks into output.
    ("loop/scripts/test_dispatch_webhook_diag.py", "email", "hunter2@sysproxy.corp"),
]


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def scan(paths: list[str]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        base = path.rsplit("/", 1)[-1]
        if base == "webhook.env" or path.startswith(".loop/") or "/.loop/" in path:
            hits.append(f"{path}:0:tracked_forbidden")
        if path == SELF:
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: not a text leak surface
        if "\0" in text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for cat, pat in PATTERNS.items():
                for m in pat.finditer(line):
                    matched = m.group(0)
                    if cat == "email" and matched.rsplit("@", 1)[-1].lower() in EMAIL_ALLOWED_DOMAINS:
                        continue
                    if any(path == p and cat == c and matched == s for p, c, s in ALLOWLIST):
                        continue
                    hits.append(f"{path}:{lineno}:{cat}")
    return hits


def main() -> int:
    hits = scan(tracked_files())
    for h in hits:
        print(h)  # file:line:category only — never content
    if hits:
        print(f"HYGIENE FAIL: {len(hits)} hit(s). Redact with placeholders; "
              "see docs/agents/issue-tracker.md privacy redlines.", file=sys.stderr)
        return 1
    print("hygiene: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
