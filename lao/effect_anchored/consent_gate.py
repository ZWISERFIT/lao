"""
Consent Gate — LAO 2.7 P0-2
=============================

确权时的授权检查 · 非安装时（数字主权核心）。

边界（对齐 LAO = Trust Layer + 创始人"用户拥有数据"）:
  - 原始经验仅存储本地（本地私有 · 自动）
  - 共享的只是「哈希化经验元数据」到 ZWISERFIT 网络
  - 授权是「每次确权时触发」，不是安装时一次性勾选

三个确认语义（对齐工单 checkboxes）:
  1. 同意共享哈希化经验元数据至 ZWISERFIT 网络
  2. 理解原始经验仅存储本地（不上传 / 平台技术上无法访问）
  3. 理解共享后可在 Melody 市场交易

用法:
  gate = ConsentGate(store_path=...)          # 本地持久化授权记录
  gate.request_consent(owner, domain)          # 返回授权请求(含3项语义)
  gate.grant(owner, domain)                    # 用户勾选同意 → 记录授权
  gate.is_granted(owner, domain)               # attest 前检查
  gate.revoke(owner, domain)                   # 撤销授权
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# 三项授权确认语义（对齐 Zeus P0-2 checkboxes）
CONSENT_ITEMS = [
    {
        "id": "share_hash_metadata",
        "label": "同意共享哈希化经验元数据至 ZWISERFIT 网络",
        "required": True,
    },
    {
        "id": "original_local_only",
        "label": "理解原始经验仅存储本地",
        "required": True,
    },
    {
        "id": "melody_market_tradable",
        "label": "理解共享后可在 Melody 市场交易",
        "required": True,
    },
]


@dataclass
class ConsentRecord:
    """一次授权记录。"""
    owner: str
    domain: str
    decisions: Dict[str, bool] = field(default_factory=dict)   # {item_id: True/False}
    all_accepted: bool = False
    granted_at: Optional[str] = None
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConsentGate:
    """授权门：确权(Ethan存证)前检查用户是否已授权共享哈希元数据。"""

    def __init__(self, store_path: Optional[str] = None):
        self._records: Dict[str, ConsentRecord] = {}   # key = f"{owner}:{domain}"
        self._path = store_path
        if store_path:
            self._load()

    # -- 授权请求 -----------------------------------------------------------

    def request_consent(self, owner: str, domain: str) -> Dict[str, Any]:
        """生成授权请求（含三项语义），返回给用户勾选。"""
        return {
            "owner": owner,
            "domain": domain,
            "items": CONSENT_ITEMS,
            "required_all": True,
            "message": (
                "确权前需授权共享哈希化经验元数据。原始经验仅存本地，"
                "平台技术上无法访问原始数据。共享后可在 Melody 市场交易。"
            ),
        }

    def grant(self, owner: str, domain: str,
              decisions: Optional[Dict[str, bool]] = None) -> bool:
        """用户接受授权。decisions 缺省视为全接受。

        Returns: 是否授权成功（必须全接受 required 项）。
        """
        key = f"{owner}:{domain}"
        dec = dict(decisions) if decisions else {i["id"]: True for i in CONSENT_ITEMS}
        # 强制所有 required 项为 True（任一 False = 未授权）
        all_accepted = all(dec.get(i["id"]) for i in CONSENT_ITEMS if i["required"])
        rec = ConsentRecord(
            owner=owner, domain=domain,
            decisions=dec, all_accepted=all_accepted,
            granted_at=datetime.now(timezone.utc).isoformat() if all_accepted else None,
        )
        self._records[key] = rec
        if self._path:
            self._save()
        return all_accepted

    def is_granted(self, owner: str, domain: str) -> bool:
        """确权前检查：该 owner+domain 是否已授权。"""
        rec = self._records.get(f"{owner}:{domain}")
        return bool(rec and rec.all_accepted and rec.granted_at)

    def revoke(self, owner: str, domain: str) -> None:
        """撤销授权。"""
        key = f"{owner}:{domain}"
        if key in self._records:
            del self._records[key]
            if self._path:
                self._save()

    def status(self, owner: str, domain: str) -> Dict[str, Any]:
        """查询某 owner+domain 的授权状态。"""
        rec = self._records.get(f"{owner}:{domain}")
        if not rec:
            return {"owner": owner, "domain": domain, "granted": False, "reason": "未授权"}
        return {
            "owner": owner, "domain": domain,
            "granted": bool(rec.all_accepted),
            "granted_at": rec.granted_at,
            "decisions": rec.decisions,
        }

    # -- 持久化 -------------------------------------------------------------

    def _load(self) -> None:
        if self._path and os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    raw = json.load(f)
                for k, d in raw.items():
                    self._records[k] = ConsentRecord(**d)
            except (json.JSONDecodeError, OSError, TypeError):
                self._records = {}

    def _save(self) -> None:
        if not self._path:
            return
        if os.path.dirname(self._path):
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump({k: r.to_dict() for k, r in self._records.items()},
                      f, ensure_ascii=False, indent=2)
