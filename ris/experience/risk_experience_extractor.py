"""
RIS Experience Extraction — 运行时异常恢复经验提取 (Phase RIS-Enablement)
=============================================================================
创始人 11:38 令：RIS 确认未启动(L1)，需完成接入。本模块负责 **Experience Extraction**：
RIS 捕获 runtime 异常(session卡死/gateway挂/CPU过载)后，**恢复经验如何提取沉淀**。

场景链:
   异常(RuntimeHealthEvent) → 恢复(RecoveryEngine 五步闭环) → 恢复经验提取
   → 沉淀进 LAO 经验库(供 experience_matching / auto_extract_anchors 复用)

复用(不开发新架构·约束):
   - ris.recovery.RecoveryResult   : 恢复闭环产物(异常→分类→恢复→验证→记录)
   - lao...evolution.ExperienceExtractor : 既有错误模式萃取 + 模式注册 + 锚点生成
   - lao...evolution.atom_engine    : 经验原子库(沉淀)

本模块 = 适配层: 把 RIS 的《恢复经验》翻译成 LAO 可复用的《经验模式/锚点》。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# 复用 LAO 既有的 ExperienceExtractor(错误模式萃取 + 注册表 + 锚点生成)
try:
    from lao.effect_anchored.evolution import ExperienceExtractor
    from lao.effect_anchored.cognitive_anchor import Anchor, DecisionAnchor
except Exception:  # 保护性 import
    ExperienceExtractor = None
    Anchor = DecisionAnchor = object

try:
    from ris.recovery import RecoveryResult, RecoveryAction
except Exception:
    RecoveryResult = object


# RIS 事件 → LAO 类别映射(model_router 粒度对齐 experience_extractor 的 category)
_EVENT_TO_CATEGORY = {
    "session_recovery": "coordination",
    "session_unresponsive": "coordination",
    "gateway_recovery": "infrastructure",
    "gateway_restart": "infrastructure",
    "cpu_anomaly": "infrastructure",
    "memory_anomaly": "infrastructure",
    "disk_anomaly": "infrastructure",
    "network_anomaly": "infrastructure",
    "http_unavailable": "infrastructure",
    "webui_unavailable": "infrastructure",
    "provider_isolation": "infrastructure",
    "config_drift": "coordination",
    # 默认
    "default": "infrastructure",
}

# 恢复成功 → 可复用经验(trust 提升)；恢复失败 → 防复发约束(需永久化)
RECOVERED_METHOD_DEFAULT = "auto"


@dataclass
class RecoveryExperience:
    """一条从 RIS 恢复闭环提取的【恢复经验】。"""
    experience_id: str
    event_type: str               # session_recovery / gateway_recovery / cpu_anomaly ...
    agent_id: str
    classified: str = ""          # 分类结论(如 session_down / cpu_anomaly)
    recovered: bool = False       # 是否恢复
    verified: bool = False        # 是否验证通过(铁律)
    attempts: int = 0             # 尝试次数
    recovery_method: str = ""     # 恢复方式(如 L1_restart / L2_hard / auto)
    category: str = "infrastructure"
    harm: str = "runtime"         # 领域: runtime(运行时异常)
    # 沉淀后原样保留 detail, 供 LAO 经验库归档
    detail: Dict[str, Any] = field(default_factory=dict)
    extracted_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RiskExperienceExtractor:
    """从 RIS 恢复结果提取可沉淀的恢复经验。

    与既有 ExperienceExtractor 的分工:
      - 既有 ExperienceExtractor: 处理【Agent 纠错/犯错事件】(H_intercept)
      - 本模块: 处理【RIS 运行时异常→恢复】事件, 翻译成同构经验结构复用其沉淀链路

    提取规则(对齐 LAO 经验复利闭环):
      1. 恢复成功(recovered+verified) → 可复用恢复动作(供未来同类异常快速恢复)
      2. 恢复失败 / 多次尝试才成功 → 防复发约束(需永久化, 触发下轮优化)
    """

    def __init__(self,
                 registry_path: Optional[str] = None,
                 store_path: Optional[Path] = None):
        # 复用既有 ExperienceExtractor 的模式注册 + 指纹去重能力
        self.extractor = ExperienceExtractor(registry_path=registry_path) \
            if ExperienceExtractor is not None else None
        # 恢复经验沉淀 JSONL
        default_store = Path(__file__).resolve().parent / "data" / "recovery_experience.jsonl"
        self.store_path = Path(store_path) if store_path else default_store
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    # ── 提取(主入口: 由 Tristan Recovery Executor 在 recorded 后调用) ──────
    def extract_from_recovery(self, result: Any, recovery_method: str = "") -> RecoveryExperience:
        """从 RecoveryResult 提取恢复经验。

        Args:
            result: ris.recovery.RecoveryResult(五步闭环产物)
            recovery_method: 本次恢复方式(如 "L1_restart"/"L2_hard"), 空则默认
        """
        ev_type = getattr(result, "event_type", "") or "runtime_recovery"
        agent = getattr(result, "agent_id", "") or "unknown"
        classified = getattr(result, "classified", "") or ev_type
        recovered = bool(getattr(result, "recovered", False))
        verified = bool(getattr(result, "verified", False))
        attempts = int(getattr(result, "attempts", 0))
        detail = dict(getattr(result, "detail", {}) or {})

        category = _EVENT_TO_CATEGORY.get(ev_type, _EVENT_TO_CATEGORY["default"])
        method = recovery_method or detail.get("recovery_method") or (
            RECOVERED_METHOD_DEFAULT if recovered else "")

        exp = RecoveryExperience(
            experience_id=f"ris-exp-{int(time.time()*1000)}",
            event_type=ev_type, agent_id=agent, classified=classified,
            recovered=recovered, verified=verified, attempts=attempts,
            recovery_method=method, category=category, detail=detail,
        )

        # 复用既有 extractor 沉淀(若可用): 把恢复结果转成 H_intercept 同构输入
        if self.extractor is not None and (recovered or attempts > 0):
            self._persist_via_extractor(exp)

        self._append_store(exp)
        return exp

    # ── 交给 LAO 既有 extractor 沉淀(注册表 + 指纹去重) ───────────────────
    def _persist_via_extractor(self, exp: RecoveryExperience) -> None:
        """把恢复经验转成既有 ExperienceExtractor 的输入, 走同一沉淀链路。

        恢复成功 → 记为"已验证正确行为"(success, 供 auto_extract_anchors 生成 Fact 锚点)
        恢复失败 → 记为"错误模式"(H_intercept, 触发 need_permanentization 防复发)
        """
        if exp.recovered and exp.verified:
            # 成功恢复经验 → 可复用恢复动作(不进错误模式表)
            return  # 成功经验单独沉淀, 不污染错误模式注册表
        # 恢复失败 / 未能及时恢复 → 记为错误模式(防复发)
        try:
            self.extractor.extract({
                "event_type": exp.event_type,
                "source_agent": exp.agent_id,
                "context_id": f"ris-{exp.classified}",
                "error_signature": f"{exp.classified} not recovered after {exp.attempts} attempts",
                "claimed": f"auto-recovered {exp.classified}",
                "expected": "recovered + verified",
                "actual": f"not recovered (attempts={exp.attempts}, verified={exp.verified})",
                "constraint_text": f"当 {exp.event_type} 发生后若未能一次恢复, 需升级恢复手段并验证通过",
                "severity": "🔴" if exp.attempts >= 3 else "🟡",
                "category": exp.category,
                "timestamp": exp.extracted_at,
            })
        except Exception:
            pass  # 注册表沉淀失败不致命, JSONL 仍保留原始经验

    # ── 生成 LAO 可复用锚点(供 experience_matching 检索) ──────────────────
    def to_lao_anchors(self) -> List[Any]:
        """把已沉淀的恢复经验转成 LAO 锚点(复用 ExperienceExtractor.auto_extract_anchors)。

        供 upper layer(如 Melody / experience_matching)检索复用。
        """
        if self.extractor is None or not hasattr(self.extractor, "auto_extract_anchors"):
            return []
        # 成功恢复经验 → success 计数(min 阈值 3 才成锚点·对齐既有规则)
        success_counts: Dict[str, int] = {}
        for exp in self._load_store():
            if exp["recovered"] and exp["verified"]:
                key = f"[{exp['event_type']}] {exp['classified']} auto-recovered (method={exp['recovery_method']})"
                success_counts[key] = success_counts.get(key, 0) + 1
        try:
            return self.extractor.auto_extract_anchors(success_counts=success_counts)
        except Exception:
            return []

    # ── 持久化 ────────────────────────────────────────────────────────────
    def _append_store(self, exp: RecoveryExperience) -> None:
        with self.store_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(exp.to_dict(), ensure_ascii=False) + "\n")

    def _load_store(self) -> List[Dict[str, Any]]:
        if not self.store_path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with self.store_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def recovery_experiences(self) -> List[RecoveryExperience]:
        """查询全部已沉淀恢复经验。"""
        return [RecoveryExperience(**r) for r in self._load_store()]

    def stats(self) -> Dict[str, Any]:
        """恢复经验台账统计。"""
        recs = self._load_store()
        return {
            "total": len(recs),
            "recovered": sum(1 for r in recs if r.get("recovered") and r.get("verified")),
            "failed": sum(1 for r in recs if not r.get("recovered")),
            "by_event": _group_by(recs, "event_type"),
        }


def _group_by(recs: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in recs:
        k = r.get(key, "unknown")
        out[k] = out.get(k, 0) + 1
    return out


# ── 便捷入口: Recovery Executor 完成后一行触发 ───────────────────────────
def extract_recovery_experience(result: Any, recovery_method: str = "") -> RecoveryExperience:
    """由 Tristan 的 Recovery Executor 在恢复完成后调用, 一行提取恢复经验。

    示例:
      result = recovery_engine.run(...)
      if result.recorded:
          extract_recovery_experience(result, recovery_method="L2_hard")
    """
    extractor = RiskExperienceExtractor()
    return extractor.extract_from_recovery(result, recovery_method)


__all__ = [
    "RiskExperienceExtractor",
    "RecoveryExperience",
    "extract_recovery_experience",
    "_EVENT_TO_CATEGORY",
]
