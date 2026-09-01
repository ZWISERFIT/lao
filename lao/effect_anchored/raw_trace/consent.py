# -*- coding: utf-8 -*-
"""raw_trace.consent · 原卷留档授权记录（A1 默认关闭的开启凭据）

口径：开启必须存在授权记录，缺记录一律拒写。
授权记录要素：授权人、授予时间、范围、保留期天数、撤销记录。
零遥测：本模块只写本地 consent.jsonl / consent.log，不出站。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class ConsentDenied(PermissionError):
    """无有效授权记录时抛出（默认关闭语义）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RawTraceConsent:
    """原卷留档授权账。只追加，撤销不打洞（留痕可审计）。"""

    def __init__(self, vault_root: str):
        self.vault_root = vault_root
        self.records_path = os.path.join(vault_root, "consent.jsonl")
        self.audit_path = os.path.join(vault_root, "consent.log")

    # -- 授权 ----------------------------------------------------------------

    def grant(self, authorizer: str, scope: str, retention_days: int,
              record_id: Optional[str] = None) -> Dict[str, Any]:
        """开启留档的唯一入口：写入授权记录。缺任一要素拒写。"""
        if not authorizer or not scope:
            raise ConsentDenied("授权记录缺授权人或范围，拒绝开启")
        if not isinstance(retention_days, int) or retention_days <= 0:
            raise ConsentDenied("授权记录缺有效保留期，拒绝开启")
        rec = {
            "record_id": record_id or (
                "rt-consent-%s" % datetime.now(timezone.utc).strftime(
                    "%Y%m%d%H%M%S%f")),
            "authorizer": authorizer,
            "scope": scope,
            "retention_days": retention_days,
            "granted_at": _now_iso(),
            "revoked_at": None,
        }
        self._ensure_dir()
        with open(self.records_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self._audit("grant", rec)
        return rec

    def revoke(self, record_id: str, actor: str) -> bool:
        """撤销授权：不打洞，只追加撤销行。撤销后 active() 即查无。"""
        rec = {
            "record_id": record_id,
            "action": "revoke",
            "actor": actor,
            "revoked_at": _now_iso(),
        }
        self._ensure_dir()
        with open(self.records_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self._audit("revoke", rec)
        return True

    # -- 查验 ----------------------------------------------------------------

    def active(self, scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """返回当前有效的授权记录；无则返回 None（默认关闭即此态）。"""
        granted: Dict[str, Dict[str, Any]] = {}
        revoked: Dict[str, str] = {}
        if not os.path.exists(self.records_path):
            return None
        with open(self.records_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("action") == "revoke":
                    revoked[rec.get("record_id", "")] = rec.get("revoked_at", "")
                elif rec.get("record_id") and rec.get("granted_at"):
                    granted[rec["record_id"]] = rec
        for rid, rec in granted.items():
            if rid in revoked:
                continue
            if scope and rec.get("scope") != scope:
                continue
            # 保留期超期不等于撤销，但不再允许新增留档（到期由 vault 标失效）
            out = dict(rec)
            out["revoked_at"] = None
            return out
        return None

    def all_records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.records_path):
            return []
        out = []
        with open(self.records_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return out

    # -- 内部 ----------------------------------------------------------------

    def _ensure_dir(self) -> None:
        os.makedirs(self.vault_root, exist_ok=True)

    def _audit(self, action: str, rec: Dict[str, Any]) -> None:
        self._ensure_dir()
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"ts": _now_iso(), "action": action,
                 "record_id": rec.get("record_id", "")},
                ensure_ascii=False, sort_keys=True) + "\n")
