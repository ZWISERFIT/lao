"""
L4 Interaction Confirmation Layer
=================================

Human-in-the-loop confirmation gate for low-confidence H-function results.

Backends:
    CLI: Interactive terminal prompt
    SDK: Non-blocking pending queue
    API: Webhook-based external confirmation
"""

__all__ = [
    "InteractionGate",
    "ConfirmationResult",
    "CLIBackend",
    "SDKBackend",
    "APIBackend",
]

from .interaction_gate import InteractionGate, ConfirmationResult
from .ui_backends import CLIBackend, SDKBackend, APIBackend
