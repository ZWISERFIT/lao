# -*- coding: utf-8 -*-
"""raw_trace.registry · 双写登记（A2，口径照抄件②记忆容器）

对齐 113号呈验件件②（114号终审PASS）的结构化口径：
  1. 对象文件＋manifest＋SHA-256；
  2. 三态 candidate / ratified / withdrawn，状态变更由流转账
     （state_log）承载——不做成可写字段，撤回记录只能由真实流转产生；
  3. 来源引用只存「类别＋编号＋哈希」，不整包复制正文；
  4. LAO 侧只产引用件，不重写件②任何代码；ral-core 只读。

写盘范围：仅限 vault_root 下 references/（对象件＋manifest＋流转账）。
默认关闭语义与 A1 同源：无有效授权记录时拒绝登记。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lao.effect_anchored.raw_trace.consent import ConsentDenied, RawTraceConsent

STATES = ("candidate", "ratified", "withdrawn")

MANIFEST_SCHEMA = "ral-raw-trace-manifest/1.0"
REFERENCE_KINDS = ("raw_volume", "ris_event", "archive", "candidate_artifact")


class ReferenceLedgerError(ValueError):
    """登记口径违规（缺哈希、非法状态转移、正文夹带等）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class ReferenceRegistry:
    """原卷引用登记处（LAO 侧）。件②登记口径的引用件镜像。"""

    def __init__(self, vault_root: str):
        self.root = os.path.join(vault_root, "references")
        self.manifest_path = os.path.join(self.root, "manifest.json")
        self.state_log_path = os.path.join(self.root, "state_log.jsonl")
        self.ref_log_path = os.path.join(self.root, "references.jsonl")
        self.consent = RawTraceConsent(vault_root)

    # -- 登记（对象文件＋哈希；不复制正文） -----------------------------------

    def register(self, card_id: str, version: int, subject_id: str,
                 library: str, ownership: str, visibility: str,
                 origin_path: str, revenue_formula: str,
                 approval_artifact: Optional[str] = None,
                 approval_sha256: Optional[str] = None,
                 actor: str = "lao") -> Dict[str, Any]:
        """登记一条卡→原卷引用。逐字段对齐件② ral_memory_card 列。

        纪律：只存指向与哈希，不复制正文——本方法不接受任何正文字段，
        传入正文即报 ReferenceLedgerError（结构上杜绝第二份副本）。
        """
        if self.consent.active() is None:
            raise ConsentDenied("原卷留档默认关闭：无有效授权记录，拒绝登记")
        if library not in ("personal", "collaborative"):
            raise ReferenceLedgerError("library 仅接受 personal/collaborative")
        if visibility not in ("internal", "public"):
            raise ReferenceLedgerError("visibility 仅接受 internal/public")
        if not os.path.exists(origin_path):
            raise ReferenceLedgerError("origin_path 不存在，拒绝登记悬空指向")
        origin_sha256 = _sha256_file(origin_path)
        if len(origin_sha256) != 64:
            raise ReferenceLedgerError("origin_sha256 长度必须为64")

        man = self._read_manifest()
        seq = int(man.get("last_seq", 0)) + 1
        # artifact_vid 沿用件②口径：形如 CWF-005@1（card_id@version）
        artifact_vid = "%s@%d" % (card_id, version)
        entry = {
            "card_id": card_id,
            "version": version,
            "subject_id": subject_id,
            "library": library,
            "ownership": ownership,
            "visibility": visibility,
            "state": "candidate",
            "origin_path": origin_path,
            "origin_sha256": origin_sha256,
            "artifact_vid": artifact_vid,
            "approval_artifact": approval_artifact,
            "approval_sha256": approval_sha256,
            "revenue_formula": revenue_formula,
            "ingested_at": _now_iso(),
            "created_at": _now_iso(),
            "manifest_seq": seq,
        }
        os.makedirs(self.root, exist_ok=True)
        with open(self.ref_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        man["last_seq"] = seq
        man["entries"].append({
            "artifact_vid": artifact_vid,
            "card_id": card_id,
            "version": version,
            "state": "candidate",
            "origin_sha256": origin_sha256,
        })
        self._write_manifest(man)
        self._append_state_log(card_id, version, None, "candidate", actor,
                               reason="register")
        return entry

    def add_source_reference(self, card_id: str, version: int,
                             ref_kind: str, ref_id: str,
                             ref_sha256: Optional[str] = None) -> None:
        """来源引用：只存「类别＋编号＋哈希」（件② ral_memory_reference 口径）。

        编号长度上限64——防以超长 ref_id 变相夹带正文（件②同源约束）。
        """
        if ref_kind not in REFERENCE_KINDS:
            raise ReferenceLedgerError("ref_kind 非法：%s" % ref_kind)
        if not ref_id or len(ref_id) > 64:
            raise ReferenceLedgerError("ref_id 必须 1–64 字符（防正文夹带）")
        if self.consent.active() is None:
            raise ConsentDenied("原卷留档默认关闭：无有效授权记录，拒绝登记")
        rec = {
            "card_id": card_id, "version": version,
            "ref_kind": ref_kind, "ref_id": ref_id,
            "ref_sha256": ref_sha256, "created_at": _now_iso(),
        }
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, "source_references.jsonl"),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    # -- 三态流转（只经流转账，不做成可写字段） -------------------------------

    def ratify(self, card_id: str, version: int, actor: str,
               approval_artifact: Optional[str] = None,
               approval_sha256: Optional[str] = None,
               reason: str = "") -> Dict[str, Any]:
        return self._transition(card_id, version, "ratified", actor,
                                approval_artifact, approval_sha256, reason)

    def withdraw(self, card_id: str, version: int, actor: str,
                 reason: str = "") -> Dict[str, Any]:
        return self._transition(card_id, version, "withdrawn", actor,
                                None, None, reason)

    def state_of(self, card_id: str, version: int) -> Optional[str]:
        e = self._latest(card_id, version)
        return e.get("state") if e else None

    def state_log(self, card_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        if not os.path.exists(self.state_log_path):
            return out
        with open(self.state_log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if card_id is None or rec.get("card_id") == card_id:
                    out.append(rec)
        return out

    def manifest(self) -> Dict[str, Any]:
        return self._read_manifest()

    # -- 内部 ----------------------------------------------------------------

    def _transition(self, card_id: str, version: int, to_state: str,
                    actor: str, approval_artifact: Optional[str],
                    approval_sha256: Optional[str],
                    reason: str) -> Dict[str, Any]:
        if to_state not in STATES:
            raise ReferenceLedgerError("非法状态：%s" % to_state)
        e = self._latest(card_id, version)
        if e is None:
            raise ReferenceLedgerError("未登记的卡不能流转：%s@%s"
                                       % (card_id, version))
        from_state = e.get("state")
        # 件②口径：状态只能真实流转产生；撤回后不可复活
        if from_state == "withdrawn":
            raise ReferenceLedgerError("已撤回的卡不可再流转")
        if from_state == to_state:
            raise ReferenceLedgerError("状态未变化，拒绝空转")
        e["state"] = to_state
        if approval_artifact:
            e["approval_artifact"] = approval_artifact
            e["approval_sha256"] = approval_sha256
        os.makedirs(self.root, exist_ok=True)
        with open(self.ref_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
        man = self._read_manifest()
        for item in man.get("entries", []):
            if item.get("artifact_vid") == e.get("artifact_vid"):
                item["state"] = to_state
        self._write_manifest(man)
        self._append_state_log(card_id, version, from_state, to_state, actor,
                               approval_artifact, approval_sha256, reason)
        return e

    def _latest(self, card_id: str, version: int) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.ref_log_path):
            return None
        hit = None
        with open(self.ref_log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("card_id") == card_id and \
                        rec.get("version") == version:
                    hit = rec
        return hit

    def _append_state_log(self, card_id: str, version: int,
                           from_state: Optional[str], to_state: str,
                           actor: str, approval_artifact: Optional[str] = None,
                           approval_sha256: Optional[str] = None,
                           reason: str = "") -> None:
        logs = self.state_log(card_id)
        seq = sum(1 for r in logs if r.get("version") == version) + 1
        rec = {
            "card_id": card_id, "version": version, "seq": seq,
            "from_state": from_state, "to_state": to_state,
            "actor": actor,
            "approval_artifact": approval_artifact,
            "approval_sha256": approval_sha256,
            "reason": reason, "changed_at": _now_iso(),
        }
        os.makedirs(self.root, exist_ok=True)
        with open(self.state_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def _read_manifest(self) -> Dict[str, Any]:
        if not os.path.exists(self.manifest_path):
            return {"schema": MANIFEST_SCHEMA, "last_seq": 0, "entries": []}
        try:
            with open(self.manifest_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"schema": MANIFEST_SCHEMA, "last_seq": 0, "entries": []}

    def _write_manifest(self, man: Dict[str, Any]) -> None:
        os.makedirs(self.root, exist_ok=True)
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.manifest_path)
