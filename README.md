## Link & Route Health Checker CLI

Audit an entire site or application (marketing pages, docs, dashboards) for broken links, orphaned routes, noisy redirect chains, and stale content indicators such as old `Last-Modified` headers or outdated copyright years. Think of it as a link/route health monitor that quickly surfaces navigation gaps.

### Features

- Crawls from a starting URL, respecting a configurable depth/page/request limit so you stay in control.
- Collects `href`/`src` attributes for anchors, images, stylesheets, and scripts.
- Flags HTTP 4xx/5xx responses, network failures, and redirect chains.
- Parses pages with BeautifulSoup/lxml so even imperfect HTML yields accurate link extraction.
- Detects outdated pages via `Last-Modified` headers, old years appearing in the body, and stale phrases (`"under construction"`, `"coming soon"`, etc.).
- Spots unused/orphan internal pages and highlights `sitemap.xml` entries that never surfaced during the crawl (disable with `--skip-orphans` if you only care about crawled pages).
- Fetches concurrently with retries, exponential backoff, and optional rate limiting so large sites finish quickly without hammering servers.
- Outputs a text summary by default or full JSON for downstream tooling.

### Installation

The tool only depends on `requests`. Install the requirement in your current environment:

```bash
python -m pip install -r requirements.txt
```

### Usage

```bash
python link_health_scanner.py https://example.com \
  --max-pages 150 \
  --max-requests 500 \
  --max-depth 3 \
  --timeout 10 \
  --outdated-days 365 \
  --include-external \
  --json
```

Flags:

- `--max-pages`: Max number of HTML documents to parse (default 150).
- `--max-requests`: Safety budget for total HTTP requests (default 500).
- `--max-depth`: Crawl depth limit from the starting URL (default 3).
- `--max-workers`: Number of concurrent fetchers (default 5).
- `--timeout`: Per-request timeout in seconds (default 10).
- `--outdated-days`: Threshold for flagging old `Last-Modified` headers (default 365).
- `--rate-limit`: Maximum requests per second (optional, helps throttle traffic).
- `--max-retries`: Retries for transient errors like 429/503 (default 2).
- `--backoff-factor`: Base seconds for exponential backoff (default 0.5).
- `--include-external`: Audit off-domain links as well.
- `--json`: Emit machine-readable JSON (omit for a text summary).
- `--skip-orphans`: Skip orphan/sitemap-only discovery if you only care about crawled pages.
- `--csv-out`: Write a CSV file containing every visited route and its diagnostics.
- `--sarif-out`: Emit a SARIF 2.1.0 file for CI/code-scanning platforms.

If you omit the URL argument, the CLI will prompt you for one interactively after launch.

### Output

- Summaries list how many URLs were OK, broken, redirected, failed, outdated, and unused (orphans + sitemap-only). Use `--skip-orphans` to omit the extra count.
- Sections detail each broken link, redirect, server error, orphan page, sitemap-only entry, and page with outdated signals (including the referrers, HTTP status, and diagnostic notes).
- JSON output exposes the same data plus dedicated `unused_links` (orphans) and `sitemap_only_links` arrays (empty if you skip the orphan scan).
- Optional CSV and SARIF exports make it easy to feed results into spreadsheets, dashboards, or CI code-scanning alerts.
- Every run ends with an “All Links Scanned” section that lists each visited route, its HTTP status, and all crawlable links discovered on that page so you can confirm coverage.

### Notes

- The crawler trims fragments, skips `mailto:`/`tel:`/`javascript:` pseudo links, and only follows HTTP/HTTPS URLs.
- Redirect-heavy sites may count as multiple requests because the entire chain is recorded.
- Use generous `--max-requests` / `--max-pages` values for larger sites, or keep them low (e.g., 50/200) for portfolios to avoid long runs.
- Combine `--max-workers` with `--rate-limit` to balance throughput and politeness; the built-in retry/backoff handles 429/5xx bursts automatically.
- Interactive mode lets you scan multiple URLs in one session; type `q` to exit when prompted for a URL.
