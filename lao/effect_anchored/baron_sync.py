#!/usr/bin/env python3
"""
baron_sync.py — Baron 内容营销自动同步
========================================
施工令验收通过后，自动生成 lao_update_N.json 并写入 Baron inputs/。

触发方式:
  1. 手动: python3 baron_sync.py --summary "..." --type feature
  2. 自动: 施工令验收环节调用 generate_update()
  3. 批量: 从本 chat 成果批量生成多条更新

输入: 命令行参数 或 generate_update() 函数调用
输出: ~/ral-store/units/market/baron/inputs/lao_update_N.json

依据: 189号件 + 196号件§三
"""
import json
import os
import glob
import argparse
from datetime import datetime
from typing import Any, Dict, Optional

BARON_INPUTS = os.path.expanduser("~/ral-store/units/market/baron/inputs")


def _next_seq() -> int:
    """获取下一个序号(扫描已有 lao_update_*.json)。"""
    os.makedirs(BARON_INPUTS, exist_ok=True)
    existing = glob.glob(os.path.join(BARON_INPUTS, "lao_update_*.json"))
    if not existing:
        return 1
    max_seq = 0
    for f in existing:
        basename = os.path.basename(f)
        # lao_update_NNN.json or lao_update_N.json
        try:
            num_part = basename.replace("lao_update_", "").replace(".json", "")
            seq = int(num_part)
            max_seq = max(max_seq, seq)
        except ValueError:
            continue
    return max_seq + 1


def generate_update(
    summary: str,
    pain_point_solved: str = "",
    effect_improvement: str = "",
    data_evidence: str = "",
    technical_detail: str = "",
    suggested_angle: str = "",
    update_type: str = "feature",
    date: str = "",
) -> Dict[str, Any]:
    """生成一条 lao_update_N.json 并写入 Baron inputs/。

    Args:
        summary: 一句话摘要(必填)
        pain_point_solved: 解决了什么痛点
        effect_improvement: 提升了什么效果
        data_evidence: 数据佐证
        technical_detail: 技术细节(可选)
        suggested_angle: 建议营销角度(可选)
        update_type: feature|bugfix|optimization|benchmark
        date: 日期(默认今天)

    Returns:
        写入的 JSON dict
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    seq = _next_seq()
    seq_str = f"{seq:03d}"
    update_id = f"lao-update-{date.replace('-', '')}-{seq_str}"

    entry = {
        "update_id": update_id,
        "date": date,
        "source": "tristan",
        "type": update_type,
        "summary": summary,
        "pain_point_solved": pain_point_solved,
        "effect_improvement": effect_improvement,
        "data_evidence": data_evidence,
        "technical_detail": technical_detail,
        "suggested_angle": suggested_angle,
    }

    os.makedirs(BARON_INPUTS, exist_ok=True)
    path = os.path.join(BARON_INPUTS, f"lao_update_{seq_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)

    print(f"[OK] 写入: {path}")
    return entry


# ── 196号件批量种子: 本 chat 成果 → Baron 第一批素材 ──

BATCH_SEEDS = [
    {
        "summary": "经验编译器上线：98条锚点转为SFT/ChatML/DPO三种训练格式，LAO模型层逼近从0到1",
        "pain_point_solved": "LAO在路由层有效果但模型层零效果——锚点只塞messages不内化，模型权重不变",
        "effect_improvement": "后训练+微调+持续逼近（模型层）——锚点能变成训练数据，模型可以学到经验",
        "data_evidence": "98条锚点(78 Decision+20 Cognitive)→SFT 98条+ChatML 98条+DPO 49条偏好对；precision_level分布：proven=0.975权重,tested=0.877,rough=0.606",
        "technical_detail": "experience_compiler.py 548行，支持--input/--output-dir/--dry-run；输出JSONL可被transformers.Trainer直接加载",
        "suggested_angle": "错误即资产：犯过的错变成训练数据，LAO持续逼近正确答案",
        "update_type": "feature",
    },
    {
        "summary": "Momo同步修复：LAO每次路由后运行经验自动写入Momo，数据飞轮闭合",
        "pain_point_solved": "l3_sync_agent_runtime_to_momo()函数存在但从未产出真实数据——anchor_store没有agent_runtime类型",
        "effect_improvement": "持续逼近（机器经验自动回流，系统越用越准）",
        "data_evidence": "修复后每次路由自动生成agent_runtime条目→双写Momo inputs/+本地JSONL，幂等去重",
        "technical_detail": "在record_route_result()中新增_create_runtime_entry()和_sync_runtime_to_momo()，从路由数据直接创建经验条目",
        "suggested_angle": "LAO不是一开始就对，而是每次路由都在积累经验，持续逼近正确答案",
        "update_type": "bugfix",
    },
    {
        "summary": "省钱账本上线：LAO自动帮用户省的钱可量化、可展示、可回答'本月省了多少'",
        "pain_point_solved": "LAO已经在路由层自动帮用户省钱，但省了多少没有账本——用户看不到价值",
        "effect_improvement": "省钱——每次请求的成本/节省可追溯，按日/周/月统计",
        "data_evidence": "cost_ledger.jsonl记录每次请求：实际成本 vs 无LAO估算成本，差值=省钱金额；缓存命中率/RIS阻断均计入",
        "technical_detail": "cost_ledger.py 298行，支持CLI(--days/--breakdown/--question)和Python API；无LAO基准=全cache_miss+无RIS阻断+无智能降级",
        "suggested_angle": "LAO已经帮用户实现了自动省钱，不需要用户自己操作——现在你能看到省了多少",
        "update_type": "feature",
    },
]


def seed_from_chat_results():
    """从本 chat(196号件)成果批量生成 Baron 种子素材。"""
    print(f"[Baron Sync] 批量生成 {len(BATCH_SEEDS)} 条种子素材...")
    results = []
    for seed in BATCH_SEEDS:
        entry = generate_update(**seed)
        results.append(entry)
    print(f"[OK] {len(results)} 条种子已写入 Baron inputs/")
    return results


# ── CLI ──────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Baron 内容营销自动同步")
    parser.add_argument("--seed", action="store_true",
                        help="从196号件成果批量生成种子素材")
    parser.add_argument("--summary", type=str, help="一句话摘要")
    parser.add_argument("--type", type=str, default="feature",
                        choices=["feature", "bugfix", "optimization", "benchmark"])
    parser.add_argument("--pain", type=str, default="", help="解决的痛点")
    parser.add_argument("--effect", type=str, default="", help="提升的效果")
    parser.add_argument("--data", type=str, default="", help="数据佐证")
    parser.add_argument("--tech", type=str, default="", help="技术细节")
    parser.add_argument("--angle", type=str, default="", help="营销角度")
    args = parser.parse_args()

    if args.seed:
        seed_from_chat_results()
    elif args.summary:
        generate_update(
            summary=args.summary,
            pain_point_solved=args.pain,
            effect_improvement=args.effect,
            data_evidence=args.data,
            technical_detail=args.tech,
            suggested_angle=args.angle,
            update_type=args.type,
        )
    else:
        parser.print_help()
