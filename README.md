## Broken Link Auditor CLI

Audit a site (portfolio, marketing page, documentation, etc.) for broken links, server errors, noisy redirect chains, and stale content indicators such as old `Last-Modified` headers or outdated copyright years.

### Features

- Crawls from a starting URL, respecting a configurable depth/page/request limit so you stay in control.
- Collects `href`/`src` attributes for anchors, images, stylesheets, and scripts.
- Flags HTTP 4xx/5xx responses, network failures, and redirect chains.
- Detects outdated pages via `Last-Modified` headers, old years appearing in the body, and stale phrases (`"under construction"`, `"coming soon"`, etc.).
- Outputs a text summary by default or full JSON for downstream tooling.

### Installation

The tool only depends on `requests`. Install the requirement in your current environment:

```bash
python -m pip install -r requirements.txt
```

### Usage

```bash
python broken_link_auditor.py https://example.com \
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
- `--timeout`: Per-request timeout in seconds (default 10).
- `--outdated-days`: Threshold for flagging old `Last-Modified` headers (default 365).
- `--include-external`: Audit off-domain links as well.
- `--json`: Emit machine-readable JSON (omit for a text summary).

### Output

- Summaries list how many URLs were OK, broken, redirected, or failed, plus outdated pages.
- Sections detail each broken link, redirect, server error, and page with outdated signals (including the referrer, HTTP status, and diagnostic notes).
- JSON output exposes the same data for integration with CI or dashboards.

### Notes

- The crawler trims fragments, skips `mailto:`/`tel:`/`javascript:` pseudo links, and only follows HTTP/HTTPS URLs.
- Redirect-heavy sites may count as multiple requests because the entire chain is recorded.
- Use generous `--max-requests` / `--max-pages` values for larger sites, or keep them low (e.g., 50/200) for portfolios to avoid long runs.
