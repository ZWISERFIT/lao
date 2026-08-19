# v3.5.2-laorefactor: P1
"""
LAO Acceptance — 出站检查 4 步 + 回站验收 4 步（产品规格 v1.0 第一、二节）
=============================================================================

Runtime 构建请求 → LAO 出站检查（用户利益方·规格 1.1 状态机）:
    Step1 经验匹配     (ExperienceMatcher.match — P0 已建)
    Step2 命中率预测   (CognitiveAnchorStore.predict_cache_hit — P1 新建)
    Step3 成本红线     (RecoveryBudget.check_budget — P1 扩展)
    Step4 Token合理性  (CostIntelligence.check_token_efficiency — P1 新建)
    → 通过 → Runtime 直连阿里云（结构不变·LAO 不碰转发）

LLM 返回 → LAO 回站验收（规格 1.2 状态机）:
    Step1 事实依据     (RealityCheckEngine.evaluate — 复用)
    Step2 认知一致     (DeterministicCognitiveSystem.check_consistency — 新建)
    Step3 成本核算     (CostIntelligence.settle_and_log — P1 新建)
    Step4 经验萃取     (ExperienceExtractor.extract — P0-2 已建)
    → 通过 → Runtime 交付用户

退回重做（规格 1.3）: retry_counter（P0-2 已建）· 事实编造/严重矛盾 → reject
    · retry<3 → 退回 Runtime 重发 · ≥3 → 降级 Flash 交付。

铁律: LAO 只碰检查不碰转发 · fail-open（任何异常放行·不阻塞 Runtime）。
约束: 仅标准库 · docstring · 不重写现有代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 出站检查
# ---------------------------------------------------------------------------

@dataclass
class OutboundDecision:
    """出站检查结果（规格 1.1）。"""

    passed: bool = True
    steps: List[Dict[str, Any]] = field(default_factory=list)
    downgrade: Optional[str] = None      # 建议降级(pro→flash)或 None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化（供日志/审计）。"""
        return asdict(self)


