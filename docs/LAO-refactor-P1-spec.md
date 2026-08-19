# LAO 架构重构 · P1 代码规格 · 出站检查4步 + 回站验收4步

> **版本:** v1.0 | **日期:** 2026-08-19 15:10 CST
> **依据:** LAO 架构产品规格 v1.0 第一、二节（Shuyu·14:10）+ 创始人 13:22 施工令
> **执行:** Tristan 落盘（Qoder 沙箱写权限受限·既定模式）
> **约束:** 仅标准库 · docstring · 模块头 `# v3.5.2-laorefactor: P1` · fail-open · 不重写现有代码

---

## 一、总览

```
Runtime构建请求
  ↓
LAO出站检查(OutboundChecker·规格1.1状态机)
  · 经验匹配(match·P0已建)
  · 命中率预测(cognitive_anchor.predict_cache_hit·新建)
  · 成本红线(recovery_budget.check_budget·扩展)
  · Token合理性(cost_intelligence.check_token_efficiency·新建)
  ↓ 通过
Runtime直连阿里云(结构不变)
  ↓
LLM返回
  ↓
LAO回站验收(InboundValidator·规格1.2状态机)
  · 事实依据(reality_check.verify_facts·复用)
  · 认知一致(cognitive_system.check_consistency·新建)
  · 成本核算(cost_intelligence.settle_and_log·新建)
  · 经验萃取(experience_extractor.extract·P0-2已建)
  ↓ 通过
Runtime交付用户
```

**退回重做**（规格 1.3）：retry_counter（P0-2 已建）· 事实编造/严重矛盾 → REJECT → retry<3 退回 Runtime 重发 · ≥3 降级 Flash 交付。

---

## 二、模块 1: lao_acceptance.py（编排器·新建）

**路径:** `lao/effect_anchored/lao_acceptance.py`

```python
class OutboundDecision:
    """出站检查结果。"""
    passed: bool                 # 是否放行
    steps: List[Dict]            # 每步结果[{step, status, detail}]
    downgrade: Optional[str]     # 建议降级(pro→flash)或 None
    warnings: List[str]          # 非阻塞标记

class OutboundChecker:
    """出站检查器(规格 1.1 状态机·fail-open 放行)。"""

    MAX_OPTIMIZE_ROUNDS = 2      # 优化最多2轮·第3轮直接通过

    def __init__(self, matcher=None, anchor_store=None,
                 budget: RecoveryBudget = None,
                 cost: CostIntelligence = None):
        ...

    def check(self, request: Dict) -> OutboundDecision:
        """出站 4 步:
        Step1 经验匹配(matcher.match)
        Step2 命中率预测(anchor_store.predict_cache_hit)
        Step3 成本红线(budget.check_budget)
        Step4 Token合理性(cost.check_token_efficiency)
        fail-open: 任何异常 → passed=True(不阻塞)
        """

class InboundDecision:
    """回站验收结果。"""
    passed: bool
    reject: bool                 # REJECT_RESPONSE
    quality_grade: str           # verified/pending/disputed
    steps: List[Dict]
    warnings: List[str]

class InboundValidator:
    """回站验收器(规格 1.2 状态机·fail-open 放行)。"""

    def __init__(self, reality=None, cognitive=None,
                 cost=None, extractor=None, retry_counter=None):
        ...

    def validate(self, request: Dict, response: Dict) -> InboundDecision:
        """回站 4 步:
        Step1 事实依据(reality.verify_facts)
        Step2 认知一致(cognitive.check_consistency)
        Step3 成本核算(cost.settle_and_log)
        Step4 经验萃取(extractor.extract)
        事实编造/严重矛盾 → reject=True(调用方决定退回)
        """
```

**request 结构**（与经验萃取一致）:
```python
{
  "request_id": str,
  "request_features": {"task_text", "task_type", "agent_id", "model_used",
                       "provider_used", "context_tokens"},
  "response_features": {"response_text", "response_tokens", "cache_hit", "actual_cost"},
  "verification": {"fact_verified", "cognitive_consistent", "quality_grade", "retry_count"},
}
```

---

## 三、模块 2: cost_intelligence.py（新建）

**路径:** `lao/effect_anchored/cost_intelligence.py`

