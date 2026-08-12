# ZWISERFIT Private Policy (私有策略·闭环)
P0① 三分离之 private/ 层。⚠️ 本层为 ZWISERFIT 私有·不开源·不进 GitHub。

私有策略真实位置:
- weights.json(认知检索权重·不开源)   → lao/effect_anchored/weights.json(本地,未进git)
- C-BMC(行为模式约束·不开源)          → lao/effect_anchored/evolution/.pattern_registry.json
- 真实 Provider 采购表                → zwiserfit-os/lao_adapter/routing_policy.py
- Policy 版本/签名                    → P0②(v3.3 全面落地)

接入方应通过 PolicyProtocol.get() 读取, 不直接碰本层文件。