class OutboundChecker:
    """出站检查器（规格 1.1 状态机·fail-open 放行）。"""

    MAX_OPTIMIZE_ROUNDS = 2   # 优化最多 2 轮·第 3 轮直接通过

    def __init__(self, matcher: Any = None, anchor_store: Any = None,
                 budget: Any = None, cost: Any = None):
        """初始化。缺省组件 → 对应步骤跳过（标记 not_configured·不阻塞）。

        Args:
            matcher: ExperienceMatcher（P0）· None → Step1 跳过
            anchor_store: CognitiveAnchorStore（predict_cache_hit）· None → Step2 跳过
            budget: RecoveryBudget（check_budget）· None → Step3 跳过
            cost: CostIntelligence（check_token_efficiency/expected_cost）· None → Step4 跳过
        """
        self.matcher = matcher
        self.anchor_store = anchor_store
        self.budget = budget
        self.cost = cost

    def check(self, request: Dict[str, Any]) -> OutboundDecision:
        """出站 4 步检查。fail-open: 任何异常 → passed=True（不阻塞）。"""
        decision = OutboundDecision()
        try:
            feat = request.get("request_features") or {}
            # Step1: 经验匹配
            if self.matcher is not None:
                try:
                    mr = self.matcher.match(feat)
                    decision.steps.append({
                        "step": "experience_match",
                        "status": mr.action,
                        "detail": f"confidence={mr.confidence}",
                    })
                    if mr.action == "direct_return":
                        decision.steps[-1]["detail"] += " → cache direct return candidate"
                except Exception as exc:
                    decision.steps.append({"step": "experience_match",
                                           "status": "fail-open", "detail": str(exc)})
            else:
                decision.steps.append({"step": "experience_match",
                                       "status": "not_configured"})

            # Step2: 命中率预测
            if self.anchor_store is not None:
                try:
                    task_text = str(feat.get("task_text") or "")
                    pred = self.anchor_store.predict_cache_hit(task_text)
                    decision.steps.append({
                        "step": "cache_hit_predict",
                        "status": "hit" if pred.get("cache_hit") else "miss",
                        "detail": f"confidence={pred.get('confidence')} anchors={pred.get('anchors')}",
                    })
                except Exception as exc:
                    decision.steps.append({"step": "cache_hit_predict",
                                           "status": "fail-open", "detail": str(exc)})
            else:
                decision.steps.append({"step": "cache_hit_predict",
                                       "status": "not_configured"})

            # Step3: 成本红线
            if self.budget is not None:
                try:
                    exp_cost = self.cost.expected_cost(request) if self.cost else 0.0
                    br = self.budget.check_budget(exp_cost)
                    decision.steps.append({
                        "step": "budget_check",
                        "status": br.get("status"),
                        "detail": br.get("detail", ""),
                    })
                    if br.get("status") == "overrun" and br.get("suggest_downgrade"):
                        decision.downgrade = "flash"
                        decision.warnings.append(
                            "budget overrun → downgrade to flash suggested")
                except Exception as exc:
                    decision.steps.append({"step": "budget_check",
                                           "status": "fail-open", "detail": str(exc)})
            else:
                decision.steps.append({"step": "budget_check",
                                       "status": "not_configured"})

            # Step4: Token 合理性
            if self.cost is not None:
                try:
                    tr = self.cost.check_token_efficiency(request)
                    decision.steps.append({
                        "step": "token_efficiency",
                        "status": tr.get("status"),
                        "detail": tr.get("detail", ""),
                    })
                    if tr.get("status") == "warning":
                        decision.warnings.append(tr.get("detail", "token warning"))
                except Exception as exc:
                    decision.steps.append({"step": "token_efficiency",
                                           "status": "fail-open", "detail": str(exc)})
            else:
                decision.steps.append({"step": "token_efficiency",
                                       "status": "not_configured"})

            # 汇总: 全部非阻塞 → passed=True（LAO 只标记不拦截·规格设计）
            decision.passed = True
            return decision
        except Exception:
            # 顶层 fail-open: 任何未预期异常 → 放行
            decision.passed = True
            decision.warnings.append("outbound check fail-open")
            return decision


# ---------------------------------------------------------------------------
# 回站验收
# ---------------------------------------------------------------------------

@dataclass
class InboundDecision:
    """回站验收结果（规格 1.2）。"""

    passed: bool = True
    reject: bool = False             # REJECT_RESPONSE（调用方决定退回）
    quality_grade: str = "verified"
    steps: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """序列化（供日志/审计）。"""
        return asdict(self)


