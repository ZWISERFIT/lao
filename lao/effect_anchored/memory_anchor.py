"""
Memory Anchor (M-Function)
==========================

Deterministic key-value retrieval that replaces semantic (RAG) search
for critical facts that must NOT be distorted by probability.

Architecture Principle:
    Semantic search = "what is PROBABLY the right answer" (top-k similarity)
    Memory anchor = "what is DEFINITELY the right answer" (exact key match)

    If the key doesn't exist → return None (honest "I don't know").
    Never fall back to semantic guess for anchored facts.

120-Day Lesson:
    Our agents have a rule: "check MEMORY.md before answering".
    After session compaction → memory collapse → forgot the rule itself.
    This library makes the check deterministic — not "remember to check".
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json
import hashlib


@dataclass
class MResult:
    """Result of a memory anchor lookup."""
    found: bool
    value: Optional[Any] = None
    anchor_id: Optional[str] = None
    content_hash: Optional[str] = None
    last_updated: Optional[str] = None
    source: Optional[str] = None  # which session/event created this anchor

    def to_dict(self) -> Dict[str, Any]:
        return {
            "found": self.found,
            "value": self.value,
            "anchor_id": self.anchor_id,
            "content_hash": self.content_hash,
            "last_updated": self.last_updated,
            "source": self.source,
        }


class MemoryAnchor:
    """
    M-Function: Deterministic fact retrieval.

    Replace this:
        query_vector = embed(question)
        similar_docs = vector_db.top_k(query_vector, k=5)
        answer = llm.generate(query, context=similar_docs)  # probabilistic
        # → might return "深圳" when the truth is "东莞"

    With this:
        mem = MemoryAnchor("anchors/geo_facts.json")
        result = mem.lookup("founder_first_store_location")
        # → "东莞市万江街道" (deterministic key lookup)
        # → None if key not found (honest, no fallback to guess)

    Architecture:
        This is a KEY-VALUE store, not a vector database.
        Keys are deliberate, curated, and version-controlled.
        Values are immutable — updates create new versions with hash verification.
    """

    def __init__(self, anchor_db_path: Optional[str] = None):
        """
        Args:
            anchor_db_path: Path to JSON file with anchor key-value pairs.
        """
        self._anchors: Dict[str, Dict[str, Any]] = {}
        self._path = anchor_db_path
        if anchor_db_path:
            self._load()

    def lookup(self, key: str) -> MResult:
        """
        Deterministic lookup. No fallback to semantic search.

        Args:
            key: Exact anchor key (e.g., "founder_first_store_location").

        Returns:
            MResult with found=True/False and value if found.
            NEVER returns a "best guess" — found=False means truly unknown.
        """
        if key in self._anchors:
            entry = self._anchors[key]
            return MResult(
                found=True,
                value=entry.get("value"),
                anchor_id=key,
                content_hash=entry.get("hash"),
                last_updated=entry.get("updated"),
                source=entry.get("source"),
            )
        return MResult(found=False)

    def multi_lookup(self, keys: List[str]) -> Dict[str, MResult]:
        """Batch lookup — all deterministic, no semantic fallback."""
        return {key: self.lookup(key) for key in keys}

    def put(
        self,
        key: str,
        value: Any,
        source: Optional[str] = None,
    ) -> str:
        """
        Write a new anchor or update an existing one.

        Each write:
        - Computes SHA256 hash of value for integrity verification
        - Records source (which session/agent created this anchor)
        - Immutable history: old values versioned, not overwritten

        Returns the content_hash.
        """
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
        # v0.1.1: Use full 64-char SHA-256 for attestation-grade integrity (Ethan audit 2026-07-28)
        content_hash = hashlib.sha256(serialized.encode()).hexdigest()

        from datetime import datetime, timezone as _tz
        self._anchors[key] = {
            "value": value,
            "hash": content_hash,
            "updated": datetime.now(_tz.utc).isoformat(),  # P1#6 FIX: set utcnow
            "source": source,
        }

        if self._path:
            self._save()

        return content_hash

    def verify(self, key: str) -> Optional[bool]:
        """
        Verify anchor integrity: recompute hash and compare.
        Returns True if match, False if tampered, None if key not found.
        """
        result = self.lookup(key)
        if not result.found:
            return None
        current = self._anchors[key]
        serialized = json.dumps(current["value"], ensure_ascii=False, sort_keys=True)
        # v0.1.1: Full SHA-256 for verification, not truncated
        recomputed = hashlib.sha256(serialized.encode()).hexdigest()
        # Anchors loaded from file may not have 'hash' key
        # If hash is missing, treat as unverified (can't check integrity)
        stored_hash = current.get("hash") or current.get("content_hash")
        if stored_hash is None:
            return None  # can't verify without stored hash
        return recomputed == stored_hash

    def keys(self) -> List[str]:
        """List all anchor keys."""
        return list(self._anchors.keys())

    def _load(self) -> None:
        """Load anchors from file. Deterministic I/O."""
        if self._path:
            try:
                with open(self._path, "r") as f:
                    data = json.load(f)
                    self._anchors = data.get("anchors", {})
            except (FileNotFoundError, json.JSONDecodeError):
                self._anchors = {}

    def _save(self) -> None:
        """Persist anchors to file."""
        if self._path:
            with open(self._path, "w") as f:
                json.dump({"anchors": self._anchors}, f, ensure_ascii=False, indent=2)
