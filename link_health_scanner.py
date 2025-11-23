"""
Link Health Scanner core logic.

This module exposes a `LinkHealthScanner` class that can crawl a site,
collect discovered links, check their HTTP status, and look for signals
that the content is outdated (old Last-Modified headers, stale years, etc.).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Deque, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import requests


class _LinkExtractor(HTMLParser):
    """Lightweight HTML parser for collecting href/src attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.found: Set[str] = set()

    def handle_starttag(
        self, tag: str, attrs: Iterable[Tuple[str, Optional[str]]]
    ) -> None:
        if tag.lower() not in {"a", "img", "script", "link"}:
            return
        for attr, value in attrs:
            if attr not in {"href", "src"}:
                continue
            if value:
                self.found.add(value.strip())


@dataclass
class LinkReport:
    url: str
    referrer: Optional[str]
    status: str
    status_code: Optional[int]
    redirected_to: Optional[str]
    issues: List[str] = field(default_factory=list)
    outdated_signals: List[str] = field(default_factory=list)
    content_type: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "url": self.url,
            "referrer": self.referrer,
            "status": self.status,
            "status_code": self.status_code,
            "redirected_to": self.redirected_to,
            "issues": self.issues,
            "outdated_signals": self.outdated_signals,
            "content_type": self.content_type,
        }


