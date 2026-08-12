# LAO 三分离边界 (Protocol / Implementation / Private Policy)

> 创始人终审(2026-08-13 06:23·方案D+批准) · P0⑥ 需求#5
> DRI: Tristan · Stella复验 · Shuyu验收

## 三分语义模型（创始人·比三方更精准·必须固化）

| 层 | 语义 | 对应 | 归属 |
|:---|:---|:---|:---|
| **Protocol** | What（是什么契约） | 声明式契约 · `lao.Constraint` · `lao/protocol/` | **开源** |
| **Implementation** | How（怎么执行） | 执行引擎 · `SelfHealingConstraint` · `ConstraintAdapter` | **闭源** |
| **Private Policy** | Why/When（何时为何） | 私有策略 · weights/采购表/C-BMC/自愈配方 | **闭源** |

三者**不能再混**（创始人铁律）。

## 一句话边界铁律（创始人）

> **公开「怎么证明」，闭源「怎么自愈得更聪明」。**

### 仍保持公开协议（可验证能力·开源）
- 验证结果、验证接口（verify）
- TrustEvent（信任事件）
- fingerprint / provenance（可验证能力）
- 声明式 `Constraint` 契约（What）
- severity 映射（公开映射表）

### 真正留在 ZWISERFIT private（闭源）
- 自愈算法（check/auto_fix 策略）
- auto_fix 逻辑、promotion/rollback policy
- 私有阈值（成本/质量/降级）
- evolution policy（演化策略）
- SQLite 真实经验/规则配方
- Founder Cognitive Policy（认知策略权重）

## 架构分层（正式边界·不允许长期直接互调）

```
lao.Constraint (What·开源契约)
        ↓
ConstraintAdapter (schema翻译·severity映射·版本兼容·provenance·TrustEvent)
        ↓
SelfHealingConstraint (How·闭源执行引擎)
```

- Adapter 是**正式架构边界**
- 第三方未来可实现自己的 Constraint Engine，不被 LAO Core 锁死
- LAO Core 只暴露 Protocol 契约（What + 可验证能力），不暴露 How/Policy

## 落地点映射

| 层 | 落点 | 开源状态 |
|:---|:---|:---|
| Protocol | `lao/protocol/` + `lao/evolution/constraint_generator.py`(Constraint) | 🔓 开源 |
| Implementation | `lao/effect_anchored/evolution/`(SelfHealingConstraint + Adapter) | 🔒 闭源 |
| Private Policy | `zwiserfit-os/`(routing_policy/weights) + 私有配方 | 🔒 闭源 |

---
*P0⑥ #5 三分离边界 · 2026-08-13 · 本文件为边界锁定文档（非代码）*
