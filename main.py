#!/usr/bin/env python3
"""
Main entry point for Link Health Scanner with clean CLI interface.
"""

import sys
import json
import argparse
from typing import List

from link_health_scanner import LinkHealthScanner, LinkReport
import cli_ui


def _truncate(text: str, limit: int = 90) -> str:
    """Trim long URLs/routes for display."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def run_interactive_mode():
    """Run the scanner in interactive mode with clean UI."""
    while True:
        cli_ui.print_header()
        url = cli_ui.get_url_input()
        if not url:
            print(cli_ui.center_text("Please enter a URL or type 'q' to quit."))
            continue
        if url.lower() == "q":
            print(cli_ui.center_text("Goodbye!"))
            return 0

        options = cli_ui.get_scan_options()

        cli_ui.display_scanning_message()

        try:
            scanner = LinkHealthScanner(
                url,
                include_external=options['include_external'],
                check_orphans=options['check_orphans'],
                max_pages=options['max_pages'],
                max_depth=options['max_depth'],
                timeout=options['timeout'],
            )

            result = scanner.run()
            reports: List[LinkReport] = result["reports"]
            summary = result["summary"]
            unused_links = result.get("unused_links", [])
            sitemap_only_links = result.get("sitemap_only_links", [])

            # Display results
            cli_ui.display_results_header()
            cli_ui.display_summary(summary, show_unused=options['check_orphans'])

            # Always show detailed results for clarity
            display_detailed_results(reports, options, unused_links, sitemap_only_links)

        except Exception as e:
            print(cli_ui.center_text(f"Error: {str(e)}"))
            return 1

        if not cli_ui.prompt_run_again():
            break

    print(cli_ui.center_text("Goodbye!"))
    return 0


def display_detailed_results(
    reports: List[LinkReport],
    options: dict,
    unused_links: List[str],
    sitemap_only_links: List[str],
):
    """Display detailed results based on user preferences."""
    width = cli_ui.get_terminal_width()
    padding = (width - 60) // 2
    indent = " " * padding

    if options['check_broken']:
        broken = [r for r in reports if r.status in {'broken', 'error'}]
        if broken:
            print("\n" + cli_ui.center_text("── Broken Links ──"))
            for rep in broken:
                print(f"{indent}URL: {_truncate(rep.url)}")
                if rep.referrers:
                    sources = ", ".join(_truncate(src) for src in rep.referrers[:3])
                    if len(rep.referrers) > 3:
                        sources += ", ..."
                    print(f"{indent}  Found on: {sources}")
                if rep.issues:
                    print(f"{indent}  Issue: {', '.join(rep.issues)}")

    if options['check_redirects']:
        redirects = [r for r in reports if r.redirected_to]
        if redirects:
            print("\n" + cli_ui.center_text("── Redirects ──"))
            for rep in redirects:
                print(f"{indent}URL: {_truncate(rep.url)}")
                print(f"{indent}  Redirects to: {_truncate(rep.redirected_to or '')}")

    if options['check_outdated']:
        outdated = [r for r in reports if r.outdated_signals]
        if outdated:
            print("\n" + cli_ui.center_text("── Potentially Outdated ──"))
            for rep in outdated:
                print(f"{indent}URL: {_truncate(rep.url)}")
                for signal in rep.outdated_signals:
                    print(f"{indent}  Signal: {signal}")

    if options['check_orphans'] and unused_links:
        print("\n" + cli_ui.center_text("── Orphan Links (no internal references) ──"))
        for url in unused_links:
            print(f"{indent}{_truncate(url)}")

    if options['check_orphans'] and sitemap_only_links:
        print("\n" + cli_ui.center_text("── Sitemap-only Links (never visited) ──"))
        for url in sitemap_only_links:
            print(f"{indent}{_truncate(url)}")

    # Always list every scanned link with status
    print("\n" + cli_ui.center_text("── All Links Scanned ──"))
    for rep in reports:
        status = rep.status
        if rep.status_code:
            status += f" (HTTP {rep.status_code})"
        print(f"{indent}{_truncate(rep.url)}")
        print(f"{indent}  Status: {status}")
        if rep.redirected_to:
            print(f"{indent}  Redirects to: {_truncate(rep.redirected_to)}")
        if rep.issues:
            print(f"{indent}  Issues: {', '.join(rep.issues)}")
        if rep.links_found:
            print(f"{indent}  Links found:")
            for child in rep.links_found:
                print(f"{indent}    - {_truncate(child)}")

    print("\n")


def print_cli_sections(
    reports: List[LinkReport],
    unused_links: List[str],
    sitemap_only_links: List[str],
    *,
    show_orphans: bool,
):
    """Print detailed sections for CLI mode without interactive UI."""

    def _print_section(title: str, predicate):
        items = [r for r in reports if predicate(r)]
        if not items:
            return
        print(f"\n{title}")
        for rep in items:
            print(f"- URL: {_truncate(rep.url)}")
            if rep.status_code:
                print(f"  Status: HTTP {rep.status_code}")
            if rep.referrers:
                sources = ", ".join(_truncate(src) for src in rep.referrers[:3])
                if len(rep.referrers) > 3:
                    sources += ", ..."
                print(f"  Found on: {sources}")
            if rep.redirected_to:
                print(f"  Redirects to: {_truncate(rep.redirected_to)}")
            if rep.issues:
                print(f"  Issues: {', '.join(rep.issues)}")
            if rep.outdated_signals:
                print(f"  Outdated signals: {', '.join(rep.outdated_signals)}")

    _print_section("Broken / Error Links", lambda r: r.status in {'broken', 'error'})
    _print_section("Server Errors", lambda r: r.status == 'server-error')
    _print_section("Redirects", lambda r: r.redirected_to is not None)
    _print_section("Outdated Content", lambda r: bool(r.outdated_signals))

    if show_orphans and unused_links:
        print("\nOrphan Links (no internal references)")
        for url in unused_links:
            print(f"- {_truncate(url)}")
    if show_orphans and sitemap_only_links:
        print("\nSitemap-only Links (never visited during crawl)")
        for url in sitemap_only_links:
            print(f"- {_truncate(url)}")

    print("\nAll Links Scanned")
    for rep in reports:
        status = rep.status
        if rep.status_code:
            status += f" (HTTP {rep.status_code})"
        print(f"- {_truncate(rep.url)}")
        print(f"  Status: {status}")
        if rep.redirected_to:
            print(f"  Redirects to: {_truncate(rep.redirected_to)}")
        if rep.issues:
            print(f"  Issues: {', '.join(rep.issues)}")
        if rep.links_found:
            print("  Links found:")
            for child in rep.links_found:
                print(f"    - {_truncate(child)}")


def run_cli_mode(args):
    """Run the scanner in CLI mode with arguments."""
    scanner = LinkHealthScanner(
        args.url,
        include_external=args.include_external,
        check_orphans=not args.skip_orphans,
        max_pages=args.max_pages,
        max_requests=args.max_requests,
        max_depth=args.max_depth,
        timeout=args.timeout,
        outdated_days=args.outdated_days,
    )

    result = scanner.run()
    reports = result["reports"]
    summary = result["summary"]
    unused_links = result.get("unused_links", [])
    sitemap_only_links = result.get("sitemap_only_links", [])

    if args.json:
        payload = {
            "summary": summary,
            "reports": [report.to_dict() for report in reports],
            "unused_links": unused_links,
            "sitemap_only_links": sitemap_only_links,
        }
        print(json.dumps(payload, indent=2))
    else:
        # Simple text output for CLI mode
        print("\nLink Health Scanner Results")
        print("=" * 40)
        print(f"Total: {summary['total']}")
        print(f"OK: {summary['ok']}")
        print(f"Broken: {summary['broken']}")
        print(f"Errors: {summary.get('error', 0)}")
        print(f"Redirects: {summary['redirect']}")
        print(f"Outdated: {summary.get('outdated', 0)}")
        if not args.skip_orphans:
            print(f"Unused: {summary.get('unused', 0)}")
        print_cli_sections(
            reports,
            unused_links,
            sitemap_only_links,
            show_orphans=not args.skip_orphans,
        )

    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Link Health Scanner - Check your website for broken links and issues",
        add_help=False,  # We'll handle help ourselves for cleaner output
    )

    parser.add_argument("url", nargs="?", help="URL to scan")
    parser.add_argument("-h", "--help", action="store_true", help="Show help")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--max-pages", type=int, default=150)
    parser.add_argument("--max-requests", type=int, default=500)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--outdated-days", type=int, default=365)
    parser.add_argument("--include-external", action="store_true")
    parser.add_argument("--skip-orphans", action="store_true")

    args = parser.parse_args()

    if args.help:
        parser.print_help()
        return 0

    # If URL is provided, run in CLI mode
    if args.url:
        if not args.url.startswith(("http://", "https://")):
            args.url = "https://" + args.url
        return run_cli_mode(args)

    # Otherwise, run interactive mode
    return run_interactive_mode()


if __name__ == "__main__":
    sys.exit(main())