class LinkHealthScanner:
    """Crawl and audit links for HTTP errors, redirects, and stale content."""

    STALE_PHRASES = (
        "under construction",
        "coming soon",
        "lorem ipsum",
        "outdated",
        "last updated 20",
    )

    def __init__(
        self,
        start_url: str,
        *,
        include_external: bool = False,
        max_pages: int = 150,
        max_requests: int = 500,
        max_depth: int = 3,
        timeout: int = 10,
        outdated_days: int = 365,
        user_agent: str = "LinkHealthScanner/1.0 (+https://example.com)",
    ) -> None:
        if not start_url.startswith(("http://", "https://")):
            raise ValueError("Start URL must include scheme (http/https)")
        self.start_url = start_url.rstrip("/")
        self.include_external = include_external
        self.max_pages = max_pages
        self.max_requests = max_requests
        self.max_depth = max_depth
        self.timeout = timeout
        self.outdated_days = outdated_days

        self._base_host = urlparse(self.start_url).netloc
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})

    def run(self) -> Dict[str, object]:
        """Run the crawl and return reports plus summary statistics."""
        queue: Deque[Tuple[str, Optional[str], int]] = deque()
        queue.append((self.start_url, None, 0))
        queued: Set[str] = {self.start_url}
        visited: Set[str] = set()

        reports: List[LinkReport] = []
        pages_crawled = 0
        total_requests = 0

        while queue and total_requests < self.max_requests:
            url, referrer, depth = queue.popleft()
            queued.discard(url)
            if url in visited:
                continue
            visited.add(url)

            total_requests += 1
            report, discovered_links, is_html = self._check_url(url, referrer)
            reports.append(report)

            if (
                is_html
                and report.status_code
                and report.status_code < 400
                and pages_crawled < self.max_pages
                and depth < self.max_depth
            ):
                pages_crawled += 1
                for raw_link in discovered_links:
                    normalized = self._normalize_link(url, raw_link)
                    if not normalized:
                        continue
                    if (not self.include_external) and (
                        not self._is_same_domain(normalized)
                    ):
                        continue
                    if normalized in visited or normalized in queued:
                        continue
                    if total_requests + len(queued) >= self.max_requests:
                        break
                    queue.append((normalized, url, depth + 1))
                    queued.add(normalized)

        summary = self._build_summary(reports)
        return {"reports": reports, "summary": summary}

    def _check_url(
        self, url: str, referrer: Optional[str]
    ) -> Tuple[LinkReport, Set[str], bool]:
        issues: List[str] = []
        outdated_signals: List[str] = []
        discovered_links: Set[str] = set()
        content_type: Optional[str] = None
        redirected_to: Optional[str] = None
        status_code: Optional[int] = None
        status = "unknown"
        is_html = False

        try:
            response = self._session.get(
                url, timeout=self.timeout, allow_redirects=True
            )
            status_code = response.status_code
            content_type = response.headers.get("Content-Type")
            if response.history:
                history_codes = [r.status_code for r in response.history]
                redirected_to = response.url
                issues.append(
                    f"Redirect chain {' -> '.join(map(str, history_codes + [status_code]))}"
                )
            if status_code >= 500:
                status = "server-error"
                issues.append("Server error")
            elif status_code >= 400:
                status = "broken"
                issues.append("Client error")
            elif status_code >= 300:
                status = "redirect"
            else:
                status = "ok"

            if (
                content_type
                and "text/html" in content_type.lower()
                and status_code < 400
            ):
                is_html = True
                outdated_signals = self._detect_outdated(response, response.text)
                extractor = _LinkExtractor()
                extractor.feed(response.text)
                discovered_links = extractor.found
        except requests.RequestException as exc:
            issues.append(str(exc))
            status = "error"

        report = LinkReport(
            url=url,
            referrer=referrer,
            status=status,
            status_code=status_code,
            redirected_to=redirected_to,
            issues=issues,
            outdated_signals=outdated_signals,
            content_type=content_type,
        )
        return report, discovered_links, is_html

    def _detect_outdated(self, response: requests.Response, text: str) -> List[str]:
        signals: List[str] = []
        last_modified = response.headers.get("Last-Modified")
        now = _dt.datetime.utcnow()
        if last_modified:
            try:
                parsed = parsedate_to_datetime(last_modified)
                if parsed.tzinfo:
                    parsed = parsed.astimezone(_dt.timezone.utc).replace(tzinfo=None)
                age = now - parsed
                if age.days > self.outdated_days:
                    signals.append(
                        f"Last-Modified is {age.days} days ago ({last_modified})"
                    )
            except (TypeError, ValueError):
                pass

        years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", text)]
        if years:
            newest_year = max(years)
            if newest_year < now.year - 1:
                signals.append(f"Latest year mentioned is {newest_year}")

        lowered = text.lower()
        for phrase in self.STALE_PHRASES:
            if phrase in lowered:
                signals.append(f"Contains '{phrase}'")

        return signals

    def _normalize_link(self, base: str, link: str) -> Optional[str]:
        if not link or link.startswith("#"):
            return None
        if link.startswith(("mailto:", "tel:", "javascript:")):
            return None
        absolute = urljoin(base, link)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            return None
        cleaned = parsed._replace(fragment="")
        normalized = urlunparse(cleaned)
        return normalized.rstrip("/")

    def _is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self._base_host

    @staticmethod
    def _build_summary(reports: List[LinkReport]) -> Dict[str, int]:
        summary = {
            "total": len(reports),
            "ok": 0,
            "broken": 0,
            "server-error": 0,
            "redirect": 0,
            "error": 0,
            "outdated": 0,
        }
        for report in reports:
            if report.status in summary:
                summary[report.status] += 1
            if report.outdated_signals:
                summary["outdated"] += 1
        return summary


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit a site for broken links and outdated pages."
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="Starting URL (including http/https). Leave blank to enter interactively.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=150,
        help="Maximum number of HTML pages to crawl (default: 150)",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=500,
        help="Maximum number of HTTP requests (default: 500)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Depth limit for crawling (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--outdated-days",
        type=int,
        default=365,
        help="Flag pages as outdated if Last-Modified exceeds this age (default: 365)",
    )
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="Also crawl and audit external domains (default: off)",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output machine-readable JSON",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    start_url = args.url
    if not start_url:
        try:
            start_url = input("Enter the starting URL (including http/https): ").strip()
        except EOFError:
            start_url = ""
    if not start_url:
        parser.error("A starting URL is required.")

    scanner = LinkHealthScanner(
        start_url,
        include_external=args.include_external,
        max_pages=args.max_pages,
        max_requests=args.max_requests,
        max_depth=args.max_depth,
        timeout=args.timeout,
        outdated_days=args.outdated_days,
    )
    result = scanner.run()
    reports: List[LinkReport] = result["reports"]  # type: ignore[assignment]
    summary: Dict[str, int] = result["summary"]  # type: ignore[assignment]

    if args.as_json:
        payload = {
            "summary": summary,
            "reports": [report.to_dict() for report in reports],
        }
        print(json.dumps(payload, indent=2))
        return 0

    print("Link Health Scanner")
    print("==================")
    print(f"Total checked: {summary['total']}")
    print(
        f"OK: {summary['ok']}  Broken: {summary['broken']}  "
        f"Server errors: {summary['server-error']}  Redirects: {summary['redirect']}"
    )
    print(f"Outdated pages detected: {summary['outdated']}")
    print()

    def _print_section(title: str, condition):
        matching = [r for r in reports if condition(r)]
        if not matching:
            return
        print(title)
        for rep in matching:
            parts = [f"- {rep.url}"]
            if rep.status_code:
                parts.append(f"(HTTP {rep.status_code})")
            if rep.referrer:
                parts.append(f"from {rep.referrer}")
            if rep.redirected_to:
                parts.append(f"-> {rep.redirected_to}")
            if rep.issues:
                parts.append(f"Issues: {', '.join(rep.issues)}")
            if rep.outdated_signals:
                parts.append(f"Outdated: {', '.join(rep.outdated_signals)}")
            print("  " + " ".join(parts))
        print()

    _print_section("Broken / Error Links", lambda r: r.status in {"broken", "error"})
    _print_section(
        "Server Errors",
        lambda r: r.status == "server-error",
    )
    _print_section("Redirects", lambda r: r.redirected_to is not None)
    _print_section("Outdated Content", lambda r: bool(r.outdated_signals))

    return 0


if __name__ == "__main__":
    sys.exit(main())
