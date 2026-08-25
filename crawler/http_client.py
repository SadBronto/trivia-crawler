"""Shared polite HTTP client for non-JS sources.

- Identifies itself honestly in the User-Agent (not spoofing a browser) so
  a site operator who notices unusual traffic can see what it is and how
  to reach us, and includes a contact so they can ask us to back off.
- Rate-limits between requests to the same run (a fixed delay is crude but
  predictable, and easy to raise if a site pushes back).
- Retries transient failures a couple of times before giving up.
- Skips any URL robots.txt disallows for our user-agent.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import requests

USER_AGENT = "TriviaCoopCrawler/0.1 (+https://github.com/SadBronto; research/directory-building bot, contact via GitHub)"
DEFAULT_DELAY_SECONDS = 1.5
MAX_RETRIES = 3


class _Robots:
    """Minimal robots.txt evaluator.

    Deliberately not using stdlib urllib.robotparser - it does NOT
    implement the "longest/most-specific rule wins" precedence that the
    de facto robots.txt standard (and every major search engine) actually
    uses. Confirmed directly: given
        Disallow: /wp-admin/
        Allow: /wp-admin/admin-ajax.php
    stdlib's can_fetch() returns False for admin-ajax.php - it matches
    rules in file order and stops at the first hit, so the broader
    Disallow always wins even though the more specific Allow should. That
    exact pattern is what Geeks Who Drink's robots.txt uses (their own JS
    widget calls admin-ajax.php - it's deliberately carved out), so this
    isn't a hypothetical edge case.
    """

    def __init__(self, lines: list[str] | None):
        # (path_prefix, allowed) pairs that apply to us - either our exact
        # UA token or the wildcard group. Grouped by user-agent block per
        # the spec (a UA-specific block replaces the wildcard entirely).
        self.rules: list[tuple[str, bool]] = []
        if not lines:
            return

        our_token = USER_AGENT.split("/")[0]  # "TriviaCoopCrawler"
        blocks: dict[str, list[tuple[str, bool]]] = {}
        current_agents: list[str] = []
        for raw_line in lines:
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            value = value.strip()
            if field == "user-agent":
                current_agents = [value]
                for agent in current_agents:
                    blocks.setdefault(agent.lower(), [])
            elif field in ("disallow", "allow") and current_agents:
                allowed = field == "allow"
                if value == "" and not allowed:
                    continue  # "Disallow:" with empty value means allow everything
                for agent in current_agents:
                    blocks.setdefault(agent.lower(), []).append((value, allowed))

        if our_token.lower() in blocks:
            self.rules = blocks[our_token.lower()]
        else:
            self.rules = blocks.get("*", [])

    def can_fetch(self, path: str) -> bool:
        if not self.rules:
            return True
        best_match = ("", True)  # (prefix, allowed) - default allow if nothing matches
        for prefix, allowed in self.rules:
            if path.startswith(prefix) and len(prefix) >= len(best_match[0]):
                best_match = (prefix, allowed)
        return best_match[1]


class PoliteSession:
    def __init__(self, delay_seconds: float = DEFAULT_DELAY_SECONDS):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.delay_seconds = delay_seconds
        self._last_request_at = 0.0
        self._robots_cache: dict[str, _Robots] = {}

    def _robots_for(self, url: str) -> _Robots:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots_cache:
            try:
                resp = self.session.get(origin + "/robots.txt", timeout=10)
                lines = resp.text.splitlines() if resp.status_code == 200 else None
            except requests.RequestException:
                lines = None  # unreachable robots.txt -> fail open (no rules = allow all)
            self._robots_cache[origin] = _Robots(lines)
        return self._robots_cache[origin]

    def allowed(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            return self._robots_for(url).can_fetch(path)
        except Exception:
            return True

    def get(self, url: str, **kwargs) -> requests.Response | None:
        if not self.allowed(url):
            print(f"  [skip] robots.txt disallows: {url}")
            return None

        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, timeout=20, **kwargs)
                self._last_request_at = time.monotonic()
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 404:
                    return None
                last_exc = RuntimeError(f"HTTP {resp.status_code}")
            except requests.RequestException as exc:
                last_exc = exc
            time.sleep(min(2**attempt, 10))
        print(f"  [fail] {url}: {last_exc}")
        return None
