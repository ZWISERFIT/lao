#!/usr/bin/env python3
"""
cost_ledger.py — LAO 省钱量化账本
===================================
记录每次路由请求的实际成本 vs 无 LAO 时的估算成本，量化 LAO 省了多少钱。

省钱来源:
  ① 缓存命中: cache_hit tokens 价格远低于 cache_miss(DeepSeek 命中=1/10价格)
  ② RIS 阻断: 被 RIS 判 down 的 provider 不发无效请求 → 省浪费
  ③ 智能路由: 命中率高 → 减少重试 → 省 token
  ④ 模型降级: 预算红线触发降级 → 用便宜模型 → 省差价

输出: ~/.lao/experience-loop/data/cost_ledger.jsonl
聚合: summary() 按日/周/月统计

用法:
  from cost_ledger import CostLedger
  ledger = CostLedger()
  ledger.record(...)
  print(ledger.summary())

依据: 188号件步④ + 196号件 #5
"""
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

DEFAULT_LEDGER_PATH = os.path.expanduser(
    "~/.lao/experience-loop/data/cost_ledger.jsonl"
)

# 无 LAO 时的基准价格假设(每百万 token, ¥)
# 无 LAO = 无缓存优化(全部 cache_miss) + 无 RIS 阻断(可能打到 down provider) + 无智能降级
BASELINE_PRICE_PER_MILLION = {
    # 模型: (input ¥/M, output ¥/M) — 假设无缓存全部走 miss 价
    "deepseek-v4-pro":   (4.0, 16.0),
    "deepseek-v3":       (2.0, 8.0),
    "deepseek-v3-flash": (0.5, 2.0),
    "qwen-plus":         (4.0, 12.0),
    "qwen-max":          (20.0, 60.0),
    "default":           (4.0, 16.0),
}

# 有 LAO 时的缓存命中价格(每百万 token, ¥)
CACHE_HIT_PRICE_PER_MILLION = {
    "deepseek-v4-pro":   0.4,
    "deepseek-v3":       0.2,
    "deepseek-v3-flash": 0.05,
    "qwen-plus":         0.4,
    "qwen-max":          2.0,
    "default":           0.4,
}


