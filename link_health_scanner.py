"""
Link Health Scanner core logic.

This module exposes a `LinkHealthScanner` class that can crawl a site,
collect discovered links, check their HTTP status, and look for signals
that the content is outdated (old Last-Modified headers, stale years, etc.).
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


def _shorten(text: str, limit: int = 100) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


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
    links_found: List[str] = field(default_factory=list)

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
            "links_found": self.links_found,
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
        check_orphans: bool = True,
        max_pages: int = 150,
        max_requests: int = 500,
        max_depth: int = 3,
        timeout: int = 10,
        outdated_days: int = 365,
        max_workers: int = 5,
        rate_limit: Optional[float] = None,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
        retry_statuses: Optional[Iterable[int]] = None,
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
        self.max_workers = max(1, max_workers)
        self.rate_limit = rate_limit if rate_limit and rate_limit > 0 else None
        self._min_interval = 1.0 / self.rate_limit if self.rate_limit else 0.0
        self.max_retries = max(0, max_retries)
        self.backoff_factor = max(0.0, backoff_factor)
        self.retry_statuses = set(
            retry_statuses or {408, 425, 429, 500, 502, 503, 504}
        )

        self._base_host = urlparse(self.start_url).netloc
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        adapter = HTTPAdapter(pool_maxsize=self.max_workers * 2)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._rate_lock = threading.Lock()
        self._last_request_time = 0.0

    def run(self) -> Dict[str, object]:
        """Run the crawl and return reports plus summary statistics."""
        queue: Deque[Tuple[str, int]] = deque()
        queue.append((self.start_url, 0))
        queued_urls: Set[str] = {self.start_url}
        visited: Set[str] = set()
        in_progress: Set[str] = set()
        referrer_map: Dict[str, Set[str]] = defaultdict(set)
        sitemap_candidates: Set[str] = set()
        if self.check_orphans:
            sitemap_candidates = self._fetch_sitemap_urls()

        reports: List[LinkReport] = []
        pages_crawled = 0
        completed_requests = 0

        futures = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while (queue or futures) and completed_requests < self.max_requests:
                while (
                    queue
                    and len(futures) < self.max_workers
                    and (completed_requests + len(futures)) < self.max_requests
                ):
                    url, depth = queue.popleft()
                    queued_urls.discard(url)
                    if url in visited or url in in_progress:
                        continue
                    in_progress.add(url)
                    future = executor.submit(self._check_url, url)
                    futures[future] = (url, depth)

                if not futures:
                    break

                done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    url, depth = futures.pop(future)
                    in_progress.discard(url)
                    try:
                        report, discovered_links, is_html = future.result()
                    except Exception as exc:  # pragma: no cover
                        report = LinkReport(url=url, status="error", issues=[str(exc)])
                        discovered_links = set()
                        is_html = False

                    report.referrers = sorted(referrer_map.get(url, []))
                    reports.append(report)
                    visited.add(url)
                    completed_requests += 1

                    if (
                        is_html
                        and report.status_code
                        and report.status_code < 400
                        and pages_crawled < self.max_pages
                        and depth < self.max_depth
                    ):
                        pages_crawled += 1
                        for normalized in discovered_links:
                            if (not self.include_external) and (
                                not self._is_same_domain(normalized)
                            ):
                                continue
                            referrer_map[normalized].add(url)
                            if (
                                normalized in visited
                                or normalized in in_progress
                                or normalized in queued_urls
                            ):
                                continue
                            queue.append((normalized, depth + 1))
                            queued_urls.add(normalized)

                    if completed_requests >= self.max_requests:
                        break

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
        links_found: List[str] = []
        content_type: Optional[str] = None
        redirected_to: Optional[str] = None
        status_code: Optional[int] = None
        status = "unknown"
        is_html = False

        try:
            response = self._request_with_retries(url)
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
                normalized_links = self._extract_links(url, response.text)
                discovered_links = normalized_links
                links_found = sorted(normalized_links)
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
            links_found=links_found,
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

    def _extract_links(self, base_url: str, html: str) -> Set[str]:
        soup = BeautifulSoup(html, "lxml")
        urls: Set[str] = set()
        tag_attrs = {
            "a": "href",
            "link": "href",
            "img": "src",
            "script": "src",
            "iframe": "src",
            "source": "src",
        }
        for tag, attr in tag_attrs.items():
            for node in soup.find_all(tag):
                value = node.get(attr)
                if not value:
                    continue
                normalized = self._normalize_link(base_url, value)
                if normalized:
                    urls.add(normalized)
        return urls

    def _throttle(self) -> None:
        if not self.rate_limit:
            return
        with self._rate_lock:
            now = time.monotonic()
            wait_time = self._min_interval - (now - self._last_request_time)
            if wait_time > 0:
                time.sleep(wait_time)
            self._last_request_time = time.monotonic()

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self.backoff_factor * (2**attempt)
        if delay > 0:
            time.sleep(delay)

    def _request_with_retries(self, url: str) -> requests.Response:
        attempt = 0
        while True:
            self._throttle()
            try:
                response = self._session.get(
                    url, timeout=self.timeout, allow_redirects=True
                )
            except requests.RequestException:
                if attempt >= self.max_retries:
                    raise
                self._sleep_backoff(attempt)
                attempt += 1
                continue

            if response.status_code in self.retry_statuses and attempt < self.max_retries:
                self._sleep_backoff(attempt)
                attempt += 1
                continue
            return response

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


def _write_csv_report(path: str, reports: List[LinkReport]) -> None:
    path_obj = Path(path)
    if path_obj.parent and not path_obj.parent.exists():
        path_obj.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "url",
        "status",
        "status_code",
        "redirected_to",
        "referrers",
        "issues",
        "outdated_signals",
        "links_found",
    ]
    with path_obj.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for report in reports:
            writer.writerow(
                {
                    "url": report.url,
                    "status": report.status,
                    "status_code": report.status_code or "",
                    "redirected_to": report.redirected_to or "",
                    "referrers": "; ".join(report.referrers),
                    "issues": "; ".join(report.issues),
                    "outdated_signals": "; ".join(report.outdated_signals),
                    "links_found": "; ".join(report.links_found),
                }
            )


def _write_sarif_report(path: str, reports: List[LinkReport]) -> None:
    path_obj = Path(path)
    if path_obj.parent and not path_obj.parent.exists():
        path_obj.parent.mkdir(parents=True, exist_ok=True)

    rules = [
        {
            "id": "link-health.broken",
            "name": "Broken Link",
            "shortDescription": {"text": "Link returned an HTTP error or network failure"},
            "defaultConfiguration": {"level": "error"},
        },
        {
            "id": "link-health.outdated",
            "name": "Outdated Content",
            "shortDescription": {"text": "Page appears to contain stale content"},
            "defaultConfiguration": {"level": "warning"},
        },
    ]

    results = []
    for report in reports:
        if report.status in {"broken", "error", "server-error"}:
            message = "; ".join(report.issues) or f"HTTP {report.status_code}"
            results.append(
                {
                    "ruleId": "link-health.broken",
                    "level": "error",
                    "message": {"text": message},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": report.url},
                            }
                        }
                    ],
                    "properties": {
                        "status": report.status,
                        "status_code": report.status_code,
                        "redirected_to": report.redirected_to,
                    },
                }
            )
        if report.outdated_signals:
            results.append(
                {
                    "ruleId": "link-health.outdated",
                    "level": "warning",
                    "message": {"text": "; ".join(report.outdated_signals)},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": report.url},
                            }
                        }
                    ],
                }
            )

    sarif_payload = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Link Health Scanner",
                        "informationUri": "https://example.com/link-health-scanner",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }

    path_obj.write_text(json.dumps(sarif_payload, indent=2), encoding="utf-8")
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
        "--max-workers",
        type=int,
        default=5,
        help="Concurrent worker threads (default: 5)",
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
        "--rate-limit",
        type=float,
        help="Maximum requests per second (optional)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Retries for transient failures (default: 2)",
    )
    parser.add_argument(
        "--backoff-factor",
        type=float,
        default=0.5,
        help="Base seconds for exponential backoff (default: 0.5)",
    )
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="Also crawl and audit external domains (default: off)",
    )
    parser.add_argument(
        "--skip-orphans",
        action="store_true",
        help="Skip orphan/sitemap-only discovery to reduce noise",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output machine-readable JSON",
    )
    parser.add_argument(
        "--csv-out",
        help="Write a CSV report to this path",
    )
    parser.add_argument(
        "--sarif-out",
        help="Write a SARIF 2.1.0 report to this path",
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
    if not start_url.startswith(("http://", "https://")):
        start_url = "https://" + start_url

    scanner = LinkHealthScanner(
        start_url,
        include_external=args.include_external,
        check_orphans=not args.skip_orphans,
        max_pages=args.max_pages,
        max_requests=args.max_requests,
        max_depth=args.max_depth,
        timeout=args.timeout,
        outdated_days=args.outdated_days,
        max_workers=args.max_workers,
        rate_limit=args.rate_limit,
        max_retries=args.max_retries,
        backoff_factor=args.backoff_factor,
    )
    start_url = scanner.start_url
    result = scanner.run()
    reports: List[LinkReport] = result["reports"]  # type: ignore[assignment]
    summary: Dict[str, int] = result["summary"]  # type: ignore[assignment]
    unused_links: List[str] = result.get("unused_links", [])
    sitemap_only_links: List[str] = result.get("sitemap_only_links", [])

    payload = {
        "summary": summary,
        "reports": [report.to_dict() for report in reports],
        "unused_links": unused_links,
        "sitemap_only_links": sitemap_only_links,
    }

    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print("Link Health Scanner")
        print("==================")
        print(f"Total checked: {summary['total']}")
        print(f"OK: {summary['ok']}")
        print(f"Broken: {summary['broken']}")
        print(f"Server errors: {summary['server-error']}")
        print(f"Redirects: {summary['redirect']}")
        print(f"Outdated pages detected: {summary['outdated']}")
        if not args.skip_orphans:
            print(f"Unused / orphan links: {summary.get('unused', 0)}")
            print()

        def _print_section(title: str, condition):
            matching = [r for r in reports if condition(r)]
            if not matching:
                return
            print(title)
            for rep in matching:
                parts = [f"- {_shorten(rep.url)}"]
                if rep.status_code:
                    parts.append(f"(HTTP {rep.status_code})")
                if rep.referrers:
                    sources = ", ".join(_shorten(src) for src in rep.referrers[:3])
                    if len(rep.referrers) > 3:
                        sources += ", ..."
                    parts.append(f"from {sources}")
                if rep.redirected_to:
                    parts.append(f"-> {_shorten(rep.redirected_to)}")
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
        if unused_links and (not args.skip_orphans):
            print("Unused / Orphan Links")
            for url in unused_links:
                print(f"  - {_shorten(url)}")
            print()
        if sitemap_only_links and (not args.skip_orphans):
            print("Sitemap-only Links (never visited)")
            for url in sitemap_only_links:
                print(f"  - {_shorten(url)}")
            print()

        print("All Links Scanned")
        for rep in reports:
            status_desc = rep.status
            if rep.status_code:
                status_desc += f" (HTTP {rep.status_code})"
            print(f"  - {_shorten(rep.url)}")
            print(f"      Status: {status_desc}")
            if rep.redirected_to:
                print(f"      Redirects to: {_shorten(rep.redirected_to)}")
            if rep.issues:
                print(f"      Issues: {', '.join(rep.issues)}")
            if rep.links_found:
                print("      Links found:")
                for child in rep.links_found:
                    print(f"        - {_shorten(child)}")
        print()

    if args.csv_out:
        _write_csv_report(args.csv_out, reports)
    if args.sarif_out:
        _write_sarif_report(args.sarif_out, reports)

    return 0


if __name__ == "__main__":
    sys.exit(main())
