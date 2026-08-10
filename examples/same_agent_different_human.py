#!/usr/bin/env python3
"""
LAO 2.7 · Experience Intelligence — Demo: Same Agent, Different Human
=====================================================================

同一 LAO Agent，面对两位不同 Human 时，检索到**已验证但差异化**的经验。

核心设计（保持 LAO = Trust Layer 边界）:
  - LAO Kernel = Storage(锚点/契约) + Verification(真实验证) + Retrieval(检索)
  - 差异来自 **Human 各自的契约数据**(ExperienceContract / DecisionAnchor / 锚点),
    属于 Storage 层的产物 —— 这是 LAO 的职责。
  - LAO **不做**个人偏好推断 / 个性化适配 —— 那是 Melody 的
    Matching + Personal Adaptation 域。本 Demo 在检索层只负责:
      1) 按 human 的契约检索到「该 human 专属 + 已验证」的经验
      2) 正确标注哪些是 human-specific、哪些是 agent 全局锚定(Trust 不变)

场景:
  - Human A: Alice —— 企业付费客户 / 高信任额  (contract: 阈值高、长期召回偏好)
  - Human B: Bob  —— 个人散客 / 低信任额      (contract: 阈值低、首次召回优先)
  同一 agent "客服" 分别面向 Alice / Bob 处理「退款」触发时,
  检索到的已验证经验集不同 —— 订单闸门、人工介入条件、召回策略各不相同。

运行:  python examples/same_agent_different_human.py
依赖:  仅 stdlib + lao 包(已 import lao 子模块)。
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List

# ── LAO Kernel 组件 ──────────────────────────────────────────────────────────
from lao.effect_anchored.cognitive_anchor import DecisionAnchor, CognitiveAnchorStore
from lao.effect_anchored.experience_contract import ExperienceContract
from lao.effect_anchored.experience_graph import ExperienceGraph
from lao.effect_anchored.experience_matching import ExperienceMatcher

AGENT = "customer_service"          # 同一个 LAO Agent
HUMAN_ALICE = "human:alice"         # 企业付费客户
HUMAN_BOB = "human:bob"             # 个人散客


def _build_contract_anchors(store: CognitiveAnchorStore, graph: ExperienceGraph) -> None:
    """写入两位 human 的「契约型经验锚点」(Storage 层产物)。

    - Human-specific 锚点: 以 owner = human:* 区分, 是差异化来源(Storage 的活)
    - 每条锚点 attach 到 ExperienceGraph, 供因果/邻居检索。
    - Note: 这里没有 `source` 列(ERGE anchors.db 用 source_type), 契约用 owner 表达 human。
    """
    contracts: List[ExperienceContract] = [
        # Alice —— 企业付费客户: 高信任额, 长期信任优先, 退款>¥2000 人工介入
        ExperienceContract(
            owner=HUMAN_ALICE, domain="refund",
            allowed_agents=[AGENT], forbidden_domains=["promo"],
            confidence=0.9, source="alice-enterprise-sla", anchor_type="decision"),
        # Bob —— 个人散客: 低信任额, 首次信任优先, 退款>¥100 人工介入
        ExperienceContract(
            owner=HUMAN_BOB, domain="refund",
            allowed_agents=[AGENT], forbidden_domains=[],
            confidence=0.7, source="bob-retail-policy", anchor_type="decision"),
    ]

    for i, cc in enumerate(contracts):
        who = "Alice" if cc.owner == HUMAN_ALICE else "Bob"
        anchor = DecisionAnchor(
            anchor_id=f"refund-{who.lower()}-gate",
            anchor_type="decision",
            value={
                "rule": (
                    f"[{cc.owner}] 退款闸门: 自动放行阈值 ¥{2000 if cc.owner == HUMAN_ALICE else 100}, "
                    f"超阈值→人工介入(依据: {cc.source})"
                ),
                "domain": cc.domain,
                "confidence": cc.confidence,
                "human": cc.owner,
            },
            tags=[cc.owner, "refund", "gate"],
            trust_weight=cc.confidence,
        )
        store.put(anchor)
        graph.add_edge(f"refund-{who.lower()}-gate", "refund_rule", "derived_from",
                       weight=cc.confidence, reason=f"{who} 退款闸门由通用退款规则派生")


def _retrieve_for(agent: str, human: str, matcher: ExperienceMatcher) -> Dict[str, Any]:
    """面向指定 human 检索已验证经验(Retrieval 层)。

    LAO 只负责「按 human 契约检索已验证经验 + 标注差异」;
    不做偏好推断(那是 Melody)。
    """
    # 用 context 透传 human 维度; permissioned 按契约 allowed_agents 过滤
    return matcher.retrieve_verified_experience(
        agent=agent,
        query="退款",
        limit=5,
        permissioned_only=True,
        context={"human_id": human, "domain": "refund"},
    )


def main() -> int:
    print("=" * 72)
    print("LAO 2.7 · Same Agent, Different Human (Demo)")
    print("=" * 72)

    # ── 1) 准备一个独立的 demo 锚点存储(不污染 ERGE 生产库) ──────────────
    tmp = tempfile.mkdtemp(prefix="lao-sadh-")
    store_path = os.path.join(tmp, "contracts.json")
    store = CognitiveAnchorStore(store_path=store_path)

    graph = ExperienceGraph()
    # 语义: 契约锚点也写入 ERGE(演示从图检索), 这里用内存图 + 独立 store 演示。
    _build_contract_anchors(store, graph)

    # 独立 ERGE 检索器: 挂 anchor_store(契约) + graph(因果/邻居)
    matcher = ExperienceMatcher(anchor_store=store, graph=graph)
    print(f"\n[demo storage] 契约锚点库: {store_path}")
    print(f"[demo storage] 锚点数: {len(store.lookup())} | 图边数: {graph.stats()['edges']}")

    # ── 2) 同一 Agent, 面向两位不同 Human 检索 ────────────────────────────
    print("\n" + "-" * 72)
    print(f"同一 Agent「{AGENT}」--- 面向不同 Human 的差异化检索")
    print("-" * 72)

    results: Dict[str, Dict[str, Any]] = {}
    for label, human in (("Alice(企业付费客户)", HUMAN_ALICE), ("Bob(个人散客)", HUMAN_BOB)):
        r = _retrieve_for(AGENT, human, matcher)
        results[human] = r
        print(f"\n  ▎面对 {label}  {human}")
        print(f"    engine={r.get('engine')} | permissioned={r.get('permissioned')} | count={r['count']}")
        for h in r["hits"]:
            c = h.get("content") if isinstance(h, dict) else getattr(h, "content", "")
            at = h.get("anchor_type") if isinstance(h, dict) else getattr(h, "anchor_type", "")
            t = h.get("trust") if isinstance(h, dict) else getattr(h, "trust", 0)
            line = c.replace("\n", " ")[:58]
            print(f"      [{at}][t={t}] {line}")

    # ── 3) 差分标注: 哪些经验是 human-specific, 哪些是 Agent 全局锚定 ────
    print("\n" + "-" * 72)
    print("差异来源标注(LAO 只负责「数据层差异」, 不做偏好推断)")
    print("-" * 72)
    alice_contract = _contract_rules(store, HUMAN_ALICE)
    bob_contract = _contract_rules(store, HUMAN_BOB)
    print(f"\n  Alice 契约(e={alice_contract['confidence']}): {alice_contract['rule']}")
    print(f"  Bob   契约(e={bob_contract['confidence']}): {bob_contract['rule']}")
    common = set([h.get("anchor_id") if isinstance(h, dict) else getattr(h, "anchor_id", None)
                  for h in results[HUMAN_ALICE]["hits"]]) & \
             set([h.get("anchor_id") if isinstance(h, dict) else getattr(h, "anchor_id", None)
                  for h in results[HUMAN_BOB]["hits"]])
    print(f"\n  两 Human 共同命中(Agent 全局锚定, Trust 不变): {len(common)} 条")
    print("  差异经验(来自各自契约, Storage 层产物): 见上「退款闸门」阈值对比")
    print("\n  → 边界: LAO 检索保持真实验证; 若需进一步个性化排序/推断,")
    print("    那是 Melody 的 Matching + Personal Adaptation 域(本 Demo 不实现)。")

    return 0


def _contract_rules(store: CognitiveAnchorStore, human: str) -> Dict[str, Any]:
    """取某 human 的第一条契约锚点, 供差分展示。"""
    for a in store.query(human):
        val = a.get("value", {}) if isinstance(a, dict) else getattr(a, "value", {})
        owner = val.get("human") if isinstance(val, dict) else None
        if owner == human:
            rule = val.get("rule", "") if isinstance(val, dict) else str(val)
            return {"rule": rule, "confidence": a.get("trust_weight", 0)}
    return {"rule": "(未命中)", "confidence": 0}


if __name__ == "__main__":
    raise SystemExit(main())
