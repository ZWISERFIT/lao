# -*- coding: utf-8 -*-
"""raw_trace.withdraw · 撤回贯通（D1）

口径（99号批件 D1）：卡撤回事件 → 原卷引用同步置 withdrawn ＋
导出清单中该卡样本标记失效；全链路留痕，审计账可完整回放。

设计：
  1. 状态流转只走 registry 的三态流转账（件②口径：撤回记录只能由
     真实流转产生，不可作为入参伪造）；
  2. 导出清单（A3 导出器的口径字段先行落地）：export_samples.jsonl
     每条样本带 artifact_vid 与 status，撤回后 status=
     invalidated_by_withdrawal，样本行本身不物理删除；
  3. 每次撤回贯通在 withdraw_audit.jsonl 留一行：卡、版本、发起人、
     联动结果、时间——回放即逐行重放。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lao.effect_anchored.raw_trace.consent import ConsentDenied, RawTraceConsent
from lao.effect_anchored.raw_trace.registry import ReferenceRegistry

SAMPLE_STATUSES = ("active", "invalidated_by_withdrawal")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WithdrawalGateway:
    """撤回贯通网关：一处发起，引用登记与样本清单联动失效。"""

    def __init__(self, vault_root: str):
        self.vault_root = vault_root
        self.registry = ReferenceRegistry(vault_root)
        self.samples_path = os.path.join(vault_root, "export_samples.jsonl")
        self.audit_path = os.path.join(vault_root, "withdraw_audit.jsonl")
        self.consent = RawTraceConsent(vault_root)

    # -- 样本清单（A3 导出器口径字段先行） -----------------------------------

    def record_sample(self, artifact_vid: str, sample_ref: str,
                      kind: str) -> Dict[str, Any]:
        """登记一条训练样本引用（不含正文；A3 建后以此为口径）。"""
        if self.consent.active() is None:
            raise ConsentDenied("原卷留档默认关闭：无有效授权记录，拒绝登记")
        rec = {
            "artifact_vid": artifact_vid,
            "sample_ref": sample_ref,
            "kind": kind,
            "status": "active",
            "recorded_at": _now_iso(),
        }
        os.makedirs(self.vault_root, exist_ok=True)
        with open(self.samples_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return rec

    # -- 撤回贯通 ------------------------------------------------------------

    def withdraw_card(self, card_id: str, version: int, actor: str,
                      reason: str = "") -> Dict[str, Any]:
        """撤卡 → 引用置 withdrawn → 样本清单联动失效 → 审计留痕。

        任一步失败即整体报错，不回写半成品状态（宁可停，不半吊）。
        """
        entry = self.registry.withdraw(card_id, version, actor, reason)
        artifact_vid = entry.get("artifact_vid")
        affected = self._invalidate_samples(artifact_vid)
        audit = {
            "ts": _now_iso(),
            "card_id": card_id,
            "version": version,
            "actor": actor,
            "reason": reason,
            "artifact_vid": artifact_vid,
            "samples_invalidated": affected,
        }
        os.makedirs(self.vault_root, exist_ok=True)
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(audit, ensure_ascii=False, sort_keys=True) + "\n")
        return audit

    # -- 审计回放 ------------------------------------------------------------

    def replay(self) -> List[Dict[str, Any]]:
        """完整回放撤回贯通审计账（逐行、按时序）。"""
        out = []
        if not os.path.exists(self.audit_path):
            return out
        with open(self.audit_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return out

    def samples(self, artifact_vid: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        if not os.path.exists(self.samples_path):
            return out
        with open(self.samples_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if artifact_vid is None or rec.get("artifact_vid") == artifact_vid:
                    out.append(rec)
        return out

    def effective_samples(self) -> List[Dict[str, Any]]:
        """当前有效（未被撤回失效）的样本清单——导出器只准取此集。"""
        seen = {}
        for rec in self.samples():
            seen[rec["sample_ref"]] = rec  # 后行覆盖前行（追加式生效口径）
        return [r for r in seen.values() if r.get("status") == "active"]

    # -- 内部 ----------------------------------------------------------------

    def _invalidate_samples(self, artifact_vid: Optional[str]) -> int:
        """追加失效行（样本行不物理删除；追加式账）。"""
        if not artifact_vid or not os.path.exists(self.samples_path):
            return 0
        count = 0
        appends = []
        for rec in self.samples(artifact_vid):
            if rec.get("status") == "active":
                rec = dict(rec)
                rec["status"] = "invalidated_by_withdrawal"
                rec["invalidated_at"] = _now_iso()
                appends.append(rec)
                count += 1
        if appends:
            with open(self.samples_path, "a", encoding="utf-8") as f:
                for rec in appends:
                    f.write(json.dumps(rec, ensure_ascii=False,
                                       sort_keys=True) + "\n")
        return count
