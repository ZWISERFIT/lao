# LAO — Agent Experience Infrastructure

> Your Agent gets smarter with every operation. Not because the model improved — because Experience accumulated.

---

## What is LAO?

LAO is infrastructure that turns Agent operations into reusable **Experience**. It sits between your LLM and your Runtime — open source, Apache 2.0, zero telemetry.

Every operation leaves behind structured, traceable history. Successes get cached. Failures become permanent constraints. Over time, your Agent becomes measurably more reliable.

→ **Understand Experience:** [zwiserfit.cn/v2/experience](https://zwiserfit.cn/v2/experience/)
→ **See the evidence:** [zwiserfit.cn/v2/evidence](https://zwiserfit.cn/v2/evidence/)

---

## This is not a demo.

LAO is built from 7 years of running a real fitness gym in Dongguan, China. Every feature exists because a real operation hit a real problem:

- **Token waste** — cache-miss requests are 8.5% of volume but 56% of cost. We measured it. [See the evidence →](https://zwiserfit.cn/v2/evidence/)
- **Memory loss** — every session starts from zero. Experience fixes that.
- **Hallucination** — intent lock + experience anchoring. LAO optimizes how, never what you meant.

We don't show slideshows. We show receipts.

---

## Verify everything yourself.

- **Code:** Apache 2.0. Read it, fork it, audit it. `pip install git+https://github.com/ZWISERFIT/lao.git`
- **Data:** every claim links to a source. No source = no claim.
- **Protocol:** the [Experience Protocol](https://zwiserfit.cn/v2/experience-protocol/) is a frozen specification — you can reproduce any Experience record.

---

## Co-build with us.

This isn't just our infrastructure — it's yours too.

- **First contribution:** see [CONTRIBUTING.md](./CONTRIBUTING.md) for easy-first-issues
- **Trust builders:** every Issue, PR, and discussion is recorded in [TRUST-BUILDERS.md](./TRUST-BUILDERS.md)
- **Narrative:** read [Why this matters](#why-this-matters) below

→ **View the website:** [zwiserfit.cn](https://zwiserfit.cn)
→ **Read the technical brief:** [zwiserfit.cn/v2/for-agents](https://zwiserfit.cn/v2/for-agents/)

---

## 3 lines. 3 minutes. See the difference.

```bash
pip install lao-human-calibration
```

```bash
# 初始化 LAO runtime
lao init

# 记录一个 Trust Event（经验原子入口）
lao trust-event --text "客户投诉退款600元，创始人决定人工介入"

# 查看锚点状态
lao status
```

---

## Architecture modules · 模块一览

`lao/core/` 与 `lao/effect_anchored/` 合仓为一个完整包：

| 引擎 | 模块 | 作用 |
|:--|:--|:--|
| **L1 智能路由** | `routing/model_router.py` | 三 provider（DeepSeek/TokenPlan/NovaRouteAI）故障转移，跨 provider 先验证模型存在 |
| **L2 认知锚点** | `cognitive_anchor.py` | Fact→Decision→Cognitive 三层递进 |
| **L3 经验原子** | `evolution/atom_engine.py` | Trust Event → Atom → Anchor → Future Protection 闭环 |
| **L2 偏好防火墙** | `preference_firewall.py` | 效率优化允许，身份/价值表达禁止 |
| **经验图** | `experience_graph.py` | similar_to / caused_by / derived_from 关系网络 |
| **反馈总线** | `feedback_bus.py` | L3经验→L2锚点→L1路由 自动闭环回流 |
| **经验契约** | `experience_contract.py` | 经验共享安全边界，防跨域污染 |
| **经验检索** | `experience_matching.py` | `retrieve_verified_experience()` 带权限/契约过滤的已验证经验检索 |

---

## Open-source boundary · 开源边界

The **recipe** is public (Apache 2.0): routing, anchoring, the experience loop, and their regression tests. Production **tuning ratios** stay with the maintainer. LAO runs standalone and makes **zero LLM calls of its own** — you keep your own model keys; we take no cut of your token spend.

配方（代码）全公开，配比（调优参数）保留。LAO独立可运行、自身零LLM调用——模型密钥留在你自己手里，我们不从你的token花费中抽成。

---

## 发布范围说明（v3.6.0-r3）

本仓库开源的是**配方**：LAO 的路由、认知锚点、经验闭环，以及它们的回归测试。**LAO 独立可运行，不依赖任何其他 ZWISERFIT 组件。**

以下内容**不在本次开源范围内**：

- **RIS（Agent Runtime Reliability Layer）**：运行时可靠性层，是**独立产品**，将另行发布，不含在本仓库内。LAO 与 RIS 之间通过一个只读的共享 JSON 文件契约通信（`RIS_BRIDGE_FILE`，默认 `~/shared/state/ris-bridge.json`）；该文件不存在时 LAO 全功能正常运行，仅不获得来自 RIS 的 provider 健康信号。本仓库保留的是 LAO 一侧的消费者实现（`ris_bridge_consumer.py`、`ris_health_gate.py`）与其回归测试，不含 RIS 本身。
- **排序权重数值**（`lao/effect_anchored/weights.json`）：加载接口与应用方式开源，权重数值不入库。文件缺失时回退为均权，功能可用但不含我们的认知偏置。
- **监控与门禁**：成本漂移告警、数据新鲜度告警、软启动门禁、单实例托管（systemd）、部署后版本校验、账单对账，均为运营侧脚本，开源版暂不包含。
- **内部运行数据**：事故记录、经验库、运行态快照、密钥与环境变量。

也就是说：开源版**包含产品代码内的保险丝**——重试上限与任务级 token 熔断、4xx 真实状态码透传、配对感知的上下文剪枝，以及 LAO 侧的桥数据陈旧保护（`RIS_BRIDGE_STALE_S`，默认 180 秒，桥文件超时即视为无信号并放行，fail-open）。**provider 隔离冷却（600 秒）与 provider 级数据时效过滤（900 秒）实现在 RIS 内，不随本仓库发布**；自建部署若需要这两层，需自备 provider 健康监控，或等 RIS 发布。运营侧的监控与门禁同样不包含，请自备监控。

本次为**带保险丝的发布**，不声称零缺陷。

### Scope of this release (English)

This repository open-sources the *recipe*: LAO routing, cognitive anchoring, the experience loop, and their regression tests. **LAO runs standalone and does not depend on any other ZWISERFIT component.**

Not included in this release:

- **RIS (Agent Runtime Reliability Layer)** — a **separate product**, shipped separately, not contained here. LAO talks to RIS only through a read-only shared JSON file contract (`RIS_BRIDGE_FILE`, default `~/shared/state/ris-bridge.json`). When that file is absent, LAO runs at full function and simply receives no provider-health signal from RIS. What this repo keeps is the LAO-side consumer (`ris_bridge_consumer.py`, `ris_health_gate.py`) and their regression tests — not RIS itself.
- **Ranking weight values** (`lao/effect_anchored/weights.json`) — the loading interface is open, the values are not committed. When the file is absent, the engine falls back to uniform weights: usable, but without our tuning.
- **Monitoring and gating** — cost-drift alerts, data-freshness alerts, soft-start gating, single-instance supervision (systemd), post-deploy version verification, and billing reconciliation are ops-side scripts and are not shipped here.
- **Internal runtime data** — incident records, experience stores, runtime snapshots, secrets and environment files.

In short: the fuses that live in the product code are included — retry ceiling and per-task token cutoff, real 4xx status pass-through, pair-aware context pruning, and LAO-side bridge staleness protection (`RIS_BRIDGE_STALE_S`, default 180s, fail-open). **Provider isolation cooldown (600s) and provider-level staleness filtering (900s) are implemented inside RIS and do not ship with this repository**; if you need those two layers in a self-hosted deployment, bring your own provider health monitoring or wait for the RIS release. Ops-side monitoring and gating are likewise not included — bring your own monitoring.

This is a **release with fuses**, not a claim of zero defects.

---

## Documentation

详见 `docs/` 与各模块 docstring。

> 📁 **历史存档说明 / Archive note**：仓库根目录 `archive/` 内为早期施工过程文档（工单、审计快照），仅作历史记录保留，**口径以本 README 与最新版本为准**。Early process documents (work orders, audit snapshots) are kept under `archive/` for the record; this README and the latest release are the source of truth.

## Contributing

遵循 [CONTRIBUTING.md](CONTRIBUTING.md)，欢迎 PR。

## License

Apache-2.0（以仓库根目录 LICENSE 文件为准）

## 隐私（Privacy）

**本版本不做任何遥测，导入与运行不产生对外网络回传。** 历史版本曾含一条默认开启的遥测（域名从未解析、无数据回收），已在 v3.6.0-r3 移除并如实披露——详见 [PRIVACY.md](PRIVACY.md)。
