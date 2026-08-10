"""
UI Backends for L4 Interaction Confirmation
===========================================

Three backends for user confirmation:
    CLI: Interactive terminal prompt
    SDK: Non-blocking pending queue for programmatic use
    API: Webhook-based external confirmation
"""

from typing import List, Dict, Optional, Any


class CLIBackend:
    """Interactive terminal-based confirmation backend."""

    def prompt(self, message: str, options: List[str]) -> str:
        """
        Display a prompt and return the user's choice.

        Args:
            message: The confirmation message to display
            options: List of option strings to present

        Returns:
            The selected option string
        """
        print(f"\n⚠️ LAO: {message}")
        for i, opt in enumerate(options, 1):
            print(f"  [{i}] {opt}")
        choice = input("  选择: ").strip()
        try:
            return options[int(choice) - 1]
        except (ValueError, IndexError):
            return options[0]


class SDKBackend:
    """Non-blocking SDK backend — collects pending prompts for programmatic retrieval."""

    def __init__(self):
        self.pending: List[Dict[str, Any]] = []

    def prompt(self, message: str, options: List[str]) -> Optional[str]:
        """
        Queue a prompt for later retrieval. Non-blocking.

        Args:
            message: The confirmation message
            options: List of option strings

        Returns:
            None (non-blocking — use get_pending() to retrieve)
        """
        self.pending.append({"message": message, "options": options})
        return None

    def get_pending(self) -> List[Dict[str, Any]]:
        """Return all pending prompts."""
        return list(self.pending)

    def clear(self) -> None:
        """Clear all pending prompts."""
        self.pending.clear()


class APIBackend:
    """Webhook-based external confirmation backend."""

    def __init__(self, webhook_url: Optional[str] = None):
        """
        Args:
            webhook_url: URL to POST confirmation requests to
        """
        self.webhook_url = webhook_url

    def prompt(self, message: str, options: List[str]) -> Dict[str, Any]:
        """
        Return a webhook call specification for external confirmation.

        Args:
            message: The confirmation message
            options: List of option strings

        Returns:
            Dict with webhook URL, message, and options for external dispatch
        """
        return {
            "webhook": self.webhook_url,
            "message": message,
            "options": options,
        }
