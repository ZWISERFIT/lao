#!/usr/bin/env python3
"""
LAO自用·ZWISERFIT商业模式锚点初始化 + 使用示例
==============================================
用法: python3 init-zwf-anchors.py
每次商业模式讨论前运行，确保全Agent在同一个事实基准上。

H函数（HallucinationGate）当前设计为关键词+规则匹配，
适用于医疗/安全场景的拦截（如"膝盖疼推荐深蹲"）。
商业模式事实校验使用 MemoryAnchor.lookup() 确定性检索。

DRI: Zeus | 2026-07-30
"""

import sys
sys.path.insert(0, '/home/agentuser/.openclaw/workspace/lineage-anchored-ontology')

from effect_anchored import MemoryAnchor

anchor = MemoryAnchor()

# ==================== 商业模式关键事实 ====================

FACTS = {
    # 门店
    "zwf_store_location": "东莞市万江街道（非深圳·非一线城市·健身渗透率最低地区）",
    "zwf_store_years": "7年（2017至今）·经历疫情·亏损·负债·未关闭",

    # YC定位
    "zwf_yc_main": "YC #15 AI Operating System for Companies（主赛道·顶层容器）",
    "zwf_yc_secondary": "#2 AI-Native Services, #3 AI Personalized Medicine, #4 Company Brain, #12 Software for Agents",

    # 产品
    "lao_product": "Lineage-Anchored Ontology——AI认知层基础设施·LLM把记忆变成概率·LAO把记忆变成谱系·pip install·Apache 2.0",
    "lao_target": "所有使用LLM的Agent开发者——任何有'幻觉'和'失忆'痛点的场景。不限于门店。",
    "saros_product": "门店数字合伙人——Momo(AI店长大脑)+SaaS栈(手脚)·开源·Apache 2.0",
    "kintwin_product": "行为数据基础设施——硬件感知层→Nova行为流生成→Ethan存证确权",

    # 商业模式
    "zwf_revenue_primary": "数据资产变现——保险定价权·行为数据→公平保费·¥50-250/人/年",
    "zwf_revenue_path": "Saros开源免费获客 → KinTwin行为数据沉淀 → Zeus产业付费",
    "zwf_moat": "硬件信任根 × PoPB协议标准 × 节点密度 × 全栈整合 × 时间不可压缩",
    "zwf_moat_relationship": "关系壁垒——聪明是通用的·懂你是专用的·跟踪越久·AI越懂你·你越离不开",

    # AI军团
    "zwf_9agents": "Shuyu(总指挥)·Stella(审计)·Momo(门店)·Zeus(资本)·Tristan(技术)·Baron(品牌)·Luna(社群)·Nova(资产)·Ethan(存证)",
    "zwf_5units": "资本组(Zeus+Baron+Luna)·效率组(Shuyu+Stella)·数据组(Momo+Nova+Ethan)·技术组(Tristan)·运营组(Momo+Zeus)",

    # 融资
    "zwf_angel_round": "天使轮·¥1000万RMB·10%股权·投后¥1亿估值",
    "zwf_narrative_v5": "资本叙事v5定稿(2026-06-25)·十一章节·Palantir终局对标·Nourish天使轮对标",

    # 创始人
    "zwf_founder": "莫淑瑜·保险内行(普华永道+平安5年)×健身布道者(东莞万江7年)×产品架构师(2025 PRD)×AI系统创建者(2026 9-Agent)",
    "zwf_founder_story": "2017年亲人因癌症去世→放弃保险投身大健康→东莞万江开一家店→7年→2026年AI拐点→亲手创建9-Agent军团",

    # 关键事件
    "zwf_milestone_lao_pypi": "2026-07-30 13:04——ZWISERFIT第一个对外开源产品上线PyPI·v0.1.0-alpha·59 tests通过",
}

# ==================== 写入 ====================
for key, value in FACTS.items():
    anchor.put(key, value)

print(f"✅ LAO MemoryAnchor: {len(FACTS)} 个商业模式关键事实已锚定")
print()

# ==================== 演示：如何用LAO回答商业模式问题 ====================
print("=" * 60)
print("  LAO自用演示：查询商业模式关键事实")
print("=" * 60)
print()

questions = [
    ("门店在哪？", "zwf_store_location"),
    ("我们是YC哪个方向？", "zwf_yc_main"),
    ("LAO是什么？", "lao_product"),
    ("LAO面向谁？", "lao_target"),
    ("怎么赚钱？", "zwf_revenue_primary"),
    ("壁垒在哪？", "zwf_moat"),
    ("壁垒的核心一句话？", "zwf_moat_relationship"),
    ("天使轮估值？", "zwf_angel_round"),
    ("创始人是谁？", "zwf_founder"),
    ("9个Agent有哪些？", "zwf_9agents"),
]

for question, key in questions:
    r = anchor.lookup(key)
    if r.found:
        print(f"  Q: {question}")
        print(f"  A: {r.value}")
        print()

print("=" * 60)
print("  全Agent铁律：")
print("  讨论商业模式前 → anchor.lookup('zwf_xxx')")
print("  不凭记忆回答。所有事实从LAO提取。")
print("=" * 60)
