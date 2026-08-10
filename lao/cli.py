"""lao command-line interface.

Commands:
  lao init             Bootstrap a working LAO runtime (dirs + config + sample event)
  lao trust-event      Interactive wizard to record a Trust Event
  lao verify           Recompute + display VerifyPing hashes for the ledger
  lao status           Show counts: trust events, verified, anchors
  lao demo             Run the 6-function capability demo
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from .schema import TrustEventLedger
from .init import init as _init


def _cmd_init(args) -> int:
    r = _init(workdir=args.dir, agent=args.agent, sample=not args.no_sample)
    print("✅ LAO initialized")
    print(f"   ledger : {r['ledger']}")
    print(f"   events : {r['events']}")
    if r.get("sample_event"):
        print(f"   sample : {r['sample_event']} (verified, hashed)")
    print("\nNext:  lao trust-event   # record a real event")
    return 0


def _cmd_trust_event(args) -> int:
    ledger = TrustEventLedger()
    try:
        import json as _json
        base = args.dir or os.getcwd()
        cfg_path = os.path.join(base, "lao.json")
        agent = args.agent
        if os.path.exists(cfg_path):
            import json
            agent = json.load(open(cfg_path)).get("agent", agent)
    except Exception:
        pass

    # Non-interactive mode (from script)
    if args.desc:
        from .schema import make_event
        ev = make_event(
            agent=agent, etype=args.type, description=args.desc,
            evidence=args.evidence, repair=args.repair,
            new_anchor=args.anchor, future_prevention=args.prevention,
            ledger=ledger,
        )
        print(f"✅ Trust Event recorded: {ev.event_id}")
        print(f"   type={ev.type} status={ev.status}")
        print(f"   hash={ev.hash[:16]}… (verified)")
        return 0

    # Interactive wizard
    print("LAO Trust Event Wizard")
    print("-" * 40)
    etype = input("Type [success/failure]: ").strip() or "success"
    desc = input("Description (what happened?): ").strip()
    if not desc:
        print("❌ description required")
        return 2
    evidence = input("Evidence (reproducible?): ").strip()
    repair = input("Repair (if failure): ").strip()
    from .schema import make_event
    ev = make_event(agent=agent, etype=etype, description=desc,
                    evidence=evidence, repair=repair, ledger=ledger)
    print(f"\n✅ Trust Event recorded: {ev.event_id}")
    print(f"   hash={ev.hash[:16]}… (verified)")
    return 0


def _cmd_verify(args) -> int:
    ledger = TrustEventLedger()
    events = ledger.load()
    if not events:
        print("No trust events yet. Run:  lao trust-event")
        return 0
    verified = sum(1 for e in events if e.verify())
    print(f"Trust Events: {len(events)} | Verified: {verified}")
    for e in events:
        ok = e.verify()
        print(f"  {'✅' if ok else '❌'} {e.event_id} [{e.type}] {e.hash[:12]}…")
    return 0 if verified == len(events) else 1


def _cmd_status(args) -> int:
    ledger = TrustEventLedger()
    events = ledger.load()
    verified = sum(1 for e in events if e.verify())
    print(f"LAO Status")
    print(f"  trust events : {len(events)}")
    print(f"  verified     : {verified}")
    from .schema import DEFAULT_LEDGER
    print(f"  ledger       : {os.environ.get('LAO_LEDGER', DEFAULT_LEDGER)}")
    anchors = os.path.join(args.dir or os.getcwd(), "live", "anchors")
    na = len([f for f in os.listdir(anchors) if os.path.isfile(os.path.join(anchors, f))]) if os.path.isdir(anchors) else 0
    print(f"  anchors      : {na}")
    return 0


def _cmd_demo(args) -> int:
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import subprocess
        demo = os.path.join(os.path.dirname(__file__), "..", "demo_record.py")
        if os.path.exists(demo):
            return subprocess.call([sys.executable, demo])
    except Exception:
        pass
    print("Demo script not found next to package.")
    return 1


def _cmd_atom(args) -> int:
    from lao.effect_anchored.evolution.atom_engine import ExperienceAtomEngine
    base = args.dir or os.getcwd()
    engine = ExperienceAtomEngine(os.path.join(base, "live", "experience-atoms.json"))

    if args.event:
        import json
        try:
            ev = json.loads(args.event)
        except json.JSONDecodeError:
            print("❌ --event must be a JSON object")
            return 2
        if "event_id" not in ev:
            print("❌ event must include event_id")
            return 2
        atom = engine.ingest(ev)
        print(f"✅ Experience Atom: {atom.atom_id}")
        print(f"   pattern={atom.pattern[:40]} category={atom.category}")
        print(f"   occurrences={atom.occurrences} status={atom.status} impact={atom.impact_score}")
        if atom.anchor_id:
            print(f"   anchor={atom.anchor_id} (Future Protection ENFORCED)")
        return 0

    if args.verify:
        reports = engine.verify_future_protection()
        if not reports:
            print("No anchors yet to verify.")
            return 0
        print("Future Protection verification:")
        for r in reports:
            print(f"  ✅ {r['atom_id']} {r['pattern'][:35]} occurrences={r['occurrences']} "
                  f"future_protection={r['future_protection']}")
        return 0

    stats = engine.stats()
    print("Experience Atoms:")
    print(f"  total={stats['total']} experience={stats['experience']} "
          f"anchor_candidate={stats['anchor_candidate']} anchor={stats['anchor']}")
    atoms = engine.load_atoms()
    for a in atoms:
        print(f"  {a.atom_id} [{a.category}] {a.pattern[:40]} occ={a.occurrences} "
              f"status={a.status}{' ★'+a.anchor_id if a.anchor_id else ''}")
    return 0


def _cmd_firewall(args) -> int:
    from lao.effect_anchored.preference_firewall import PreferenceFirewall
    fw = PreferenceFirewall()
    if args.request:
        r = fw.check(args.request)
        print(f"Ver‏dict: {r.verdict.value.upper()}")
        print(f"Allowed : {r.allowed}")
        print(f"Reason  : {r.reason}")
        if r.matched_dimension:
            print(f"Dim     : {r.matched_dimension}")
        if r.verdict.value == "block":
            te = fw.block_to_trust_event(r, args.agent)
            print(f"TrustEvent → E-{args.agent.upper()}-PF (blocked before damage)")
        return 0
    print("Preference Firewall — deterministic guard (Optimization ≠ Replacement)")
    print("  ALLOW : efficiency (token/workflow/context)")
    print("  BLOCK : value/identity/expression change")
    print("  REVIEW: ambiguous")
    print("\nTry:  lao firewall --request '<change description>'")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lao", description="LAO reliability plugin")
    sub = p.add_subparsers(dest="command", required=True)

    si = sub.add_parser("init", help="Bootstrap a working LAO runtime")
    si.add_argument("--dir", default=None, help="target directory (default: cwd)")
    si.add_argument("--agent", default="demo", help="agent id (default: demo)")
    si.add_argument("--no-sample", action="store_true", help="skip sample event")
    si.set_defaults(func=_cmd_init)

    st = sub.add_parser("trust-event", help="Record a Trust Event")
    st.add_argument("--dir", default=None, help="target directory")
    st.add_argument("--agent", default=None, help="agent id")
    st.add_argument("--type", default="success", choices=["success", "failure"])
    st.add_argument("--desc", default=None, help="non-interactive description")
    st.add_argument("--evidence", default="")
    st.add_argument("--repair", default="")
    st.add_argument("--anchor", action="append", default=None)
    st.add_argument("--prevention", default="")
    st.set_defaults(func=_cmd_trust_event)

    sv = sub.add_parser("verify", help="Recompute VerifyPing hashes")
    sv.set_defaults(func=_cmd_verify)

    ss = sub.add_parser("status", help="Show trust event / anchor counts")
    ss.add_argument("--dir", default=None)
    ss.set_defaults(func=_cmd_status)

    sd = sub.add_parser("demo", help="Run the 6-function demo")
    sd.set_defaults(func=_cmd_demo)

    sa = sub.add_parser("atom", help="Experience Atom: Trust Event → Atom → Anchor → Future Protection")
    sa.add_argument("--dir", default=None, help="target directory")
    sa.add_argument("--event", default=None, help="record a Trust Event then convert to Atom (JSON string)")
    sa.add_argument("--verify", action="store_true", help="verify Future Protection for anchors")
    sa.set_defaults(func=_cmd_atom)

    sf = sub.add_parser("firewall", help="Preference Firewall: efficiency OK, identity/value change NO")
    sf.add_argument("--request", default=None, help="change request to evaluate (non-interactive)")
    sf.add_argument("--agent", default="demo")
    sf.set_defaults(func=_cmd_firewall)

    return p


def main(argv: Optional[list] = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\naborted")
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"❌ {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
