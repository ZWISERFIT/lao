# LAO — Agent Trust Runtime Infrastructure

Your agents are smart. **But they forget.** LAO remembers their mistakes and makes sure they never happen again.

> **🧭 一句话定位**：LAO 是 LLM 到执行之间的"人性校准层"——让每个 Agent 装上之后，不再像机器一样**忘事**和**胡说**，而是像人一样**靠谱**。更关键的是：**把每个错误铸成不可绕过的门，让组织从不重犯。**

![build](https://img.shields.io/badge/build-passing-brightgreen)
![license](https://img.shields.io/badge/license-Apache%202.0-blue)
![py](https://img.shields.io/badge/python-3.9%2B-blue)

---

## 🔍 See it in action（Trust Casebook）

> **5 个真实错误，24 小时内，LAO 把它们全部铸成了不可绕过的门。**

→ [打开 Trust Casebook](https://github.com/ZWISERFIT/lao/blob/main/trust-events/README.md)

不是虚构的演示，是真实的失败与修复：Agent 报"无法读图"却没先查技能库 → 铸成 **C005 能力探测门**；HTML 审阅 URL 两次用错端口 → 铸成 **A-OUTPUT-004 端口锁定**；纸面规则拦不住同模式错误 → 铸成 **C005-2 URL 三重校验门**。

**原则：** *错误不会降低信任，隐藏错误才降低信任。*

---

## ✨ 装前 vs 装后（30 秒看懂价值）

| | 装 LAO **之前**（裸 LLM） | 装 LAO **之后** |
|:--|:--|:--|
| **用户说「下周会来训练」** | Agent 当场记住，上下文窗口滚动后 → 忘了 | LAO 把这句话沉淀为行为记录 → 30 天后**还记得**（只是概率衰减） |
| **用户说「我想续一年」** | Agent 可能哪天突然推荐别的 | LAO 记录意图，D+3 到期自动提醒，兑现/失信影响后续信任 |
| **LLM 想「胡说」** | 靠 prompt 拦——但 prompt 只是 token，可以被覆盖 | LAO 的确定性校验在推理空间外拦截 → 幻觉到不了用户 |

**核心差异一句话：** LLM 的「记忆」会随上下文窗口滚动而丢失；LAO 的「记忆」是不丢失的结构化行为表。前者靠运气，后者靠工程。

---

## 🚀 Quick Start（5 分钟 getting started）

```bash
pip install lao-human-calibration
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

**5 分钟你能验证：**
1. `pip install lao-human-calibration` —— 秒装，零外部依赖
2. `watch("用户下周会来训练")` → `state()` 里能看到意图被记住 → `follow_through_prob` 给你履约判断
3. 你的记忆保持率直接看得到 —— 不是"应该记得"，是**实测记得**

---

## Demo（3 分钟看懂 LAO 在做什么）

装了以后复制这段就能自己跑一遍完整流程（都已验证可运行）：

```python
from lao import LAOAgent
ai = LAOAgent()                                # ① 启动
ai.watch("member_0421", "用户下周会来训练")    # ② 记录一条用户意图
s = ai.state("member_0421")                    # ③ 看它是否被记住
p = ai.predict("member_0421")                  # ④ 拿到履约概率
print("记忆保持:", s)
print("履约概率:", p.get("follow_through_prob"))
```

你会看到：意图被沉淀进行为表、不被上下文窗口冲掉，`predict` 给你一个基于行为数据的履约概率。**这就是 LAO 装上去之后最直接的体感。**

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

## 📉 122 installs · 0% retention — we know why

> **诚实的数据：** 自 7/31 发布以来，`lao-human-calibration` 在 PyPI 获得 **473 次总下载（含镜像）**、**122 次纯净安装**——但留存接近零。

**我们清楚根因，正在修：**
- 🚶 **Onboarding 缺失**：装了"能跑"不等于"知道怎么用"——缺上手路径
- ⚡ **Activation 断层**：从"装好"到"跑通第一个价值场景"缺少引导
- 🔁 **Feedback 缺失**：用户不知道反馈路径，沉默离开

**这不是隐藏的失败，是正在修复的真实路径。** 我们希望每一位安装者都能进入 [Trust Casebook](https://github.com/ZWISERFIT/lao/blob/main/trust-events/README.md) 看到 LAO 怎么对自己诚实——这是 LAO 信任叙事的起点。完整分析：[Market Intelligence Report](https://github.com/ZWISERFIT/lao/blob/main/trust-events/README.md)。

> ⚠️ **装对包：** 我们的包是 **`lao-human-calibration`**。PyPI 上另有一个独立的 `lao` 包（SynthexCapital 的本地 preflight 工具），与 ZWISERFIT 无关——请勿混淆，`pip install lao-human-calibration` 才是 LAO。

---

## 🤝 贡献与信任

LAO 是开源的，也需要你来让它更好。**信任不是营销出来的——是透明的、可验证的。** GitHub Issues 是公开的——每一条 bug、每一次修复、每一次交流，都是信任构建的证据。

- 想贡献代码 / 文档？看 [CONTRIBUTING.md](./CONTRIBUTING.md)（有 `good first issue` 引导）
- 每个外部贡献者都会被记进 [TRUST-BUILDERS.md](./TRUST-BUILDERS.md)——你的名字会被记住
- **提交问题 / 报告 bug：** [Open an Issue](https://github.com/ZWISERFIT/lao/issues) — 我们承诺透明回复、闭环修复
- **提功能 / 参与讨论：** [GitHub Discussions](https://github.com/ZWISERFIT/ZWISERFIT/discussions)
- **想理解「为什么 Agent 会忘事」？** 看 [Technical Manifesto](https://github.com/ZWISERFIT/ZWISERFIT/discussions/5)
- **Star the repo** — 让更多人发现它：<https://github.com/ZWISERFIT/lao>
- **Try the demo** — 3 分钟自己跑一遍：<https://github.com/ZWISERFIT/lao#demo>

---

*LAO — 让 Agent 像人一样靠谱。不再忘事，不再胡说，不再重犯。*
