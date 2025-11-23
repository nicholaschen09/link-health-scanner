"""
CLI User Interface module for Link Health Scanner.
Provides a clean, simple interface for user interaction.
"""

import os
import sys
import termios
import tty
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
    """Get scan options from user with interactive selection."""
    # Default options - all enabled except external links
    options = [
        {'name': 'Check for broken links', 'key': 'check_broken', 'value': True},
        {'name': 'Check for redirects', 'key': 'check_redirects', 'value': True},
        {'name': 'Check for outdated content', 'key': 'check_outdated', 'value': True},
        {'name': 'Include external links', 'key': 'include_external', 'value': False},
    ]

    # Fixed numeric options
    fixed_options = {
        'max_pages': 150,
        'max_depth': 3,
        'timeout': 10
    }

    current_index = 0

    def display_menu(selected_idx):
        """Display the full menu screen."""
        clear_screen()
        print("\n" * 2)
        print(center_text("Scan Options"))
        print(center_text("(Use arrow keys to navigate, space to toggle, Enter to continue)"))
        print("\n")

        width = get_terminal_width()
        padding = (width - 50) // 2

        for i, opt in enumerate(options):
            indicator = "✓" if opt['value'] else "✗"
            if i == selected_idx:
                # Highlight current selection
                print(" " * padding + f"▶ [{indicator}] {opt['name']}")
            else:
                print(" " * padding + f"  [{indicator}] {opt['name']}")

        print()
        print(" " * padding + f"  Max pages: {fixed_options['max_pages']}")
        print(" " * padding + f"  Max depth: {fixed_options['max_depth']}")
        print(" " * padding + f"  Timeout: {fixed_options['timeout']}s")

    def get_key():
        """Get a single keypress from user."""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            key = sys.stdin.read(1)
            # Handle arrow keys (they come as escape sequences)
            if key == '\x1b':
                key += sys.stdin.read(2)
            return key
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    # Interactive loop
    while True:
        display_menu(current_index)
        key = get_key()

        if key == '\r' or key == '\n':  # Enter key
            break
        elif key == ' ':  # Space bar to toggle
            options[current_index]['value'] = not options[current_index]['value']
        elif key == '\x1b[A':  # Up arrow
            current_index = max(0, current_index - 1)
        elif key == '\x1b[B':  # Down arrow
            current_index = min(len(options) - 1, current_index + 1)
        elif key == '\x03':  # Ctrl+C
            raise KeyboardInterrupt

    clear_screen()
    print_header()  # Redisplay header after options selection

    # Convert to final options dict
    result = {opt['key']: opt['value'] for opt in options}
    result.update(fixed_options)

    return result


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

    if summary.get('unused', 0) > 0:
        print(" " * padding + f"Ø Unused Links: {summary['unused']}")

    print("\n")
