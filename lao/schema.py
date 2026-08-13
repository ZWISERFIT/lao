"""LAO Trust Event & VerifyPing data models (schema).

Reuses the proven trust-events/ledger-v1.json structure so data is
backward-compatible with existing deployments.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


# Default paths are overridable via env for embedding in other runtimes.
DEFAULT_LEDGER = "live/trust-events/ledger-v1.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class TrustEvent:
    """A single externally-verifiable trust event.

    v3.3 Phase1 Step2 (创始人终审 P0-2): TrustEvent = 全系统唯一事件骨架。
    所有重要事件都是 TrustEvent subtype, 通过 `subtype` 字段区分, 消灭多账本:

        subtype ∈ { DecisionEvent, ContextEvent, RuntimeEvent,
                    RecoveryEvent, ExperienceEvent, OwnershipEvent }
        domain   ∈ { ui, gateway, network, session, agent, model,
                     provider, tool, context, ... }  (FailureDomain 分组用·P0-9)

    Mirrors the recorded ledger structure (E-SHUYU / E-LUNA / S-LAO series).
    """
    event_id: str
    date: str
    type: str                # "success" | "failure" | "decision" | "context" | ...
    failure: str             # description (for failure) / result (success)
    evidence: str = ""       # reproducible evidence
    detection: str = ""
    repair: str = ""
    new_anchor: list = field(default_factory=list)
    impact: str = ""
    status: str = "OPEN"     # "OPEN" | "CLOSED"
    future_prevention: str = ""
    agent: str = ""          # producing agent id
    subtype: str = "RuntimeEvent"   # v3.3: 唯一骨架之事件子类(六类)
    domain: str = ""               # v3.3: FailureDomain 分组(ui/gateway/network/...)
    # VerifyPing proof fields (added at verify time)
    hash: str = ""
    verified: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "TrustEvent":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return asdict(self)

    def compute_hash(self) -> str:
        """Hash the non-meta fields so the event is tamper-evident."""
        payload = {
            "event_id": self.event_id,
            "date": self.date,
            "type": self.type,
            "failure": self.failure,
            "evidence": self.evidence,
            "repair": self.repair,
            "new_anchor": self.new_anchor,
            "future_prevention": self.future_prevention,
        }
        return _sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))

    def verify(self) -> bool:
        """Recompute hash and compare. True if unchanged / already verified."""
        expected = self.compute_hash()
        if self.hash:
            return _sha256(self.hash) == _sha256(expected) or self.hash == expected
        return True


class TrustEventLedger:
    """Append-only ledger for Trust Events with dedup + VerifyPing hashes."""

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("LAO_LEDGER", DEFAULT_LEDGER)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def load(self) -> list[TrustEvent]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path) as f:
                raw = json.load(f)
            events = raw.get("events", []) if isinstance(raw, dict) else raw
            return [TrustEvent.from_dict(e) for e in events]
        except (json.JSONDecodeError, OSError, TypeError):
            return []

    def append(self, event: TrustEvent) -> TrustEvent:
        event.hash = event.compute_hash()
        event.verified = True
        events = self.load()
        # dedup by event_id
        events = [e for e in events if e.event_id != event.event_id]
        events.append(event)
        with open(self.path, "w") as f:
            json.dump({"schema_version": 1, "events": [e.to_dict() for e in events]},
                      f, ensure_ascii=False, indent=2)
        return event

    def count_verified(self) -> int:
        return sum(1 for e in self.load() if e.verified)

    @staticmethod
    def next_event_id(agent: str, events: list[TrustEvent]) -> str:
        prefix = "E-" if True else "S-"
        existing = [e for e in events if e.agent == agent]
        n = len(existing) + 1
        return f"E-{agent.upper()}-{n:03d}"


def make_event(
    agent: str,
    etype: str,
    description: str,
    evidence: str = "",
    repair: str = "",
    new_anchor: Optional[list] = None,
    future_prevention: str = "",
    ledger: TrustEventLedger | None = None,
    subtype: str = "RuntimeEvent",   # v3.3: 唯一骨架之事件子类(六类)
    domain: str = "",               # v3.3: FailureDomain 分组
) -> TrustEvent:
    """Convenience factory that dedups event ids against an existing ledger."""
    lg = ledger or TrustEventLedger()
    events = lg.load()
    eid = TrustEventLedger.next_event_id(agent, events)
    ev = TrustEvent(
        event_id=eid,
        date=_utc_now(),
        type=etype,
        failure=description,
        evidence=evidence,
        repair=repair,
        new_anchor=new_anchor or [],
        status="CLOSED" if repair else "OPEN",
        future_prevention=future_prevention,
        agent=agent,
        subtype=subtype,   # v3.3 唯一骨架
        domain=domain,     # v3.3 FailureDomain
    )
    return lg.append(ev)
