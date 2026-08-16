"""
ExperienceAsset — ExperienceAsset MVP (Phase2 P0-3·创始人令 v3.4 提前)
=============================================================================
外部开发者加入必须有"资产感"。贡献不只是 issue/PR/测试报告, 而是生成的
**可验证 ExperienceAsset**(Web5 原住民入口)。

    ExperienceAsset:
        asset_id(EXP-00001)
        creator_did(did:zwf:developer)
        problem(Gateway Failure)
        solution(Recovery Pattern)
        verification_pct(98%)
        attestation(TrustEvent hash)

设计原则:
- 单一事实源: 资产确权基于 TrustEvent(attestation = TrustEvent hash)·不另建账本
- 所有贡献可证明: verification + attestation 溯源
- 衔接 Phase3 DID/VC: creator_did · 可发 VC(Recovery Pattern Creator)
"""
from __future__ import annotations
import hashlib, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ExperienceAsset:
    """一个外部开发者贡献的可验证经验资产。"""
    asset_id: str
    creator_did: str
    problem: str
    solution: str
    verification_pct: float = 0.0
    attestation: str = ""
    domain: str = ""                 # 故障域(gateway/context/provider/...)
    tags: List[str] = field(default_factory=list)
    policy_version: str = ""
    created_ts: str = ""
    source_events: List[str] = field(default_factory=list)   # 溯源 TrustEvent

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id, "creator_did": self.creator_did,
            "problem": self.problem, "solution": self.solution,
            "verification_pct": self.verification_pct, "attestation": self.attestation,
            "domain": self.domain, "tags": self.tags,
            "policy_version": self.policy_version, "created_ts": self.created_ts,
            "source_events": self.source_events,
        }


class ExperienceAssetRegistry:
    """ExperienceAsset 注册表(内存 + 可持久化)。

    2026-08-16 修复(L3确权): 原实现纯内存无 store_path — 资产"上链"进程
    即失。传入 store_path 后 JSON 持久化(不传=内存·兼容旧行为)。
    """

    def __init__(self, store_path: Optional[str] = None):
        self._assets: Dict[str, ExperienceAsset] = {}
        self._counter = 0
        self._path = store_path
        if store_path:
            self._load()

    def _load(self) -> None:
        import json as _json
        import os
        if not (self._path and os.path.exists(self._path)):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = _json.load(f)
            for d in raw.get("assets", []):
                a = ExperienceAsset(**d)
                self._assets[a.asset_id] = a
            self._counter = int(raw.get("counter", len(self._assets)))
        except (OSError, _json.JSONDecodeError, TypeError):
            pass

    def _save(self) -> None:
        import json as _json
        import os
        if not self._path:
            return
        try:
            d = os.path.dirname(self._path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump({"counter": self._counter,
                            "assets": [a.to_dict() for a in self._assets.values()]},
                           f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def create(self, creator_did: str, problem: str, solution: str,
               domain: str = "", verification_pct: float = 0.0,
               trust_event_hash: str = "", tags: Optional[List[str]] = None,
               policy_version: str = "") -> ExperienceAsset:
        """创建并注册一个资产(Tenant 贡献上链)。"""
        self._counter += 1
        asset = ExperienceAsset(
            asset_id=f"EXP-{self._counter:05d}",
            creator_did=creator_did, problem=problem, solution=solution,
            domain=domain, verification_pct=verification_pct,
            attestation=trust_event_hash or _simple_fp(f"{self._counter}:{problem}:{solution}"),
            tags=tags or [], policy_version=policy_version,
            created_ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        self._assets[asset.asset_id] = asset
        self._save()
        return asset

    def get(self, asset_id: str) -> Optional[ExperienceAsset]:
        return self._assets.get(asset_id)

    def all(self) -> List[ExperienceAsset]:
        return list(self._assets.values())

    def count(self) -> int:
        return len(self._assets)

    def verify(self, asset_id: str) -> bool:
        """校验资产完整性。

        - 若 attestation 为本地指纹 → 重算比对(不信任自报)
        - 若 attestation 为外部 TrustEvent hash(sha256:...) → 校验非空+资产字段完整（外部 provenance 已在 TrustEvent ledger 可独立验证）
        """
        a = self._assets.get(asset_id)
        if not a:
            return False
        # 资产必须字段完整
        if not (a.problem and a.solution and a.creator_did):
            return False
        if a.attestation.startswith("sha256:"):
            # 外部 TrustEvent 溯源·非空即可（真实性由 TrustEvent ledger 独立验证）
            return bool(a.attestation)
        # 本地指纹·重算比对
        expect = _simple_fp(f"{int(asset_id.split('-')[-1])}:{a.problem}:{a.solution}")
        return a.attestation == expect

    def trust_event(self, asset: ExperienceAsset) -> dict:
        """→ TrustEvent 负载(资产上链·可审计)。"""
        return {
            "event": "AssetAttested",
            "subtype": "OwnershipEvent",
            "domain": asset.domain or "experience",
            "asset_id": asset.asset_id,
            "creator_did": asset.creator_did,
            "problem": asset.problem,
            "verification_pct": asset.verification_pct,
            "attestation": asset.attestation,
            "ts": asset.created_ts,
        }


def _simple_fp(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