class InboundValidator:
    """回站验收器（规格 1.2 状态机·fail-open 放行）。"""

    def __init__(self, reality: Any = None, cognitive: Any = None,
                 cost: Any = None, extractor: Any = None,
                 retry_counter: Any = None):
        """初始化。缺省组件 → 对应步骤跳过（不阻塞）。

        Args:
            reality: RealityCheckEngine（evaluate）· None → Step1 跳过
            cognitive: DeterministicCognitiveSystem（check_consistency）· None → Step2 跳过
            cost: CostIntelligence（settle_and_log）· None → Step3 跳过
            extractor: ExperienceExtractor（extract）· None → Step4 跳过
            retry_counter: RetryCounter · None → 不管理退回
        """
        self.reality = reality
        self.cognitive = cognitive
        self.cost = cost
        self.extractor = extractor
        self.retry_counter = retry_counter

    def validate(self, request: Dict[str, Any],
                 response: Dict[str, Any]) -> InboundDecision:
        """回站 4 步验收。fail-open: 任何异常 → passed=True（不阻塞）。"""
        decision = InboundDecision()
        verification = request.get("verification") or {}
        try:
            # Step1: 事实依据
            if self.reality is not None:
                try:
                    answer_id = str(request.get("request_id") or "unknown")
                    resp_text = str(
                        (response.get("response_features") or {}).get("response_text", ""))
                    ev = self.reality.evaluate(answer_id=answer_id, answer=resp_text)
                    fact_ok = bool(getattr(ev, "sourced", True))
                    verification["fact_verified"] = fact_ok
                    decision.steps.append({
                        "step": "fact_check",
                        "status": "ok" if fact_ok else "unverified",
                    })
                    if not fact_ok:
                        decision.warnings.append("fact unverified")
                except Exception as exc:
                    decision.steps.append({"step": "fact_check",
                                           "status": "fail-open", "detail": str(exc)})
            else:
                decision.steps.append({"step": "fact_check", "status": "not_configured"})

            # Step2: 认知一致
            if self.cognitive is not None:
                try:
                    resp_text = str(
                        (response.get("response_features") or {}).get("response_text", ""))
                    cr = self.cognitive.check_consistency(resp_text)
                    consistent = bool(cr.get("consistent", True))
                    verification["cognitive_consistent"] = consistent
                    decision.steps.append({
                        "step": "cognitive_check",
                        "status": "ok" if consistent else "conflict",
                    })
                    if not consistent:
                        decision.warnings.append("cognitive conflict")
                except Exception as exc:
                    decision.steps.append({"step": "cognitive_check",
                                           "status": "fail-open", "detail": str(exc)})
            else:
                decision.steps.append({"step": "cognitive_check",
                                       "status": "not_configured"})

            # 判定: 事实编造/严重矛盾 → reject
            fact_ok = bool(verification.get("fact_verified", True))
            cog_ok = bool(verification.get("cognitive_consistent", True))
            if not fact_ok or not cog_ok:
                decision.reject = True
                decision.passed = False
                retry_count = 0
                if self.retry_counter is not None:
                    rid = str(request.get("request_id") or "unknown")
                    retry_count = self.retry_counter.increment(rid)
                verification["retry_count"] = retry_count
                decision.quality_grade = "disputed" if not fact_ok else "pending"
                if retry_count >= getattr(self.retry_counter, "MAX_RETRIES", 3) \
                        if self.retry_counter is not None else False:
                    decision.warnings.append(
                        f"retry={retry_count} → downgrade flash deliver")
            else:
                verification["retry_count"] = int(
                    verification.get("retry_count", 0) or 0)
                decision.quality_grade = "verified"
                if self.retry_counter is not None:
                    rid = str(request.get("request_id") or "unknown")
                    self.retry_counter.clear(rid)

            # Step3: 成本核算
            if self.cost is not None:
                try:
                    sr = self.cost.settle_and_log(request, response)
                    decision.steps.append({
                        "step": "cost_settle",
                        "status": sr.get("status"),
                        "detail": f"cost={sr.get('cost')} cache_hit={sr.get('cache_hit')}",
                    })
                except Exception as exc:
                    decision.steps.append({"step": "cost_settle",
                                           "status": "fail-open", "detail": str(exc)})
            else:
                decision.steps.append({"step": "cost_settle", "status": "not_configured"})

            # Step4: 经验萃取
            if self.extractor is not None:
                try:
                    request["verification"] = verification
                    eid = self.extractor.extract(request)
                    decision.steps.append({
                        "step": "experience_extract",
                        "status": "ok" if eid else "skipped",
                        "detail": f"exp_id={eid}",
                    })
                except Exception as exc:
                    decision.steps.append({"step": "experience_extract",
                                           "status": "fail-open", "detail": str(exc)})
            else:
                decision.steps.append({"step": "experience_extract",
                                       "status": "not_configured"})

            # 汇总
            if not decision.reject:
                decision.passed = True
            return decision
        except Exception:
            decision.passed = True
            decision.warnings.append("inbound validate fail-open")
            return decision
