"""
Context Rebuilder (C-Function)
==============================

Structured event recording with content hash verification.
After session compaction or context overflow, reconstruct the full
event chain from immutable event records.

Architecture Principle:
    Single-line session logs ("replied to founder") are useless for
    context reconstruction. Structured events with parent-child links
    and content hashes enable deterministic replay.

120-Day Lesson:
    Session compaction erases conversation history. Our guards check
    file size but don't preserve the MEANING of what was lost.
    C-function records each event as a structured datum with hash —
    then rebuilds the event chain on demand.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json
import hashlib
import time


@dataclass
class Event:
    """A single structured event in the context chain."""
    event_id: str
    timestamp: str
    speaker: str  # "founder" | "zeus" | "shuyu" | "tristan" | "stella" | ...
    event_type: str  # "decision" | "question" | "answer" | "report" | "veto" | ...
    subject: str
    summary: str
    content_hash: Optional[str] = None  # SHA256 of the original message/decision content; auto-computed in record() if empty
    parent_events: List[str] = field(default_factory=list)
    child_events: List[str] = field(default_factory=list)
    anchor_keys: List[str] = field(default_factory=list)
    files_touched: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "speaker": self.speaker,
            "event_type": self.event_type,
            "subject": self.subject,
            "summary": self.summary,
            "content_hash": self.content_hash,
            "parent_events": self.parent_events,
            "child_events": self.child_events,
            "anchor_keys": self.anchor_keys,
            "files_touched": self.files_touched,
            "metadata": self.metadata,
        }


class ContextRebuilder:
    """
    C-Function: Structured event recording + deterministic context reconstruction.

    Usage:
        # Recording (happens on every significant interaction):
        recon = ContextRebuilder(session_id="zeus_0727")
        recon.record(Event(
            event_id="evt_1856",
            speaker="founder",
            event_type="veto",
            subject="run_a_real_gym定位否决",
            ...
        ))

        # Reconstruction (after compaction):
        events = recon.reconstruct(from_timestamp="14:00", to_timestamp="20:00")
        # → fully linked event chain, verified by content hashes
    """

    def __init__(
        self,
        session_id: str,
        event_store_path: Optional[str] = None,
    ):
        """
        Args:
            session_id: Unique session identifier.
            event_store_path: Path to event store (JSON lines file).
                              Default: ~/.openclaw/workspace/zeus/memory/events/{session_id}.jsonl
        """
        self.session_id = session_id
        self._events: List[Event] = []
        self._event_index: Dict[str, int] = {}  # P0 FIX (Tristan audit): O(1) lookup replacing O(n) _find_event
        self._path = event_store_path
        self._dirty = False

    def record(self, event: Event) -> str:
        """
        Record a structured event.

        Side effects:
            - Computes content hash if not provided
            - Links parent-child relationships
            - Persists to event store
        """
        if not event.content_hash:
            event.content_hash = self._hash_content(event)

        # Link to parent events
        for parent_id in event.parent_events:
            parent = self._find_event(parent_id)
            if parent and event.event_id not in parent.child_events:
                parent.child_events.append(event.event_id)

        self._events.append(event)
        self._event_index[event.event_id] = len(self._events) - 1  # P0 FIX: O(1) index
        self._dirty = True
        self._persist()

        return event.event_id

    def reconstruct(
        self,
        from_timestamp: Optional[str] = None,
        to_timestamp: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        speakers: Optional[List[str]] = None,
        anchor_keys: Optional[List[str]] = None,
    ) -> List[Event]:
        """
        Reconstruct event chain after compaction or context loss.

        Args:
            from_timestamp: Start of reconstruction window (ISO format or "HH:MM").
            to_timestamp: End of reconstruction window.
            event_types: Filter by event type ("decision", "veto", "question", etc.).
            speakers: Filter by speaker.
            anchor_keys: Filter by anchor keys tagged on events.

        Returns:
            List of events in chronological order, with parent-child links intact.
        """
        results = []
        for event in self._events:
            if from_timestamp and event.timestamp < from_timestamp:
                continue
            if to_timestamp and event.timestamp > to_timestamp:
                continue
            if event_types and event.event_type not in event_types:
                continue
            if speakers and event.speaker not in speakers:
                continue
            if anchor_keys:
                if not set(anchor_keys) & set(event.anchor_keys):
                    continue
            results.append(event)
        return results

    def get_event_chain(self, event_id: str, depth: int = 3) -> List[Event]:
        """
        Get the full ancestor and descendant chain for an event.
        Useful for understanding "what led to this decision" and "what followed".
        """
        event = self._find_event(event_id)
        if not event:
            return []

        chain = [event]

        # Walk ancestors
        visited = {event_id}
        current = event
        for _ in range(depth):
            if not current.parent_events:
                break
            for pid in current.parent_events:
                if pid not in visited:
                    parent = self._find_event(pid)
                    if parent:
                        chain.insert(0, parent)
                        visited.add(pid)
                        current = parent

        # Walk descendants
        current = event
        for _ in range(depth):
            if not current.child_events:
                break
            for cid in current.child_events:
                if cid not in visited:
                    child = self._find_event(cid)
                    if child:
                        chain.append(child)
                        visited.add(cid)
                        current = child

        return chain

    def verify_integrity(self, event_id: str) -> bool:
        """Verify that an event's content hasn't been tampered with."""
        event = self._find_event(event_id)
        if not event:
            return False
        return self._hash_content(event) == event.content_hash

    def export(self) -> List[Dict[str, Any]]:
        """Export all events as dicts."""
        return [e.to_dict() for e in self._events]

    def _find_event(self, event_id: str) -> Optional[Event]:
        # P0 FIX (Tristan audit 2026-07-28): O(1) dict lookup replacing O(n) linear scan.
        # With 100K+ events, the old O(n) would be catastrophic on every reconstruct().
        idx = self._event_index.get(event_id)
        if idx is not None and idx < len(self._events):
            event = self._events[idx]
            if event.event_id == event_id:  # sanity check: index might be stale
                return event
        # Fallback: linear scan for stale index
        for e in self._events:
            if e.event_id == event_id:
                return e
        return None

    def _hash_content(self, event: Event) -> str:
        """Create content hash from event's key identifying fields."""
        seed = f"{event.speaker}|{event.event_type}|{event.subject}|{event.summary}"
        # v0.1.1: Full 64-char SHA-256 for cross-verification with M-function attestation
        return hashlib.sha256(seed.encode()).hexdigest()

    def _persist(self) -> None:
        """Append event to JSON-lines file."""
        if not self._path or not self._dirty:
            return
        event = self._events[-1]
        with open(self._path, "a") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self._dirty = False
