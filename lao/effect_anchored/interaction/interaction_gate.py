"""
Interaction Gate (L4 Layer)
============================

Human-in-the-loop confirmation gate for low-confidence H-function results.

Architecture:
    H(check) → PASS/FAIL with confidence
                 ↓
           confidence ≥ 0.95 → auto-block
           confidence < 0.95 → push to InteractionGate
                                    ↓
                              CLI/SDK/API confirmation
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, Any, List, Dict


@dataclass
class ConfirmationResult:
    """Result of an interaction gate check."""
    needs_user: bool = False
    user_confirmed: Optional[bool] = None
    auto_action: Optional[str] = None  # "blocked", "passed"
    permanent: bool = False
    anchor_created: bool = False
    overridden: bool = False
    reason: str = ""
    note: str = ""


class InteractionGate:
    """
    L4 Interaction Confirmation Layer.

    Receives H-function intercept events and determines whether to
    auto-block (high confidence) or push to user confirmation.

    Modes:
        sdk: Non-blocking, returns ConfirmationResult for programmatic use
        cli: Interactive terminal prompt
        api: Webhook-based external confirmation
    """

    def __init__(self, mode: str = "sdk", ui_callback: Optional[Callable] = None):
        """
        Args:
            mode: Confirmation mode — "sdk", "cli", or "api"
            ui_callback: Custom confirmation callback for SDK mode
        """
        self.mode = mode
        self.ui_callback = ui_callback or self._default_cli_prompt
        self._pending: List[Dict[str, Any]] = []

    def check(self, intercept_event: Any, confidence: float = 0.0) -> ConfirmationResult:
        """
        Determine whether to auto-block or push to user confirmation.

        Args:
            intercept_event: The HResult or other intercept event result
            confidence: Fallback confidence score (overridden by event.confidence)

        Returns:
            ConfirmationResult with auto-action or user-confirmation request
        """
        # Extract confidence from event if available
        if hasattr(intercept_event, 'confidence'):
            confidence = intercept_event.confidence

        # High confidence → automatic handling
        if confidence >= 0.95:
            return ConfirmationResult(
                needs_user=False,
                auto_action="blocked",
                reason=f"高置信度自动拦截 (confidence={confidence})",
            )

        # Low confidence → route by mode
        if self.mode == "sdk":
            return ConfirmationResult(
                needs_user=True,
                reason=f"低置信度({confidence})需用户确认",
            )
        elif self.mode == "cli":
            return self._default_cli_prompt(intercept_event)
        elif self.mode == "api":
            return ConfirmationResult(
                needs_user=True,
                reason=f"低置信度({confidence})需用户确认",
            )

        # Fallback
        return ConfirmationResult(
            needs_user=True,
            reason=f"未知模式({self.mode})，默认需要用户确认",
        )

    def confirm(self, result_id: str, user_decision: str) -> ConfirmationResult:
        """
        Process a user confirmation decision.

        Args:
            result_id: Identifier for the pending confirmation
            user_decision: "confirm_block" or "confirm_pass"

        Returns:
            ConfirmationResult reflecting the user's decision
        """
        return ConfirmationResult(
            needs_user=True,
            user_confirmed=(user_decision == "confirm_block"),
            permanent=True,
            anchor_created=True,
        )

    def _default_cli_prompt(self, event: Any) -> ConfirmationResult:
        """Default CLI prompt — extracts reason from the event."""
        reason = getattr(event, 'reason', str(event))
        return ConfirmationResult(
            needs_user=True,
            reason=reason,
        )

    def get_pending(self) -> List[Dict[str, Any]]:
        """Return list of pending confirmation requests (SDK mode)."""
        return list(self._pending)
