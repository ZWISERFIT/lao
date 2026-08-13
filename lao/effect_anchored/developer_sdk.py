"""
LAODeveloperSDK — Developer SDK + Experience Loop (Phase2 P1-5·创始人令 v3.4)
=============================================================================
Sandbox 已完成, 现在封装为 LAO Developer SDK(外部开发者 10 分钟体验)。

用法:
    from lao import AgentRuntime
    agent = AgentRuntime(model="deepseek")
    agent.enable_trust()
    agent.enable_cost()
    agent.enable_memory()

体验流程(创始人):
    1. 创建 Agent → 2. 注入故障 → 3. LAO 修复 → 4. 查看成本下降 → 5. 生成 ExperienceAsset
最终: Developer Experience Certificate(DID + Contribution + Verified% + Asset EXP-000001)

注: 这是 SDK 门面(封装已建的 RuntimeRegistry/Sandbox/CostIntelligence/MemoryIntelligence/ExperienceAsset)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 复用已建模块
from lao.effect_anchored.runtime_registry import RuntimeRegistry
from lao.effect_anchored.sandbox import LAOSandbox
from lao.effect_anchored.routing.cost_intelligence import SavingsEngine
from lao.effect_anchored.memory_intelligence import MemoryIntelligenceEngine
from lao.effect_anchored.experience_asset import ExperienceAssetRegistry
from lao.effect_anchored.recovery_replay import RecoveryMemory
from lao.effect_anchored.reality_check import RealityCheckEngine


@dataclass
class DeveloperExperienceCertificate:
    """开发者体验证书(生态入口·Web5 原住民路径)。"""
    did: str = ""
    contribution: str = ""
    verified_pct: float = 0.0
    asset_id: str = ""
    attestation: str = ""
    ts: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class AgentRuntime:
    """LAO Developer SDK 门面(外部开发者入口)。"""

    def __init__(self, model: str = "deepseek", did: str = ""):
        self.model = model
        self.did = did or f"did:zwf:dev-{int(time.time())}"
        self._id = f"agent-{int(time.time())}"
        # 能力组件(惰性构建)
        self._registry: Optional[RuntimeRegistry] = None
        self._sandbox: Optional[LAOSandbox] = None
        self._cost: Optional[SavingsEngine] = None
        self._memory: Optional[MemoryIntelligenceEngine] = None
        self._assets: Optional[ExperienceAssetRegistry] = None
        self._recovery: Optional[RecoveryMemory] = None
        self._reality: Optional[RealityCheckEngine] = None
        # 开关
        self._trust_on = False
        self._cost_on = False
        self._memory_on = False
        # 证书
        self._certificate: Optional[DeveloperExperienceCertificate] = None

    # -- 能力开关(创始人 SDK 用法) --
    def enable_trust(self) -> "AgentRuntime":
        self._trust_on = True
        return self

    def enable_cost(self) -> "AgentRuntime":
        self._cost_on = True
        return self

    def enable_memory(self) -> "AgentRuntime":
        self._memory_on = True
        return self

    # -- 惰性构建各引擎 --
    def _engines(self):
        if self._registry is None:
            self._registry = RuntimeRegistry()
            self._sandbox = LAOSandbox(self._registry)
            self._cost = SavingsEngine()
            self._memory = MemoryIntelligenceEngine()
            self._assets = ExperienceAssetRegistry()
            self._recovery = RecoveryMemory()
            self._reality = RealityCheckEngine()
            self._registry.register(self._id, did=self.did, model=self.model, provider="deepseek")
        return self

    # -- 体验流程 1: 建 Agent(已构造) --

    # 2+3: 注入故障 → LAO 修复(闭环)
    def run_failure_heal(self, domain: str = "gateway") -> dict:
        """演示: 弄坏 → 自动修 → 证明。"""
        self._engines()
        sc = self._sandbox.run_heal_demo(self._id, domain)
        return {"agent": self._id, "domain": domain,
                "verified": sc.verified, "attestation": sc.attestation}

    # 4: 查看成本下降(Cost Intelligence)
    def record_cost_saving(self, task_type: str = "task", in_tok: int = 1000,
                           out_tok: int = 200) -> dict:
        """记录一次成本节省(同Agent original vs optimized)。"""
        self._engines()
        ev = self._cost.compute_saving(
            self._id, task_type, "deepseek-v4-pro", "deepseek-v4-flash",
            in_tok=in_tok, out_tok=out_tok, quality_score=96, switch_reason="cost_redline")
        return {"saved": round(ev.saving_amount, 5), "ratio": round(ev.saving_ratio, 3)}

    def cost_report(self) -> dict:
        """LAO Impact Report。"""
        self._engines()
        return self._cost.impact_report()

    # 5: 生成 ExperienceAsset
    def contribute_asset(self, problem: str, solution: str,
                         domain: str = "") -> dict:
        """开发者贡献 → 生成可验证 ExperienceAsset。"""
        self._engines()
        asset = self._assets.create(
            creator_did=self.did, problem=problem, solution=solution,
            domain=domain, verification_pct=99, tags=["developer-contrib"],
            policy_version="3.4.0")
        return {"asset_id": asset.asset_id, "verification_pct": asset.verification_pct,
                "attestation": asset.attestation, "did": self.did}

    # 最终: Developer Experience Certificate
    def get_certificate(self, contribution: str = "Recovery Pattern",
                        verified_pct: float = 99.0) -> DeveloperExperienceCertificate:
        """生成开发者体验证书(生态入口)。"""
        self._engines()
        cert = DeveloperExperienceCertificate(
            did=self.did, contribution=contribution, verified_pct=verified_pct,
            asset_id=self._assets.all()[-1].asset_id if self._assets.all() else "",
            attestation=self._assets.all()[-1].attestation if self._assets.all() else "",
            ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self._certificate = cert
        return cert

    # -- 便捷: 完整 5 步体验 --
    def run_developer_demo(self) -> dict:
        """一次完整的 10 分钟开发者体验(5步)。"""
        self._engines()
        out = {}
        # 1. 建 Agent(已构造)
        out["step1_create_agent"] = {"agent": self._id, "did": self.did}
        # 2+3. 注入故障→修复
        heal = self.run_failure_heal("gateway")
        out["step2_3_failure_heal"] = heal
        # 4. 成本下降
        for _ in range(10):
            self.record_cost_saving()
        out["step4_cost_report"] = self.cost_report()
        # 5. 生成资产
        asset = self.contribute_asset("Gateway Failure", "auto_heal_gateway", "gateway")
        out["step5_generate_asset"] = asset
        # 证书
        out["certificate"] = self.get_certificate().to_dict()
        return out
