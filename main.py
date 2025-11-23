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


def run_interactive_mode():
    """Run the scanner in interactive mode with clean UI."""
    # Display header
    cli_ui.print_header()

    # Get URL from user
    url = cli_ui.get_url_input()
    if not url:
        print(cli_ui.center_text("No URL provided. Exiting."))
        return 1

    # Get scan options
    options = cli_ui.get_scan_options()

    # Display scanning message
    cli_ui.display_scanning_message()

    # Run the scanner
    try:
        scanner = LinkHealthScanner(
            url,
            include_external=options['include_external'],
            max_pages=options['max_pages'],
            max_depth=options['max_depth'],
            timeout=options['timeout'],
        )

        result = scanner.run()
        reports: List[LinkReport] = result["reports"]
        summary = result["summary"]
        unused_links = result.get("unused_links", [])

        # Display results
        cli_ui.display_results_header()
        cli_ui.display_summary(summary)

        # Always show detailed results for clarity
        display_detailed_results(reports, options, unused_links)

    except Exception as e:
        print(cli_ui.center_text(f"Error: {str(e)}"))
        return 1

    return 0


def display_detailed_results(
    reports: List[LinkReport], options: dict, unused_links: List[str]
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
                print(f"{indent}✗ {rep.url}")
                if rep.referrers:
                    sources = ", ".join(rep.referrers[:3])
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
                print(f"{indent}↻ {rep.url}")
                print(f"{indent}  → {rep.redirected_to}")

    if options['check_outdated']:
        outdated = [r for r in reports if r.outdated_signals]
        if outdated:
            print("\n" + cli_ui.center_text("── Potentially Outdated ──"))
            for rep in outdated:
                print(f"{indent}⌚ {rep.url}")
                for signal in rep.outdated_signals:
                    print(f"{indent}  • {signal}")

    if unused_links:
        print("\n" + cli_ui.center_text("── Unused / Orphan Links ──"))
        for url in unused_links:
            print(f"{indent}Ø {url}")

    print("\n")


def run_cli_mode(args):
    """Run the scanner in CLI mode with arguments."""
    scanner = LinkHealthScanner(
        args.url,
        include_external=args.include_external,
        max_pages=args.max_pages,
        max_requests=args.max_requests,
        max_depth=args.max_depth,
        timeout=args.timeout,
        outdated_days=args.outdated_days,
    )

    result = scanner.run()
    reports = result["reports"]
    summary = result["summary"]

    if args.json:
        payload = {
            "summary": summary,
            "reports": [report.to_dict() for report in reports],
            "unused_links": result.get("unused_links", []),
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
        print(f"Unused: {summary.get('unused', 0)}")

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

    args = parser.parse_args()

    if args.help:
        parser.print_help()
        return 0

    # If URL is provided, run in CLI mode
    if args.url:
        return run_cli_mode(args)

    # Otherwise, run interactive mode
    return run_interactive_mode()


if __name__ == "__main__":
    sys.exit(main())