class CostLedger:
    """LAO 省钱量化账本。"""

    def __init__(self, ledger_path: Optional[str] = None):
        self.ledger_path = ledger_path or DEFAULT_LEDGER_PATH
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)

    def record(
        self,
        request_id: str = "",
        provider: str = "",
        model: str = "",
        in_tok: int = 0,
        out_tok: int = 0,
        cache_hit: int = 0,
        cache_miss: int = 0,
        actual_cost_yuan: float = 0.0,
        ris_blocked: bool = False,
        latency_ms: int = 0,
        status: str = "ok",
        agent: str = "",
        tier: str = "",
    ) -> Dict[str, Any]:
        """记录一次路由请求的成本。

        Args:
            request_id: 请求唯一标识
            provider: 路由到的 provider
            model: 路由到的模型
            in_tok: 输入 token 数
            out_tok: 输出 token 数
            cache_hit: 缓存命中 token 数
            cache_miss: 缓存未命中 token 数
            actual_cost_yuan: 实际成本(¥)
            ris_blocked: 是否被 RIS 阻断(省了一次无效请求)
            latency_ms: 延迟毫秒
            status: 请求状态(ok/retry/error)
            agent: 请求的 agent
            tier: 任务类型

        Returns:
            记录的条目 dict
        """
        now = datetime.now()

        # 估算无 LAO 时的成本
        estimated_without_lao = self._estimate_without_lao(
            model, in_tok, out_tok, cache_hit, cache_miss, ris_blocked
        )

        saved = round(estimated_without_lao - actual_cost_yuan, 6)

        entry = {
            "date": now.strftime("%Y-%m-%d"),
            "timestamp": now.isoformat(),
            "request_id": request_id,
            "provider": provider,
            "model": model,
            "agent": agent,
            "tier": tier,
            "tokens_total": int(in_tok) + int(out_tok),
            "input_tokens": int(in_tok),
            "output_tokens": int(out_tok),
            "cache_hit_tokens": int(cache_hit),
            "cache_miss_tokens": int(cache_miss),
            "cache_hit_rate": round(
                cache_hit / (cache_hit + cache_miss), 4
            ) if (cache_hit + cache_miss) > 0 else 0.0,
            "ris_blocked": bool(ris_blocked),
            "actual_cost_yuan": round(actual_cost_yuan, 6),
            "estimated_cost_without_lao": round(estimated_without_lao, 6),
            "saved_yuan": round(saved, 6),
            "latency_ms": int(latency_ms),
            "status": status,
        }

        # 追加写入 JSONL
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return entry

    def _estimate_without_lao(
        self, model: str, in_tok: int, out_tok: int,
        cache_hit: int, cache_miss: int, ris_blocked: bool
    ) -> float:
        """估算无 LAO 时的成本。

        无 LAO = ① 全部 token 走 cache_miss 价(无缓存优化)
                + ② RIS 阻断的请求仍然发出去(浪费)
                + ③ 失败重试平均多 1.5 次
        """
        # 基准价格(无缓存)
        prices = BASELINE_PRICE_PER_MILLION.get(
            model, BASELINE_PRICE_PER_MILLION["default"]
        )
        input_price, output_price = prices

        # 无 LAO: 全部 input token 按 cache_miss 价
        # (有 LAO 时 cache_hit 部分价格低很多)
        all_input_cost = (in_tok / 1_000_000) * input_price
        output_cost = (out_tok / 1_000_000) * output_price

        base_cost = all_input_cost + output_cost

        # RIS 阻断节省: 如果被阻断, 无 LAO 会浪费一次完整请求
        if ris_blocked:
            base_cost *= 1.3  # 估算 30% 的浪费(无效请求+重试)

        return round(base_cost, 6)

    # ── 聚合查询 ──────────────────────────────────────────

    def _load_records(self, days: int = 0) -> List[Dict]:
        """加载记录。days=0 全部, >0 最近 N 天。"""
        records = []
        if not os.path.exists(self.ledger_path):
            return records

        cutoff = ""
        if days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        with open(self.ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if cutoff and rec.get("date", "") < cutoff:
                        continue
                    records.append(rec)
                except json.JSONDecodeError:
                    continue
        return records

    def summary(self, days: int = 0) -> Dict[str, Any]:
        """成本汇总。days=0 全部, 7=最近一周, 30=最近一月。"""
        records = self._load_records(days)
        if not records:
            return {
                "period": f"最近{days}天" if days else "全部",
                "total_requests": 0,
                "total_actual_cost_yuan": 0,
                "total_estimated_without_lao": 0,
                "total_saved_yuan": 0,
                "avg_cache_hit_rate": 0,
                "ris_blocks": 0,
                "ok_requests": 0,
                "error_requests": 0,
            }

        total_actual = sum(r.get("actual_cost_yuan", 0) for r in records)
        total_without = sum(r.get("estimated_cost_without_lao", 0) for r in records)
        total_saved = sum(r.get("saved_yuan", 0) for r in records)
        cache_rates = [r.get("cache_hit_rate", 0) for r in records if r.get("cache_hit_rate") is not None]
        ris_blocks = sum(1 for r in records if r.get("ris_blocked"))
        ok_count = sum(1 for r in records if r.get("status") == "ok")

        return {
            "period": f"最近{days}天" if days else "全部",
            "total_requests": len(records),
            "total_actual_cost_yuan": round(total_actual, 4),
            "total_estimated_without_lao": round(total_without, 4),
            "total_saved_yuan": round(total_saved, 4),
            "saved_pct": round(total_saved / total_without * 100, 1) if total_without > 0 else 0,
            "avg_cache_hit_rate": round(sum(cache_rates) / len(cache_rates), 4) if cache_rates else 0,
            "ris_blocks": ris_blocks,
            "ok_requests": ok_count,
            "error_requests": len(records) - ok_count,
        }

    def daily_breakdown(self, days: int = 7) -> List[Dict[str, Any]]:
        """按日拆分。"""
        records = self._load_records(days)
        by_day: Dict[str, List] = {}
        for r in records:
            d = r.get("date", "unknown")
            by_day.setdefault(d, []).append(r)

        result = []
        for day in sorted(by_day.keys(), reverse=True):
            recs = by_day[day]
            actual = sum(r.get("actual_cost_yuan", 0) for r in recs)
            without = sum(r.get("estimated_cost_without_lao", 0) for r in recs)
            result.append({
                "date": day,
                "requests": len(recs),
                "actual_cost_yuan": round(actual, 4),
                "estimated_without_lao": round(without, 4),
                "saved_yuan": round(without - actual, 4),
            })
        return result

    def answer_how_much_saved(self) -> str:
        """回答'LAO 本月帮用户省了多少钱'。"""
        now = datetime.now()
        month_start = now.replace(day=1).strftime("%Y-%m-%d")
        records = self._load_records()
        month_records = [r for r in records if r.get("date", "") >= month_start]

        if not month_records:
            return "本月暂无路由记录。"

        total_saved = sum(r.get("saved_yuan", 0) for r in month_records)
        total_actual = sum(r.get("actual_cost_yuan", 0) for r in month_records)
        total_requests = len(month_records)
        avg_cache_rate = sum(
            r.get("cache_hit_rate", 0) for r in month_records
        ) / total_requests if total_requests else 0

        return (
            f"LAO {now.strftime('%Y年%m月')} 帮用户省了 ¥{total_saved:.2f}。"
            f"实际花费 ¥{total_actual:.2f}，"
            f"无 LAO 估算 ¥{total_actual + total_saved:.2f}。"
            f"共 {total_requests} 次请求，"
            f"平均缓存命中率 {avg_cache_rate:.1%}。"
        )


# ── CLI 入口 ──────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LAO 省钱账本")
    parser.add_argument("--days", type=int, default=0, help="统计天数(0=全部)")
    parser.add_argument("--breakdown", action="store_true", help="按日拆分")
    parser.add_argument("--question", action="store_true", help="回答省了多少钱")
    args = parser.parse_args()

    ledger = CostLedger()

    if args.question:
        print(ledger.answer_how_much_saved())
    elif args.breakdown:
        for row in ledger.daily_breakdown(args.days or 7):
            print(f"  {row['date']}: {row['requests']}次 "
                  f"实际¥{row['actual_cost_yuan']:.4f} "
                  f"省¥{row['saved_yuan']:.4f}")
    else:
        s = ledger.summary(args.days)
        print(json.dumps(s, indent=2, ensure_ascii=False))
