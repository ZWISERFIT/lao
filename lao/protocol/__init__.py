"""
LAO Protocol — 开放协议层 (P0① 三分离: protocol / open / private)
================================================================
⚖️ 本目录 = LAO 稳定开放协议契约 (Open Protocol)。

三分离设计:
- protocol/   ← 本目录: 稳定协议契约 (路由/经验/确权/授权/信任事件/策略)
               → 供任何 Agent / OS / Melody / 第三方接入, 不开源但稳定(版本化)
- open/       ← 参考实现 (Reference Implementation): 当前 lao/effect_anchored 等开源代码
- private/    ← ZWISERFIT 私有 Policy (weights.json/C-BMC/采购表): 在 zwiserfit-os 私有侧

铁律(智囊团共识·2026-08-12):
- 协议必须稳定, 实现可以迭代 (LAO v4/v5 改内部类, 接入方协议不破)
- 协议层 = 接口/结构, 不含配方 (配方在 private/Policy)
"""
