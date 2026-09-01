# -*- coding: utf-8 -*-
"""raw_trace.vault · 原卷账（A1 主体）

结构（全部限于 vault_root，用户侧）：
  ledger.jsonl   只追加原卷账（每行一条，已过脱敏钩子）
  index.json     条目索引（seq → 状态/到期日/哈希），原子重写
  audit.jsonl    管道自身操作审计（写入/失效），可完整回放

三条硬语义：
  1. 默认关闭：无有效授权记录时，append 抛 ConsentDenied，零写入；
  2. 只追加：账本文件永不截断、永不改写历史行；
  3. 保留期到期只标失效（status=expired），不物理删除（审计可溯）。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from lao.effect_anchored.raw_trace.consent import ConsentDenied, RawTraceConsent
from lao.effect_anchored.raw_trace.redaction import redact_payload


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


class RawTraceVault:
    """原卷留档管道。所有写操作均以有效授权记录为前提。"""

    SCHEMA = "lao-raw-trace-vault/1.0"

    def __init__(self, vault_root: Optional[str] = None):
        home = vault_root or os.path.join(
            os.environ.get("LAO_HOME", os.path.expanduser("~")), "raw_trace")
        self.vault_root = home
        self.ledger_path = os.path.join(home, "ledger.jsonl")
        self.index_path = os.path.join(home, "index.json")
        self.audit_path = os.path.join(home, "audit.jsonl")
        self.consent = RawTraceConsent(home)

    # -- 写入门（A1 默认关闭语义） -------------------------------------------

    def append(self, kind: str, payload: Dict[str, Any],
               source: str) -> Dict[str, Any]:
        """追加一条原卷记录。无有效授权 → ConsentDenied，零写入。

        payload 在进入账本前强制过脱敏钩子（写入路径上，不可绕过）。
        """
        rec = self.consent.active()
        if rec is None:
            raise ConsentDenied("原卷留档默认关闭：无有效授权记录，拒绝写入")
        entry = {
            "schema": self.SCHEMA,
            "seq": self._next_seq(),
            "kind": kind,
            "source": source,
            "recorded_at": _now_iso(),
            "consent_record_id": rec.get("record_id"),
            "expires_at": (_now() + timedelta(
                days=int(rec.get("retention_days", 0)))).isoformat(),
            "status": "active",
            "payload": redact_payload(payload),
        }
        entry["payload_sha256"] = hashlib.sha256(
            json.dumps(entry["payload"], ensure_ascii=False,
                       sort_keys=True).encode("utf-8")).hexdigest()
        os.makedirs(self.vault_root, exist_ok=True)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        self._update_index(entry)
        self._audit("append", {"seq": entry["seq"], "kind": kind})
        return entry

    # -- 保留期（到期只标失效，不物理删除） -----------------------------------

    def expire_due(self) -> List[int]:
        """把已过保留期的条目标记为 expired（索引层），返回被标记的 seq。

        账本原行不动；失效状态由索引承载，可审计回放。
        """
        idx = self._read_index()
        now = _now()
        expired: List[int] = []
        for item in idx.get("entries", []):
            if item.get("status") == "active" and item.get("expires_at"):
                try:
                    exp = datetime.fromisoformat(item["expires_at"])
                except ValueError:
                    continue
                if exp <= now:
                    item["status"] = "expired"
                    item["expired_at"] = _now_iso()
                    expired.append(item["seq"])
                    self._audit("expire", {"seq": item["seq"]})
        if expired:
            self._write_index(idx)
        return expired

    # -- 只读查询 ------------------------------------------------------------

    def read_entries(self, include_expired: bool = False) -> List[Dict[str, Any]]:
        if not os.path.exists(self.ledger_path):
            return []
        idx = {e["seq"]: e for e in self._read_index().get("entries", [])}
        out = []
        with open(self.ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                meta = idx.get(entry.get("seq"), {})
                entry["status"] = meta.get("status", entry.get("status", "active"))
                if entry["status"] != "active" and not include_expired:
                    continue
                out.append(entry)
        return out

    def stats(self) -> Dict[str, Any]:
        idx = self._read_index()
        entries = idx.get("entries", [])
        return {
            "enabled": self.consent.active() is not None,
            "total": len(entries),
            "active": sum(1 for e in entries if e.get("status") == "active"),
            "expired": sum(1 for e in entries if e.get("status") == "expired"),
        }

    # -- 内部 ----------------------------------------------------------------

    def _next_seq(self) -> int:
        idx = self._read_index()
        return int(idx.get("last_seq", 0)) + 1

    def _read_index(self) -> Dict[str, Any]:
        if not os.path.exists(self.index_path):
            return {"schema": self.SCHEMA, "last_seq": 0, "entries": []}
        try:
            with open(self.index_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"schema": self.SCHEMA, "last_seq": 0, "entries": []}

    def _update_index(self, entry: Dict[str, Any]) -> None:
        idx = self._read_index()
        idx["last_seq"] = entry["seq"]
        idx["entries"].append({
            "seq": entry["seq"],
            "kind": entry["kind"],
            "recorded_at": entry["recorded_at"],
            "expires_at": entry["expires_at"],
            "status": "active",
            "payload_sha256": entry["payload_sha256"],
        })
        self._write_index(idx)

    def _write_index(self, idx: Dict[str, Any]) -> None:
        """原子重写索引（先写临时件再替换）。"""
        os.makedirs(self.vault_root, exist_ok=True)
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.index_path)

    def _audit(self, action: str, detail: Dict[str, Any]) -> None:
        os.makedirs(self.vault_root, exist_ok=True)
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(
                {"ts": _now_iso(), "action": action, **detail},
                ensure_ascii=False, sort_keys=True) + "\n")
