# L1 选品路由重构 v3.1 — 设计文档

> **创始人裁定**: 2026-08-11  
> **执行**: Tristan（P0修复·6Agent闭环测试通过）  
> **状态**: ✅ 已实现并提交

---

## 1. 背景与动机

### 1.1 原设计（v2.1）问题

v2.1 的路由决策采用 **固定优先级池** (`pool[0]` 为首选)：

```python
primary = pool[0]           # 固定首选, 不动态评估
fallbacks = pool[1:]        # 其余为降级链
```

**问题**:
- 池顺序是写死的手工排序, 不同 tier 可能选择次优模型
- 没有显式的质量门禁: flash 可能被用于 heavy 任务（幻觉风险）
- 成本/效率/安全三维没有结构化分离, 难以审计和调整

### 1.2 创始人纠正

> "不是等权三维最优, 而是三级顺序过滤 ——  
> **安全 > 效率 > 成本**。  
> 性价比最优 ≠ 成本最低。安全是第一门禁。"

---

## 2. 三级过滤架构

```
┌─────────────────────────────────────────────┐
│                MODEL_POOL (全量候选池)        │
│          每tier含多provider/多model选项       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  ① SAFETY GATE      │  ← 第一门禁·不可绕过
         │  quality >= gate    │
         └─────────┬───────────┘
                   │ safe candidates
                   ▼
         ┌─────────────────────┐
         │  ② EFFICIENCY       │  ← 同质量集内比效率
         │  latency排序         │
         │  credit_mode偏好     │
         └─────────┬───────────┘
                   │ efficient candidates
                   ▼
         ┌─────────────────────┐
         │  ③ COST             │  ← 同效率集内比成本
         │  前25%效率 → 低成本  │
         └─────────┬───────────┘
                   │
                   ▼
              SELECTED
```

### 2.1 ① Safety Gate（安全门禁）

**不可绕过** — 即使所有候选模型都被过滤, 也不降低底线, 而是取该 tier 质量最高的。

| tier | safety_gate | 含义 |
|------|-------------|------|
| ultra_light | 0.50 | 心跳/问候 → flash 可过 |
| light | 0.50 | 日常问答 → flash 可过 |
| cn_explain | 0.50 | 中文解释 → flash 可过 |
| medium | 0.80 | 分析/推断 → flash(0.7)被拦, 必须 pro |
| code | 0.80 | 代码生成 → flash 被拦 |
| cn_creative | 0.80 | 创意写作 → flash 被拦 |
| heavy | 0.85 | 战略分析 → 仅 pro(0.92) |
| reasoning | 0.85 | 深度推理 → 仅 pro(0.92) |

**模型质量基准**:
- `deepseek-v4-pro`: quality=0.92
- `deepseek-v4-flash`: quality=0.7

**极端情况**: 若整个池都被安全门禁拦下（quality 全部 < gate），取该 tier 质量最高的模型，不降低门禁。

### 2.2 ② Efficiency（效率筛选）

同质量集内, 按 latency 从小到大排序。

**credit_mode 偏好**:
- `"avoid"`: 过滤 credit 消费模型
- `"force"`: 强制 credit 消费（reasoning 层除外）
- `"prefer"`: 不干预

### 2.3 ③ Cost（成本优先）

取效率前 25% 的候选, 在其中选成本最低的。

成本解析: `"$input/$output"` → 取输入价比较。

---

## 3. 降级链（Fallback Chain）

降级链也过 safety gate, 确保降级不降安全:

```python
gate = self.SAFETY_GATE.get(tier, 0.50)
fallback_pool = [e for e in pool if float(e.get("quality", 0)) >= gate]
fallbacks = [f"{e['provider']}/{e['model']}"
             for e in fallback_pool if e != primary]
```

---

## 4. 与 task_classifier.py 的配合

`task_classifier.py` 负责将任务文本映射到 `tier`, `model_router.py` 负责在该 tier 内执行三级过滤。

**路由流程**:
```
task_text → TaskClassifier.classify() → tier
         → ModelRouter.route() → select_optimal(pool, tier) → RouteSelection
```

---

## 5. 测试验证

### 5.1 安全门禁测试

```
Input: tier=heavy, pool含 [flash(0.7), pro(0.92)]
Expected: flash 被 safety gate(0.85) 过滤, 选择 pro
```

### 5.2 效率测试

```
Input: tier=light, pool含多个同质量flash(0.7)
Expected: 选 latency 最低的 flash
```

### 5.3 成本测试

```
Input: tier=medium, pool含 [deepseek-pro(0.92,$2.20), novarouteai-pro(0.92,$2.20)]
Expected: 同质量同效率 → 选成本最低
```

### 5.4 降级链安全测试

```
Input: tier=heavy, fallback_pool含 flash(0.7)+pro(0.92)
Expected: fallback_chain 不含 flash(被 safety gate 过滤)
```

---

## 6. 向后兼容

- `MODEL_POOL` 数据结构增加 `quality`/`latency` 字段, 旧代码仅访问 `model`/`provider`/`cost`/`credit` 不受影响
- `route()` 返回值 `RouteSelection` 不变
- `route_with_budget()` 保持不变（预算约束场景使用固定选择）
- `explain_route()` 保持不变

---

## 7. 提交记录

| commit | 描述 |
|--------|------|
| `c49b849` | L1选品路由重构: 安全>效率>成本三级过滤 |
| `4a25da0` | ⑤多模型切换BUG: refuse后不再re-ask |

---

*设计文档 v1.0 · 2026-08-11 · Tristan*
