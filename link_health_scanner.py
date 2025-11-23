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
import xml.etree.ElementTree as ET
from collections import deque, defaultdict
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
    referrers: List[str] = field(default_factory=list)
    status: str = "unknown"
    status_code: Optional[int] = None
    redirected_to: Optional[str] = None
    issues: List[str] = field(default_factory=list)
    outdated_signals: List[str] = field(default_factory=list)
    content_type: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "url": self.url,
            "referrers": self.referrers,
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
        check_orphans: bool = False,
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
        self.check_orphans = check_orphans

        self._base_host = urlparse(self.start_url).netloc
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})

    def run(self) -> Dict[str, object]:
        """Run the crawl and return reports plus summary statistics."""
        queue: Deque[Tuple[str, int]] = deque()
        queue.append((self.start_url, 0))
        queued: Set[str] = {self.start_url}
        visited: Set[str] = set()
        referrer_map: Dict[str, Set[str]] = defaultdict(set)
        sitemap_candidates: Set[str] = set()
        if self.check_orphans:
            sitemap_candidates = self._fetch_sitemap_urls()

        reports: List[LinkReport] = []
        pages_crawled = 0
        total_requests = 0

        while queue and total_requests < self.max_requests:
            url, depth = queue.popleft()
            queued.discard(url)
            if url in visited:
                continue
            visited.add(url)

            total_requests += 1
            report, discovered_links, is_html = self._check_url(url)
            report.referrers = sorted(referrer_map.get(url, []))
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
                    referrer_map[normalized].add(url)
                    if normalized in visited or normalized in queued:
                        continue
                    if total_requests + len(queued) >= self.max_requests:
                        break
                    queue.append((normalized, depth + 1))
                    queued.add(normalized)

        summary = self._build_summary(reports)
        orphan_links: List[str] = []
        sitemap_only_links: List[str] = []
        if self.check_orphans:
            orphan_links, sitemap_only_links = self._find_unused_links(
                reports, sitemap_candidates, visited
            )
            summary["unused"] = len(orphan_links) + len(sitemap_only_links)
        else:
            summary["unused"] = 0
        return {
            "reports": reports,
            "summary": summary,
            "unused_links": orphan_links,
            "sitemap_only_links": sitemap_only_links,
        }

    def _check_url(self, url: str) -> Tuple[LinkReport, Set[str], bool]:
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
            "unused": 0,
        }
        for report in reports:
            if report.status in summary:
                summary[report.status] += 1
            if report.outdated_signals:
                summary["outdated"] += 1
        return summary

    def _find_unused_links(
        self,
        reports: List[LinkReport],
        sitemap_urls: Set[str],
        visited_urls: Set[str],
    ) -> Tuple[List[str], List[str]]:
        """Identify orphan pages and sitemap entries never visited."""
        orphans: Set[str] = set()
        sitemap_only: Set[str] = set()
        for report in reports:
            if (
                self._is_same_domain(report.url)
                and report.url != self.start_url
                and not report.referrers
            ):
                orphans.add(report.url)
        for url in sitemap_urls:
            if self._is_same_domain(url) and url not in visited_urls:
                sitemap_only.add(url)
        return sorted(orphans), sorted(sitemap_only)

    def _fetch_sitemap_urls(self) -> Set[str]:
        """Attempt to fetch sitemap.xml to discover unlinked pages."""
        sitemap_url = urljoin(self.start_url, "/sitemap.xml")
        candidates: Set[str] = set()
        try:
            response = self._session.get(sitemap_url, timeout=self.timeout)
            if response.status_code != 200:
                return candidates
            root = ET.fromstring(response.text)
            for loc in root.iter():
                if loc.tag.endswith("loc") and loc.text:
                    normalized = self._normalize_link(self.start_url, loc.text.strip())
                    if normalized and self._is_same_domain(normalized):
                        candidates.add(normalized)
        except (requests.RequestException, ET.ParseError):
            return candidates
        return candidates


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
        "--check-orphans",
        action="store_true",
        help="Also flag orphan/sitemap-only routes (default: off)",
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
        check_orphans=args.check_orphans,
        max_pages=args.max_pages,
        max_requests=args.max_requests,
        max_depth=args.max_depth,
        timeout=args.timeout,
        outdated_days=args.outdated_days,
    )
    start_url = scanner.start_url
    result = scanner.run()
    reports: List[LinkReport] = result["reports"]  # type: ignore[assignment]
    summary: Dict[str, int] = result["summary"]  # type: ignore[assignment]
    unused_links: List[str] = result.get("unused_links", [])
    sitemap_only_links: List[str] = result.get("sitemap_only_links", [])

    if args.as_json:
        payload = {
            "summary": summary,
            "reports": [report.to_dict() for report in reports],
            "unused_links": unused_links,
            "sitemap_only_links": sitemap_only_links,
        }
        print(json.dumps(payload, indent=2))
        return 0

    print("Link Health Scanner")
    print("==================")
    print(f"Total checked: {summary['total']}")
    print(f"OK: {summary['ok']}")
    print(f"Broken: {summary['broken']}")
    print(f"Server errors: {summary['server-error']}")
    print(f"Redirects: {summary['redirect']}")
    print(f"Outdated pages detected: {summary['outdated']}")
    if args.check_orphans:
        print(f"Unused / orphan links: {summary.get('unused', 0)}")
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
            if rep.referrers:
                sources = ", ".join(rep.referrers[:3])
                if len(rep.referrers) > 3:
                    sources += ", ..."
                parts.append(f"from {sources}")
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
    if unused_links:
        print("Unused / Orphan Links")
        for url in unused_links:
            print(f"  - {url}")
        print()
    if sitemap_only_links:
        print("Sitemap-only Links (never visited)")
        for url in sitemap_only_links:
            print(f"  - {url}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
