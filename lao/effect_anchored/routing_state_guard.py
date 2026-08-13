"""
RoutingStateGuard — Routing 状态防误回滚保护 (创始人令 Phase3)
=============================================================================
2026-08-13 事件: 19:42 接入 lao-router(9 agent→8765), 20:49 openclaw.json
被静默改回直连 DeepSeek — 没有 before/after/actor/reason/approval 任何记录。
这正是"production routing state 没有保护机制"的问题。

RoutingStateGuard:
- 任何 openclaw.json provider/baseUrl 修改必须产生 RoutingChangeEvent
- 包含 before / after / timestamp / actor / reason / approval
- **禁止静默改变**(silent change 是事故根因)
"""
from __future__ import annotations
import json, os, hashlib, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class RoutingChangeEvent:
    """一次 provider/baseUrl 路由变更(可审计·禁止静默)。"""
    event_id: str
    before: Dict[str, str] = field(default_factory=dict)
    after: Dict[str, str] = field(default_factory=dict)
    changed_agents: List[str] = field(default_factory=list)
    timestamp: str = ""
    actor: str = "unknown"
    reason: str = ""
    approval: str = ""
    checksum_before: str = ""
    checksum_after: str = ""

    def to_trust_event(self) -> dict:
        return {
            "event": "RoutingChange",
            "subtype": "RuntimeEvent",
            "domain": "routing",
            "changed_agents": self.changed_agents,
            "before": self.before,
            "after": self.after,
            "actor": self.actor,
            "reason": self.reason,
            "approval": self.approval,
            "timestamp": self.timestamp,
        }


class RoutingStateGuard:
    """路由状态守护: 快照→比对→事件(防静默变更)。"""

    def __init__(self, config_path: str = "/home/agentuser/.openclaw/openclaw.json",
                 ledger_path: str = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/logs/routing-change-events.jsonl"):
        self.config_path = config_path
        self.ledger_path = ledger_path
        self._snapshot: Optional[Dict[str, str]] = None
        os.makedirs(os.path.dirname(ledger_path), exist_ok=True)

    def snapshot(self) -> Dict[str, str]:
        """提取当前 openclaw.json 中所有 provider 的 baseUrl 映射。"""
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path) as f:
            data = json.load(f)
        # providers 在 models.providers 下(OpenClaw 实际结构)
        providers = data.get("models", {}).get("providers", {})
        result = {}
        for name, p in providers.items():
            if isinstance(p, dict) and "baseUrl" in p and p["baseUrl"]:
                result[name] = p["baseUrl"]
        return result

    def save_snapshot(self) -> Dict[str, str]:
        self._snapshot = self.snapshot()
        return self._snapshot

    def detect_change(self, actor: str = "unknown", reason: str = "",
                      approval: str = "") -> Optional[RoutingChangeEvent]:
        if self._snapshot is None:
            self.save_snapshot()
            return None
        current = self.snapshot()
        before = self._snapshot
        changed = [k for k in set(before) | set(current)
                   if before.get(k) != current.get(k)]
        if not changed:
            return None
        ev = RoutingChangeEvent(
            event_id=f"RCE-{int(time.time())}",
            before={k: before.get(k, "") for k in changed},
            after={k: current.get(k, "") for k in changed},
            changed_agents=changed,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            actor=actor, reason=reason, approval=approval,
            checksum_before=_fp(json.dumps(before, sort_keys=True)),
            checksum_after=_fp(json.dumps(current, sort_keys=True)),
        )
        self._persist(ev)
        self._snapshot = current
        return ev

    def _persist(self, ev: RoutingChangeEvent):
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_trust_event(), ensure_ascii=False, default=str) + "\n")

    def verify_routing(self, expected_base: str = "http://127.0.0.1:8765/v1",
                       expected_count: int = 9) -> dict:
        """校验当前路由是否指向预期(如 lao-router 9/9)。"""
        snap = self.snapshot()
        agent_providers = [k for k in snap if k.startswith("deepseek-")]
        on_router = [k for k in agent_providers if snap[k] == expected_base]
        return {
            "total_agent_providers": len(agent_providers),
            "on_router": len(on_router),
            "expected": expected_count,
            "ok": len(on_router) >= expected_count,
            "off_router": [k for k in agent_providers if snap[k] != expected_base],
        }


def _fp(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
