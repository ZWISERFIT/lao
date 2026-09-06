#!/usr/bin/env python3
"""
experience_compiler.py — LAO 经验编译器
========================================
把 anchors.json 中的 DecisionAnchor / CognitiveAnchor 转成三种训练格式：
  ① SFT (Alpaca)  — 监督微调
  ② ChatML         — 多轮对话微调
  ③ DPO 种子       — 偏好对齐（从 trade_off 提取）

输入：~/.lao/experience-loop/anchors.json
输出：同目录下
  - training_sft.jsonl
  - training_chatml.jsonl
  - training_dpo.jsonl
  - compile_report.json

依据：188号件《LAO完善施工令》步②
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────
# 0. 常量与权重映射
# ─────────────────────────────────────────────

PRECISION_WEIGHT = {
    "proven":   1.0,
    "tested":   0.85,
    "rough":    0.4,
    "draft":    0.2,
}
DEFAULT_PRECISION_WEIGHT = 0.5

TRAJECTORY_TAG = {
    "rising":   "[持续验证中]",
    "stable":   "",
    "falling":  "[可信度下降，谨慎使用]",
}

COG_PREFIXES = ("cog-",)
DEC_PREFIXES = ("dec-",)

DEFAULT_INPUT  = os.path.expanduser("~/.lao/experience-loop/anchors.json")
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/.lao/experience-loop/training")


# ─────────────────────────────────────────────
# 1. 加载与过滤
# ─────────────────────────────────────────────

def load_anchors(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def classify_anchors(data: Dict[str, Any]) -> Tuple[List[Dict], List[Dict]]:
    decisions, cognitives = [], []
    for key, entry in data.items():
        current = entry.get("current", entry)
        anchor_type = current.get("anchor_type", "")
        if anchor_type == "decision" or key.startswith(DEC_PREFIXES):
            current["_anchor_id"] = key
            decisions.append(current)
        elif anchor_type == "cognitive" or key.startswith(COG_PREFIXES):
            current["_anchor_id"] = key
            cognitives.append(current)
    return decisions, cognitives


# ─────────────────────────────────────────────
# 2. 辅助函数
# ─────────────────────────────────────────────

def _val(anchor: Dict, *keys, default=""):
    value = anchor.get("value", {})
    for k in keys:
        v = value.get(k)
        if v is not None:
            return v
    return default


def _get(anchor: Dict, key: str, default=""):
    v = anchor.get(key)
    return v if v is not None else default


def get_training_weight(anchor: Dict) -> float:
    pl = _get(anchor, "precision_level", "rough")
    base = PRECISION_WEIGHT.get(pl, DEFAULT_PRECISION_WEIGHT)
    traj = _get(anchor, "confidence_trajectory", "stable")
    if traj == "rising":
        base *= 1.1
    elif traj == "falling":
        base *= 0.7
    tw = _get(anchor, "trust_weight", 0.5)
    if isinstance(tw, (int, float)):
        final = (base + tw) / 2.0
    else:
        final = base
    return round(max(0.05, min(1.0, final)), 4)


def build_scenario(anchor: Dict) -> str:
    parts = []
    pc = _val(anchor, "paradigm_context")
    if pc:
        parts.append(f"范式：{pc}")
    chain = _get(anchor, "evidence_chain", [])
    if isinstance(chain, list):
        for ev in chain[:3]:
            detail = ev.get("detail", ev.get("result", ""))
            if detail:
                parts.append(f"证据：{detail}")
    return "；".join(parts) if parts else "无附加场景"


def _list_to_str(val, sep="、") -> str:
    if isinstance(val, list):
        return sep.join(str(x) for x in val)
    return str(val) if val else ""


# ─────────────────────────────────────────────
# 3. SFT 编译（Alpaca 格式）
# ─────────────────────────────────────────────

def compile_sft_decision(anchor: Dict) -> Optional[Dict]:
    trigger = _val(anchor, "trigger_condition")
    principle = _val(anchor, "principle")
    action = _val(anchor, "action_rule")
    if not trigger or not action:
        return None

    scenario = build_scenario(anchor)
    inp = f"原则：{principle}\n{scenario}"

    trade_off = _val(anchor, "trade_off", default={})
    rationale = trade_off.get("rationale", "") if isinstance(trade_off, dict) else ""
    scope = _get(anchor, "scope_boundary", "")

    output_parts = [f"行动规则：{action}"]
    if rationale:
        output_parts.append(f"权衡依据：{rationale}")
    if scope:
        output_parts.append(f"适用边界：{scope}")
    output = "\n".join(output_parts)

    return {
        "instruction": trigger,
        "input": inp,
        "output": output,
        "weight": get_training_weight(anchor),
        "source_anchor": _get(anchor, "_anchor_id"),
        "precision_level": _get(anchor, "precision_level", "unknown"),
    }


def compile_sft_cognitive(anchor: Dict) -> Optional[Dict]:
    principle = _val(anchor, "principle")
    if not principle:
        return None

    applicability = _val(anchor, "applicability", default=[])
    applicability_str = _list_to_str(applicability)
    formation = _get(anchor, "formation_trigger", "")

    if applicability_str:
        instruction = f"在{applicability_str}场景下应遵循什么原则？"
    else:
        instruction = "应遵循什么原则？"

    inp_parts = [f"原则：{principle}"]
    if formation:
        inp_parts.append(f"形成原因：{formation}")
    inp_parts.append(build_scenario(anchor))
    inp = "\n".join(inp_parts)

    constraint = _val(anchor, "constraint", default={})
    if isinstance(constraint, dict):
        c_limit = constraint.get("limit", "")
        c_enforce = constraint.get("enforce", "")
        out = [f"原则：{principle}"]
        if c_limit:
            out.append(f"约束：{c_limit}")
        if c_enforce:
            out.append(f"执行方式：{c_enforce}")
        conflicts = _val(anchor, "conflicts", default=[])
        if isinstance(conflicts, list) and conflicts:
            out.append(f"禁止事项：{'；'.join(str(c) for c in conflicts)}")
        output = "\n".join(out)
    else:
        output = principle

    return {
        "instruction": instruction,
        "input": inp,
        "output": output,
        "weight": get_training_weight(anchor),
        "source_anchor": _get(anchor, "_anchor_id"),
        "precision_level": _get(anchor, "precision_level", "unknown"),
    }


def compile_sft(decisions: List[Dict], cognitives: List[Dict]) -> List[Dict]:
    results = []
    for a in decisions:
        item = compile_sft_decision(a)
        if item:
            results.append(item)
    for a in cognitives:
        item = compile_sft_cognitive(a)
        if item:
            results.append(item)
    return results


# ─────────────────────────────────────────────
# 4. ChatML 编译
# ─────────────────────────────────────────────

def compile_chatml_decision(anchor: Dict) -> Optional[Dict]:
    trigger = _val(anchor, "trigger_condition")
    principle = _val(anchor, "principle")
    action = _val(anchor, "action_rule")
    if not trigger or not action:
        return None

    pc = _val(anchor, "paradigm_context")
    scope = _get(anchor, "scope_boundary", "")
    sys_parts = [f"核心原则：{principle}"]
    if pc:
        sys_parts.append(f"范式：{pc}")
    if scope:
        sys_parts.append(f"适用边界：{scope}")
    traj_tag = TRAJECTORY_TAG.get(_get(anchor, "confidence_trajectory", "stable"), "")
    if traj_tag:
        sys_parts.append(traj_tag)
    falsification = _get(anchor, "falsification_condition", "")
    if falsification:
        sys_parts.append(f"证伪条件：{falsification}")

    scenario = build_scenario(anchor)
    user_content = f"场景：{trigger}\n{scenario}"

    trade_off = _val(anchor, "trade_off", default={})
    assistant_parts = [f"行动规则：{action}"]
    if isinstance(trade_off, dict):
        chosen = trade_off.get("chosen", "")
        rationale = trade_off.get("rationale", "")
        if chosen:
            assistant_parts.append(f"权衡选择：{chosen}")
        if rationale:
            assistant_parts.append(f"权衡依据：{rationale}")
    assumptions = _get(anchor, "assumption_stack", [])
    if isinstance(assumptions, list) and assumptions:
        assistant_parts.append(f"前提假设：{'；'.join(str(a) for a in assumptions)}")

    return {
        "messages": [
            {"role": "system", "content": "\n".join(sys_parts)},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": "\n".join(assistant_parts)},
        ],
        "weight": get_training_weight(anchor),
        "source_anchor": _get(anchor, "_anchor_id"),
        "precision_level": _get(anchor, "precision_level", "unknown"),
    }


def compile_chatml_cognitive(anchor: Dict) -> Optional[Dict]:
    principle = _val(anchor, "principle")
    if not principle:
        return None

    applicability = _val(anchor, "applicability", default=[])
    applicability_str = _list_to_str(applicability)
    pc = _val(anchor, "paradigm_context")

    sys_parts = [f"核心原则：{principle}"]
    if pc:
        sys_parts.append(f"范式：{pc}")
    scope = _get(anchor, "scope_boundary", "")
    if scope:
        sys_parts.append(f"适用边界：{scope}")
    falsification = _get(anchor, "falsification_condition", "")
    if falsification:
        sys_parts.append(f"证伪条件：{falsification}")

    if applicability_str:
        user_content = f"在{applicability_str}相关场景中，应遵循什么原则？"
    else:
        user_content = "应遵循什么原则？"

    constraint = _val(anchor, "constraint", default={})
    assistant_parts = [f"原则：{principle}"]
    if isinstance(constraint, dict):
        c_limit = constraint.get("limit", "")
        c_enforce = constraint.get("enforce", "")
        if c_limit:
            assistant_parts.append(f"约束：{c_limit}")
        if c_enforce:
            assistant_parts.append(f"执行方式：{c_enforce}")
    conflicts = _val(anchor, "conflicts", default=[])
    if isinstance(conflicts, list) and conflicts:
        assistant_parts.append(f"禁止事项：{'；'.join(str(c) for c in conflicts)}")

    return {
        "messages": [
            {"role": "system", "content": "\n".join(sys_parts)},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": "\n".join(assistant_parts)},
        ],
        "weight": get_training_weight(anchor),
        "source_anchor": _get(anchor, "_anchor_id"),
        "precision_level": _get(anchor, "precision_level", "unknown"),
    }


def compile_chatml(decisions: List[Dict], cognitives: List[Dict]) -> List[Dict]:
    results = []
    for a in decisions:
        item = compile_chatml_decision(a)
        if item:
            results.append(item)
    for a in cognitives:
        item = compile_chatml_cognitive(a)
        if item:
            results.append(item)
    return results


# ─────────────────────────────────────────────
# 5. DPO 种子编译（从 trade_off 提取偏好对）
# ─────────────────────────────────────────────

def compile_dpo_decision(anchor: Dict) -> Optional[Dict]:
    """DecisionAnchor → DPO 偏好对（仅当 trade_off 完整时）"""
    trade_off = _val(anchor, "trade_off", default={})
    if not isinstance(trade_off, dict):
        return None

    chosen = trade_off.get("chosen", "")
    sacrificed = trade_off.get("sacrificed", "")
    rationale = trade_off.get("rationale", "")

    if not chosen or not sacrificed:
        return None  # 不完整 trade_off，跳过

    # prompt = 场景描述
    trigger = _val(anchor, "trigger_condition")
    principle = _val(anchor, "principle")
    scenario = build_scenario(anchor)

    prompt_parts = [f"场景：{trigger}" if trigger else "场景：决策权衡"]
    prompt_parts.append(f"原则：{principle}")
    prompt_parts.append(scenario)
    prompt = "\n".join(prompt_parts)

    # chosen = 实际选择的方案 + 理由
    chosen_text = f"选择：{chosen}"
    if rationale:
        chosen_text += f"\n理由：{rationale}"

    # rejected = 放弃的方案 + 为什么放弃
    rejected_text = f"放弃：{sacrificed}"
    if rationale:
        # 反转逻辑：说明为什么牺牲这个
        rejected_text += f"\n牺牲原因：{rationale}"

    # falsification → 生成负样本标注
    falsification = _get(anchor, "falsification_condition", "")
    neg_note = ""
    if falsification:
        neg_note = f"注意：此偏好对在以下条件下不成立：{falsification}"

    return {
        "prompt": prompt,
        "chosen": chosen_text,
        "rejected": rejected_text,
        "weight": get_training_weight(anchor),
        "source_anchor": _get(anchor, "_anchor_id"),
        "precision_level": _get(anchor, "precision_level", "unknown"),
        "negative_note": neg_note,
    }


def compile_dpo(decisions: List[Dict]) -> List[Dict]:
    """仅 DecisionAnchor 有 trade_off，CognitiveAnchor 不参与 DPO"""
    results = []
    for a in decisions:
        item = compile_dpo_decision(a)
        if item:
            results.append(item)
    return results


# ─────────────────────────────────────────────
# 6. 输出与报告
# ─────────────────────────────────────────────

def write_jsonl(items: List[Dict], path: str) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(items)


def generate_report(
    sft_data: List[Dict],
    chatml_data: List[Dict],
    dpo_data: List[Dict],
    n_decisions: int,
    n_cognitives: int,
    elapsed_sec: float,
) -> Dict:
    # precision_level 分布
    precision_dist = {}
    weight_sum = 0.0
    for item in sft_data:
        pl = item.get("precision_level", "unknown")
        precision_dist[pl] = precision_dist.get(pl, 0) + 1
        weight_sum += item.get("weight", 0)

    avg_weight = round(weight_sum / len(sft_data), 4) if sft_data else 0

    # DPO 来源统计
    dpo_anchors = [item.get("source_anchor", "") for item in dpo_data]

    return {
        "compile_time": datetime.now().isoformat(),
        "input_summary": {
            "total_decision_anchors": n_decisions,
            "total_cognitive_anchors": n_cognitives,
            "total_anchors": n_decisions + n_cognitives,
        },
        "output_summary": {
            "sft_count": len(sft_data),
            "chatml_count": len(chatml_data),
            "dpo_count": len(dpo_data),
        },
        "precision_level_distribution": precision_dist,
        "average_training_weight": avg_weight,
        "dpo_source_anchors": dpo_anchors,
        "elapsed_seconds": round(elapsed_sec, 2),
        "format_spec": {
            "sft": "Alpaca JSONL — keys: instruction, input, output, weight, source_anchor, precision_level",
            "chatml": "ChatML JSONL — keys: messages[{role,content}], weight, source_anchor, precision_level",
            "dpo": "DPO JSONL — keys: prompt, chosen, rejected, weight, source_anchor, precision_level, negative_note",
        },
        "transformers_compatible": True,
    }


# ─────────────────────────────────────────────
# 7. 主流程
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LAO 经验编译器")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="anchors.json 路径")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只出报告不写文件")
    args = parser.parse_args()

    t0 = datetime.now()

    # 1. 加载
    print(f"[1/5] 加载锚点：{args.input}")
    data = load_anchors(args.input)
    decisions, cognitives = classify_anchors(data)
    print(f"      DecisionAnchor: {len(decisions)} 条")
    print(f"      CognitiveAnchor: {len(cognitives)} 条")

    # 2. SFT
    print("[2/5] 编译 SFT (Alpaca) 格式...")
    sft_data = compile_sft(decisions, cognitives)
    print(f"      → {len(sft_data)} 条 SFT 样本")

    # 3. ChatML
    print("[3/5] 编译 ChatML 格式...")
    chatml_data = compile_chatml(decisions, cognitives)
    print(f"      → {len(chatml_data)} 条 ChatML 样本")

    # 4. DPO
    print("[4/5] 编译 DPO 种子格式...")
    dpo_data = compile_dpo(decisions)
    print(f"      → {len(dpo_data)} 条 DPO 偏好对")

    # 5. 输出
    elapsed = (datetime.now() - t0).total_seconds()
    report = generate_report(sft_data, chatml_data, dpo_data,
                             len(decisions), len(cognitives), elapsed)

    if args.dry_run:
        print("\n[DRY RUN] 报告：")
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        out_dir = args.output_dir
        sft_path   = os.path.join(out_dir, "training_sft.jsonl")
        chatml_path = os.path.join(out_dir, "training_chatml.jsonl")
        dpo_path   = os.path.join(out_dir, "training_dpo.jsonl")
        report_path = os.path.join(out_dir, "compile_report.json")

        n_sft    = write_jsonl(sft_data, sft_path)
        n_chatml = write_jsonl(chatml_data, chatml_path)
        n_dpo    = write_jsonl(dpo_data, dpo_path)

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"\n[5/5] 输出完成：")
        print(f"      SFT:    {sft_path} ({n_sft} 条)")
        print(f"      ChatML: {chatml_path} ({n_chatml} 条)")
        print(f"      DPO:    {dpo_path} ({n_dpo} 条)")
        print(f"      Report: {report_path}")

    # 6. 验收摘要
    print("\n" + "=" * 50)
    print("验收摘要")
    print("=" * 50)
    print(f"  DecisionAnchor 输入: {len(decisions)}")
    print(f"  CognitiveAnchor 输入: {len(cognitives)}")
    print(f"  SFT 输出: {len(sft_data)} (目标: {len(decisions) + len(cognitives)})")
    print(f"  ChatML 输出: {len(chatml_data)} (目标: {len(decisions) + len(cognitives)})")
    print(f"  DPO 输出: {len(dpo_data)} (目标: ≥20)")
    print(f"  precision_level 分布: {report['precision_level_distribution']}")
    print(f"  平均训练权重: {report['average_training_weight']}")

    # 返回退出码
    ok = (len(sft_data) > 0 and len(chatml_data) > 0 and len(dpo_data) >= 20)
    if not ok:
        print("\n[WARN] 验收未完全通过，请检查 DPO 数量或锚点数据完整性")
        return 1
    print("\n[OK] 编译完成，全部验收通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
