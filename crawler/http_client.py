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
import urllib.robotparser
from urllib.parse import urlparse

import requests

USER_AGENT = "TriviaCoopCrawler/0.1 (+https://github.com/SadBronto; research/directory-building bot, contact via GitHub)"
DEFAULT_DELAY_SECONDS = 1.5
MAX_RETRIES = 3


class PoliteSession:
    def __init__(self, delay_seconds: float = DEFAULT_DELAY_SECONDS):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.delay_seconds = delay_seconds
        self._last_request_at = 0.0
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots_cache:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(origin + "/robots.txt")
            # Deliberately not using rp.read() - it fetches with urllib's
            # default "Python-urllib/x.y" User-Agent (no custom headers
            # supported), which some hosts 403 outright. That makes
            # RobotFileParser treat the site as unreadable and set
            # disallow_all=True - the opposite of "fail open" - even
            # though the exact same robots.txt fetches fine with our real
            # UA. Fetch it ourselves through the same session instead.
            try:
                resp = self.session.get(origin + "/robots.txt", timeout=10)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp.allow_all = True  # no robots.txt / can't reach it -> fail open
            except requests.RequestException:
                rp.allow_all = True
            self._robots_cache[origin] = rp
        return self._robots_cache[origin]

    def allowed(self, url: str) -> bool:
        try:
            return self._robots_for(url).can_fetch(USER_AGENT, url)
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
