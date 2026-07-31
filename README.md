# LAO — The Human-Calibration Layer for Your Agents

> **🧭 一句话定位**：LAO 是 LLM 到执行之间的"人性校准层"——让每个 Agent 装上之后，不再像机器一样**忘事**和**胡说**，而是像人一样**靠谱**。

![build](https://img.shields.io/badge/build-passing-brightgreen)
![license](https://img.shields.io/badge/license-Apache%202.0-blue)
![py](https://img.shields.io/badge/python-3.9%2B-blue)

---

## ✨ 装前 vs 装后（30 秒看懂价值）

| | 装 LAO **之前**（裸 LLM） | 装 LAO **之后** |
|:--|:--|:--|
| **用户说「下周会来训练」** | Agent 当场记住，上下文窗口滚动后 → 忘了 | LAO 把这句话沉淀为行为记录 → 30 天后**还记得**（只是概率衰减） |
| **用户说「我想续一年」** | Agent 可能哪天突然推荐别的 | LAO 记录意图，D+3 到期自动提醒，兑现/失信影响后续信任 |
| **LLM 想「胡说」** | 靠 prompt 拦——但 prompt 只是 token，可以被覆盖 | LAO 的确定性校验在推理空间外拦截 → 幻觉到不了用户 |

**核心差异一句话：** LLM 的「记忆」会随上下文窗口滚动而丢失；LAO 的「记忆」是不丢失的结构化行为表。前者靠运气，后者靠工程。

---

## 🚀 3 行代码 · 3 分钟见效

```bash
pip install lao
```

```python
from lao import LAOAgent
ai = LAOAgent()                                    # ① 启动，一行
ai.watch("member_0421", "用户下周会来训练")          # ② 记录行为
ai.watch("member_0421", "用户来店训练了45分钟")      #     （多条行为累积）
result = ai.predict("member_0421")                  # ③ 预测下个行为
```

**predict 直接给你履约判断（来自 7 年门店数据教出的履约概率）：**
```json
{
  "follow_through_prob": 0.70,                            // 履约概率 0-1
  "next_action_prob": {"action_checkin": 1.0},
  "suggestion": "该用户履约率高，可放心推进续费/长期计划",
  "active_intentions": ["用户下周会来训练"]
}
```

> 🔧 **发布形态说明：** `LAOAgent` 是开箱即用的门面——`watch` 一句自然语言进去，行为自动存入模型；`predict` 给履约概率 + 建议。**关键：这个履约概率是 7 年实体门店运营萃取的 BMC 行为模式教出来的**——不是猜的，是有行为数据的。`state()` 额外暴露流失风险/续费概率。底层 `IntentionDecayModel` 接口也同时导出（见文档）。

**3 分钟你能验证：**
1. `pip install lao` —— 秒装，零外部依赖
2. `watch("用户下周会来训练")` → `state()` 里能看到意图被记住 → `follow_through_prob` 给你履约判断
3. 你的记忆保持率直接看得到 —— 不是"应该记得"，是**实测记得**

---

## 🧠 你得到的三层能力

- **记忆保持** — 用户说过的话沉淀为行为轨迹，跨 session 不丢（单元测试保持率 100% · 生产抽样见 Release Notes）
- **意图衰减** — 承诺随真实时间衰减，兑现率高的人 λ 小、记得久；爱放鸽子的 λ 大、淡得快
- **胡说拦截** — 确定性校验层把 LLM 输出挡在推理空间外，幻觉拦截率与精确率可量化

---

## 🔒 开源但架构锁

- **引擎全开源**（Apache 2.0）——记忆模型、衰减引擎、校验函数，全部可审计
- **初始行为模式权重不开源** —— 7 年实体门店运营萃取的 BMC 权重是核心资产
- 这是我们的护城河：代码能抄，**行为模式偏差抄不走**

---

## 📊 数据透明度

**来自 120 天真实生产环境**（真实门店 · 9 Agent · 120 天）：

| 指标 | 值 | 数据类型 |
|:--|:--|:--|
| 生产环境 | 真实门店 · 9 Agent · 120 天 | 生产 |
| 引擎已在真实运营中持续运行 | 是 | 生产 |

**当前测试基准**（便于你评估；正式量化数据随 Release Notes 随版本发布）：

| 指标 | 值 | 数据类型 |
|:--|:--|:--|
| 记忆保持率 | 100%（10/10） | 单元测试基准 · 生产抽样确认见 Release Notes |
| 幻觉拦截 | 生产环境实测 · 细节见 Release Notes | 生产 |

> ⚖️ 我们刻意区分「生产经验」和「测试基准」——不把模拟数据包装成生产数据。真实数据随社区使用持续累积，随版本实时公布。

---

## 🧭 下一步

- [**Star the repo**](https://github.com/ZWISERFIT/zwiserfit-ai-store-manager) — 让更多人发现它
- [**Try the demo**](https://github.com/ZWISERFIT/zwiserfit-ai-store-manager#demo) — 3 分钟自己跑一遍
- [**Open an Issue**](https://github.com/ZWISERFIT/zwiserfit-ai-store-manager/issues) — 反馈问题，我们承诺闭环修复
- 想知道「为什么 Agent 会忘事」？看 [Technical Manifesto](https://github.com/ZWISERFIT/ZWISERFIT/discussions/5)

---

*LAO — 让 Agent 像人一样靠谱。不再忘事，不再胡说。*