```python
class CostIntelligence:
    """成本智能(规格 1.1 Step4 + 1.2 Step3 + Zeus LAO-C2 期望成本路由)。"""

    MODEL_COST_PER_1K = {        # 元/1K tokens(估算·可配)
        "pro": 0.003,            # Pro 模型
        "flash": 0.001,          # Flash 模型(便宜3x)
    }

    def check_token_efficiency(self, request: Dict) -> Dict:
        """Step4 Token合理性: 能用Flash别用Pro。
        返回 {"status": "ok"/"warning", "detail": str}
        - 若 task_type=simple/chat 且 model=pro → warning(建议flash)
        - context_tokens 超阈值(>8000) → warning(砍冗余)
        """

    def settle_and_log(self, request: Dict, response: Dict) -> Dict:
        """Step3 成本核算: 记录命中/未命中·实际token·实际花费·更新统计。
        返回 {"status": "ok", "cost": float, "cache_hit": bool, ...}
        """

    def expected_cost(self, request: Dict) -> float:
        """Zeus LAO-C2 期望成本路由: 预测本次调用成本。
        expected = context_tokens/1000*rate + response_tokens/1000*rate
        """
```

---

## 四、模块 3: cognitive_anchor.py 扩展（predict_cache_hit·Stella 修正 1）

**路径:** `lao/effect_anchored/cognitive_anchor.py`（追加方法·不重写）

```python
class CognitiveAnchorStore:
    def predict_cache_hit(self, trigger: str) -> Dict:
        """Stella 修正 1: 新建方法(非已有方法改名)。
        预测该 trigger 是否命中缓存:
        - query(trigger) 返回锚点 → 有历史 → 预测命中(cache_hit=True·confidence)
        - 无锚点 → 预测不命中(cache_hit=False)
        返回 {"cache_hit": bool, "confidence": float, "anchors": n}
        """
```

---

## 五、模块 4: recovery_budget.py 扩展（check_budget）

**路径:** `lao/effect_anchored/recovery_budget.py`（追加方法·不重写）

```python
class RecoveryBudget:
    def check_budget(self, expected_cost: float) -> Dict:
        """Step3 成本红线: 预算内 → 通过 · 超预算 → 建议降级。
        返回 {"status": "ok"/"overrun", "budget_remaining": float,
              "suggest_downgrade": bool, "detail": str}
        """
```

---

## 六、测试用例（P1 验收）

**测试文件:** `tests/test_lao_acceptance.py`（新建）

| # | 用例 | 断言 |
|:--|:--|:--|
| A1 | 出站全通过(无经验/预算足/token合理) | passed=True·4步全ok |
| A2 | 出站经验命中 direct_return | Step1 status=direct_return |
| A3 | 出站预算超 → 建议降级 | downgrade=flash·passed=True |
| A4 | 出站 token 不合理(pro+简单任务) | warning·passed=True |
| A5 | 出站 fail-open(异常) | passed=True·不抛 |
| A6 | 回站全通过 | passed=True·quality_grade=verified |
| A7 | 回站事实编造 | reject=True·warnings含事实 |
| A8 | 回站认知严重矛盾 | reject=True |
| A9 | 回站成本核算+经验萃取 | settle ok·extract 写入经验库 |
| A10 | 回站 fail-open | passed=True·不抛 |

**测试文件:** `tests/test_cost_intelligence.py`（新建）

| # | 用例 | 断言 |
|:--|:--|:--|
| C1 | check_token_efficiency pro+chat | warning |
| C2 | check_token_efficiency flash+fact | ok |
| C3 | settle_and_log 记录 | cost>0·cache_hit 正确 |
| C4 | expected_cost 计算 | 公式正确 |
| C5 | 大 context 超阈值 | warning |

**测试文件:** `tests/test_predict_cache_hit.py`（新建）

| # | 用例 | 断言 |
|:--|:--|:--|
| P1 | 有锚点 → 预测命中 | cache_hit=True |
| P2 | 无锚点 → 预测不命中 | cache_hit=False |
| P3 | fail-open | 不抛 |

**测试文件:** `tests/test_check_budget.py`（新建）

| # | 用例 | 断言 |
|:--|:--|:--|
| B1 | 预算内 | status=ok |
| B2 | 超预算 | status=overrun·suggest_downgrade=True |

---

## 七、完成定义（DoD）

- [ ] `lao_acceptance.py`（模块头 `# v3.5.2-laorefactor: P1`）
- [ ] `cost_intelligence.py`（新建）
- [ ] `cognitive_anchor.py` 追加 `predict_cache_hit`
- [ ] `recovery_budget.py` 追加 `check_budget`
- [ ] 23 个测试全过（10 acceptance + 5 cost + 3 predict + 2 budget + 3 容错）
- [ ] 全量回归无破坏（348+ 现有测试）
