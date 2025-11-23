"""
CLI User Interface module for Link Health Scanner.
Provides a clean, simple interface for user interaction.
"""

import os
from typing import Dict, Any


def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def get_terminal_width() -> int:
    """Get terminal width for centering content."""
    try:
        columns = os.get_terminal_size().columns
        return columns
    except:
        return 80  # Default fallback


def center_text(text: str, width: int = None) -> str:
    """Center text within the given width."""
    if width is None:
        width = get_terminal_width()
    return text.center(width)


def print_header():
    """Print a clean, centered header."""
    clear_screen()
    print("\n" * 2)
    print(center_text("━" * 50))
    print(center_text("LINK HEALTH SCANNER"))
    print(center_text("━" * 50))
    print("\n")
    print(center_text("a website link checker and analyzer"))
    print("\n" * 2)


def get_url_input() -> str:
    """Get URL input from user with clean formatting."""
    width = get_terminal_width()
    print(center_text("Enter the website URL to scan:"))
    print(center_text("(include http:// or https://)"))
    print()

    # Create centered input prompt
    prompt = "URL: "
    padding = (width - len(prompt) - 40) // 2  # 40 chars for typical URL
    print(" " * padding, end="")
    url = input(prompt).strip()

    if not url.startswith(('http://', 'https://')):
        if not url:
            return ""
        # Auto-prepend https:// if missing
        url = 'https://' + url

    return url


def get_scan_options() -> Dict[str, Any]:
    """Get scan options from user with defaults."""
    print("\n" * 2)
    print(center_text("Configure Scan Options"))
    print(center_text("(Press Enter to use defaults)"))
    print("\n")

    width = get_terminal_width()
    options = {}

    def get_bool_option(prompt: str, default: bool = True) -> bool:
        """Get a boolean option with default."""
        default_text = "Y" if default else "N"
        full_prompt = f"{prompt} [{default_text}/{'n' if default else 'y'}]: "
        padding = (width - len(full_prompt)) // 2
        print(" " * padding, end="")
        response = input(full_prompt).strip().lower()

        if not response:
            return default
        return response in ['y', 'yes', 'true', '1']

    def get_int_option(prompt: str, default: int) -> int:
        """Get an integer option with default."""
        full_prompt = f"{prompt} [{default}]: "
        padding = (width - len(full_prompt)) // 2
        print(" " * padding, end="")
        response = input(full_prompt).strip()

        if not response:
            return default
        try:
            return int(response)
        except ValueError:
            return default

    print(center_text("── Check Types ──"))
    print()
    options['check_broken'] = get_bool_option("Check for broken links", True)
    options['check_redirects'] = get_bool_option("Check for redirects", True)
    options['check_outdated'] = get_bool_option("Check for outdated content", True)
    options['include_external'] = get_bool_option("Include external links", False)

    print()
    print(center_text("── Scan Limits ──"))
    print()
    options['max_pages'] = get_int_option("Max pages to scan", 150)
    options['max_depth'] = get_int_option("Max crawl depth", 3)
    options['timeout'] = get_int_option("Request timeout (seconds)", 10)

    return options


def display_scanning_message():
    """Display scanning in progress message."""
    print("\n" * 2)
    print(center_text("🔍 Scanning in progress..."))
    print(center_text("This may take a few moments"))
    print("\n")


def display_results_header():
    """Display results section header."""
    print("\n")
    print(center_text("━" * 50))
    print(center_text("SCAN RESULTS"))
    print(center_text("━" * 50))
    print("\n")


def display_summary(summary: Dict[str, int]):
    """Display scan summary with clean formatting."""
    width = get_terminal_width()

    # Calculate padding for alignment
    padding = (width - 40) // 2

    print(" " * padding + f"Total Links Scanned: {summary['total']}")
    print(" " * padding + f"✓ Healthy Links: {summary['ok']}")

    if summary['broken'] > 0:
        print(" " * padding + f"✗ Broken Links: {summary['broken']}")

    if summary['server-error'] > 0:
        print(" " * padding + f"⚠ Server Errors: {summary['server-error']}")

    if summary['redirect'] > 0:
        print(" " * padding + f"↻ Redirects: {summary['redirect']}")

    if summary.get('outdated', 0) > 0:
        print(" " * padding + f"⌚ Outdated Pages: {summary['outdated']}")

    if summary.get('error', 0) > 0:
        print(" " * padding + f"! Connection Errors: {summary['error']}")

    print("\n")


def prompt_for_detailed_view() -> bool:
    """Ask if user wants to see detailed results."""
    width = get_terminal_width()
    prompt = "View detailed results? [Y/n]: "
    padding = (width - len(prompt)) // 2
    print(" " * padding, end="")
    response = input(prompt).strip().lower()
    return response != 'n'