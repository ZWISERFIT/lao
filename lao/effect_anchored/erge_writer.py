"""
ERGE Writer — 三层认知锚点 → ERGE SQLite 五表联动
===================================================

把 Cognitive Anchor Framework (cognitive_anchor.py) 的产出
写入 ERGE anchors.db 五表 (anchors / tags / events / permissions / versions)。

三层映射 (Shuyu 批准 2026-08-10):
    FactAnchor      → status=candidate   · impact_level=reference_only  · anchor_type=fact
    DecisionAnchor  → status=verified    · impact_level=decision_aid    · anchor_type=decision
    CognitiveAnchor → status=permanent   · impact_level=runtime_behavior · anchor_type=cognitive
                                      + preference_firewall=1

写入时五表联动:
    anchors      — 主表(核心锚点)
    tags         — 标签
    events       — 事件(created/promoted 记录状态变更)
    permissions  — 共享权限
    versions     — 版本快照(不可变追溯)
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional


# 三层 → ERGE 映射表
TYPE_MAP = {
    "fact":       {"status": "candidate", "impact_level": "reference_only", "preference_firewall": 0},
    "decision":   {"status": "verified",  "impact_level": "decision_aid",    "preference_firewall": 0},
    "cognitive":  {"status": "permanent", "impact_level": "runtime_behavior", "preference_firewall": 1},
}


class ErgeWriter:
    """
    把三层锚点写入 ERGE anchors.db（五表联动）。
    """

    def __init__(self, db_path: str = "/home/agentuser/.openclaw/workspace/data/ZWISERFIT/cognitive-os/anchors.db"):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    def write_anchor(self, anchor: Dict[str, Any],
                     agent: str = "tristan", reason: str = "LAO 2.7 P0-① Cognitive Anchor") -> str:
        """
        写入/更新一个三层锚点到 anchors.db（五表联动）。

        anchor: CognitiveAnchorStore 的 anchor.to_dict() 或 anchors["current"]
                关键字段: anchor_id / anchor_type / value / source / tags / trust_weight
        """
        anchor_id = anchor["anchor_id"]
        anchor_type = anchor.get("anchor_type", "fact")
        tm = TYPE_MAP.get(anchor_type, TYPE_MAP["fact"])

        # value 合并 rule/rationale/counter_example
        value = anchor.get("value", {})
        if isinstance(value, dict):
            rule = str(value.get("rule") or value.get("action_rule") or value.get("principle") or json.dumps(value, ensure_ascii=False))
            rationale = str(value.get("rationale") or value.get("trigger_condition") or "")
            counter = str(value.get("counter_example") or value.get("counter_examples") or "")
        else:
            rule = json.dumps(value, ensure_ascii=False)
            rationale = ""
            counter = ""

        trust = float(anchor.get("trust_weight", 1.0))
        category = self._infer_category(anchor, anchor_type)

        conn = self._conn()
        try:
            # 版本号读取须在 upsert 之前(新锚点=1·已存在=旧version+1)
            prev_row = conn.execute(
                "SELECT version FROM anchors WHERE id = ?", (anchor_id,)).fetchone()
            cur = conn.execute(
                """INSERT INTO anchors
                   (id, category, status, owner, scope, trust_weight, impact_level,
                    confidence_score, evidence_count, source_type, source_timestamp,
                    rule, rationale, counter_example, preference_firewall, anchor_type)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     anchor_type=excluded.anchor_type,
                     status=excluded.status,
                     impact_level=excluded.impact_level,
                     trust_weight=excluded.trust_weight,
                     rule=excluded.rule,
                     rationale=excluded.rationale,
                     counter_example=excluded.counter_example,
                     preference_firewall=excluded.preference_firewall,
                     updated_at=datetime('now'),
                     version=version+1""",
                (anchor_id, category, tm["status"], anchor.get("owner", "suzanne"), "global",
                 trust, tm["impact_level"], trust, anchor.get("evidence_count", 1),
                 anchor.get("source_type", "agent_derived"),
                 anchor.get("created_at", self._now()),
                 rule, rationale, counter, tm["preference_firewall"],
                 anchor_type),
            )
            # 版本快照 (versions 表)
            # 修复(2026-08-16): 旧实现 `cur.lastrowid and 1 or 1` 恒写 version=1,
            # versions 表版本永不递增(版本追溯失效)。改为真实递增。
            next_version = (prev_row["version"] + 1) if prev_row and prev_row["version"] else 1
            conn.execute(
                "INSERT INTO versions (anchor_id, version, snapshot, created_by) VALUES (?,?,?,?)",
                (anchor_id, next_version, json.dumps(anchor, ensure_ascii=False), agent),
            )
            # 事件 (events 表): created
            conn.execute(
                "INSERT INTO events (anchor_id, event_type, to_status, agent, reason) VALUES (?,?,?,?,?)",
                (anchor_id, "created", tm["status"], agent, reason),
            )
            # 标签 (tags 表)
            for tag in anchor.get("tags", []):
                conn.execute(
                    "INSERT OR IGNORE INTO tags (anchor_id, tag) VALUES (?,?)",
                    (anchor_id, str(tag)),
                )
            # 权限 (permissions 表): owner + 检索 agent(tristan) + 显式 allowed_agents
            conn.execute(
                "INSERT OR IGNORE INTO permissions (anchor_id, agent_id, access_level) VALUES (?,?,?)",
                (anchor_id, anchor.get("owner", "suzanne"), "admin"),
            )
            # 写入方 agent 默认可读(使其能通过 erge-retrieve <agent> 检索到)
            conn.execute(
                "INSERT OR IGNORE INTO permissions (anchor_id, agent_id, access_level) VALUES (?,?,?)",
                (anchor_id, agent, "read"),
            )
            for ag in anchor.get("allowed_agents", []):
                conn.execute(
                    "INSERT OR IGNORE INTO permissions (anchor_id, agent_id, access_level) VALUES (?,?,?)",
                    (anchor_id, ag, "read"),
                )
            conn.commit()
        finally:
            conn.close()
        return anchor_id

    def write_decision_anchors_batch(self, anchors: List[Dict[str, Any]], agent: str = "tristan") -> List[str]:
        """批量写入多个决策/认知锚点。"""
        ids = []
        for a in anchors:
            ids.append(self.write_anchor(a, agent=agent))
        return ids

    def _infer_category(self, anchor: Dict[str, Any], anchor_type: str) -> str:
        """推断 category。"""
        tags = anchor.get("tags", [])
        # 三层默认 category
        if anchor_type == "cognitive":
            return "governance" if "governance" in tags else "architecture"
        if anchor_type == "decision":
            return "product"
        return "architecture"

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    # -- 检索验证 -----------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT anchor_type, COUNT(*) c FROM anchors GROUP BY anchor_type").fetchall()
            return {r["anchor_type"]: r["c"] for r in rows}
        finally:
            conn.close()
