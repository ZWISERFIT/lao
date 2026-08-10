"""lao init — bootstrap a working LAO runtime in under a minute."""
from __future__ import annotations

import os
import shutil
from typing import Optional

from .schema import TrustEventLedger, make_event


def init(workdir: str | None = None, agent: str = "demo", sample: bool = True) -> dict:
    """Create the LAO directory skeleton + config + optional sample event.

    Returns a summary dict of what was created.
    """
    base = os.path.abspath(workdir or os.getcwd())
    created = []

    dirs = ["live/trust-events", "live/anchors", "live/events"]
    for d in dirs:
        p = os.path.join(base, d)
        os.makedirs(p, exist_ok=True)
        created.append(p)

    config_path = os.path.join(base, "lao.json")
    if not os.path.exists(config_path):
        import json
        with open(config_path, "w") as f:
            json.dump({
                "version": "0.1.0",
                "agent": agent,
                "ledger": "live/trust-events/ledger-v1.json",
                "anchors_dir": "live/anchors",
                "events_dir": "live/events",
            }, f, ensure_ascii=False, indent=2)
        created.append(config_path)

    ledger = TrustEventLedger(os.path.join(base, DEFAULT_LEDGER_REL))
    result = {"dirs": created, "ledger": ledger.path, "events": 0}

    if sample:
        ev = make_event(
            agent=agent,
            etype="success",
            description=f"LAO initialized for agent '{agent}' — first verified trust event.",
            evidence=f"lao init run in {base}",
            repair="",
            ledger=ledger,
        )
        result["events"] = 1
        result["sample_event"] = ev.event_id
        created.append(ledger.path)

    return result


DEFAULT_LEDGER_REL = "live/trust-events/ledger-v1.json"
