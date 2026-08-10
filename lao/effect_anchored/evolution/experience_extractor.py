#!/usr/bin/env python3
"""
RetroOnto Dynamic Engine v0.1 — experience_extractor.py

核心职责：从 H 函数拦截事件中自动萃取错误模式。
输入：JSON（stdin 或文件），包含 Agent 犯错事件。
输出：error_pattern 对象，含 need_permanentization 判断。

去重机制：
  - 使用模式指纹（pattern_fingerprint）做 content-addressable 去重
  - 维护本地模式注册表（JSON 文件），记录已见过的所有模式
  - 新模式 → need_permanentization=true, 自动分析并添加到注册表
  - 已见过模式 → need_permanentization=false

设计原则：
  - 纯 Python 3 标准库，零外部依赖
  - CLI 入口支持 --help, --input, --output
  - 可独立运行，也可 import 到其他模块
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────────────


@dataclass
class ErrorPattern:
    """萃取后的错误模式对象"""

    pattern_id: str
    error_signature: str
    claimed: str
    expected: str
    actual: str
    gap_analysis: str  # 自动分析 claimed vs actual 的差值
    severity: str  # 🔴 / 🟡 / 🟢
    category: str  # infrastructure / coordination / cognitive
    source_event_type: str
    source_agent: str
    source_context_id: str
    constraint_text: str
    pattern_fingerprint: str  # SHA256 指纹用于去重
    need_permanentization: bool
    similar_patterns: list[str] = field(default_factory=list)
    extracted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ExtractionInput:
    """H 函数拦截事件输入格式"""

    event_type: str = ""
    source_agent: str = ""
    context_id: str = ""
    error_signature: str = ""
    claimed: str = ""
    expected: str = ""
    actual: str = ""
    constraint_text: str = ""
    severity: str = "🟡"
    category: str = "infrastructure"
    timestamp: str = ""


# ── Core Extractor ──────────────────────────────────────────────────────────


class ExperienceExtractor:
    """
    从 H 函数拦截事件中自动萃取错误模式。

    核心逻辑：
      1. 解析输入事件
      2. 通过 error_signature + actual 差值自动识别错误类型
      3. 计算模式指纹（SHA256）
      4. 比对本地注册表 → 判断是否需要永久化
      5. 新模式 → need_permanentization=true, 注册到 registry
    """

    DEFAULT_REGISTRY_PATH: str = ""

    def __init__(self, registry_path: Optional[str] = None) -> None:
        """
        Args:
            registry_path: 模式注册表 JSON 文件路径。
                           默认：同目录下的 .pattern_registry.json
        """
        if registry_path is None:
            if self.DEFAULT_REGISTRY_PATH:
                registry_path = self.DEFAULT_REGISTRY_PATH
            else:
                registry_path = str(
                    Path(__file__).resolve().parent / ".pattern_registry.json"
                )
        self.registry_path = Path(registry_path)
        self._registry: dict[str, Any] = self._load_registry()

    # ── Registry Management ──────────────────────────────────────────────

    def _load_registry(self) -> dict[str, Any]:
        """加载模式注册表，不存在则初始化空注册表。"""
        if self.registry_path.exists():
            try:
                with open(self.registry_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("已加载模式注册表: %d 条记录", len(data.get("patterns", {})))
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("注册表损坏，重建: %s", e)
        return {"patterns": {}, "version": "1.0", "updated_at": None}

    def _save_registry(self) -> None:
        """保存模式注册表。"""
        self._registry["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._registry["count"] = len(self._registry["patterns"])
        try:
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump(self._registry, f, indent=2, ensure_ascii=False)
            logger.info("注册表已保存: %s (%d 条)", self.registry_path, len(self._registry["patterns"]))
        except OSError as e:
            logger.error("无法保存注册表: %s", e)

    def _register_pattern(self, pattern: ErrorPattern) -> None:
        """将新模式注册到注册表。"""
        self._registry["patterns"][pattern.pattern_fingerprint] = {
            "pattern_id": pattern.pattern_id,
            "fingerprint": pattern.pattern_fingerprint,
            "error_signature": pattern.error_signature,
            "category": pattern.category,
            "severity": pattern.severity,
            "source_agent": pattern.source_agent,
            "extracted_at": pattern.extracted_at,
            "times_extracted": 1,
        }

    def _increment_count(self, fingerprint: str) -> None:
        """增加已有模式的抽取计数。"""
        if fingerprint in self._registry["patterns"]:
            entry = self._registry["patterns"][fingerprint]
            entry["times_extracted"] = entry.get("times_extracted", 0) + 1
            entry["last_seen_at"] = datetime.now(timezone.utc).isoformat()

    # ── Fingerprint Computation ───────────────────────────────────────────

    @staticmethod
    def compute_fingerprint(error_signature: str, actual: str, category: str) -> str:
        """
        计算模式指纹（SHA256）。
        输入：error_signature + actual + category
        同一类错误的这些字段组合应是稳定的。
        """
        raw = f"{error_signature}|{actual}|{category}"
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]

    @staticmethod
    def compute_pattern_id(fingerprint: str) -> str:
        """从指纹生成可读的模式 ID。"""
        return f"PAT-{fingerprint[:8].upper()}"

    # ── Gap Analysis ──────────────────────────────────────────────────────

    @staticmethod
    def analyze_gap(claimed: str, expected: str, actual: str) -> str:
        """
        自动分析 claimed vs actual 的差值，生成人类可读的 gap 描述。

        启发式：
          - claimed 含 "已完成/已修复/已解决" 但 actual 含 "不可达/不存在/失败"
            → "虚假完成声明"
          - claimed 含 "已部署/已重启" 但 actual 含 "仍/依然/未"
            → "部署未生效"
          - claimed 含 "已验证/已确认" 但 actual 含 "实际/实测"
            → "验证手段不可信"
        """
        claimed_lower = claimed.lower()
        actual_lower = actual.lower()
        expected_lower = expected.lower()

        # 启发式 1: 虚假完成声明
        completion_words = {"完成", "修复", "解决", "好了", "ok", "done", "fixed", "resolved"}
        failure_words = {"不可达", "不存在", "失败", "无法", "错误", "报错", "still", "not", "fail", "error", "crash"}
        if any(w in claimed_lower for w in completion_words) and any(w in actual_lower for w in failure_words):
            return f"虚假完成声明: 声称'{claimed}', 但实测'{actual}'"

        # 启发式 2: 部署未生效
        deploy_words = {"部署", "重启", "启动", "启动", "deploy", "restart", "start"}
        still_words = {"仍", "依然", "还", "still", "remain"}
        if any(w in claimed_lower for w in deploy_words) and any(w in actual_lower for w in still_words):
            return f"部署未生效: 声称'{claimed}', 实测'{actual}'"

        # 启发式 3: 验证手段不可信
        verify_words = {"验证", "确认", "核实", "verify", "confirm", "check"}
        if any(w in claimed_lower for w in verify_words) and any(w in actual_lower for w in still_words):
            return f"验证手段不可信: 声称'{claimed}', 实测'{actual}'"

        # 启发式 4: 状态漂移（预期 vs 实际不一致）
        if expected and actual:
            return f"状态漂移: 预期 '{expected}', 实测 '{actual}'"

        # 默认
        return f"声称与实际不符: 声称'{claimed}', 实测'{actual}'"

    # ── Similarity Detection ──────────────────────────────────────────────

    def find_similar_patterns(self, signature: str, category: str) -> list[str]:
        """在注册表中查找签名子串匹配的已有模式。"""
        similar: list[str] = []
        sig_lower = signature.lower()
        for fp, entry in self._registry["patterns"].items():
            existing_sig = entry.get("error_signature", "")
            # 简单子串匹配
            if (sig_lower and existing_sig.lower()):
                if sig_lower in existing_sig.lower() or existing_sig.lower() in sig_lower:
                    similar.append(entry.get("pattern_id", fp[:8]))
                elif entry.get("category") == category:
                    # 同类别的也标记为潜在相似
                    similar.append(entry.get("pattern_id", fp[:8]))
        return similar[:5]  # 最多返回 5 个

    # ── Main Extraction Logic ─────────────────────────────────────────────

    def extract(self, raw_input: dict[str, Any]) -> ErrorPattern:
        """
        从原始事件 JSON 萃取错误模式。

        Args:
            raw_input: H_intercept 事件 JSON 字典

        Returns:
            ErrorPattern 对象，含 need_permanentization 判断

        Raises:
            ValueError: 缺少必需字段时
        """
        # 验证必需字段
        required = ["error_signature", "actual", "category"]
        for field in required:
            if field not in raw_input or not raw_input.get(field):
                raise ValueError(f"缺少必需字段: {field}")

        event = ExtractionInput(
            event_type=raw_input.get("event_type", "H_intercept"),
            source_agent=raw_input.get("source_agent", "Tristan"),
            context_id=raw_input.get("context_id", f"ctx-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"),
            error_signature=raw_input["error_signature"],
            claimed=raw_input.get("claimed", ""),
            expected=raw_input.get("expected", ""),
            actual=raw_input["actual"],
            constraint_text=raw_input.get("constraint_text", ""),
            severity=raw_input.get("severity", "🟡"),
            category=raw_input["category"],
            timestamp=raw_input.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )

        # 步骤 2: 计算模式指纹
        fingerprint = self.compute_fingerprint(
            event.error_signature, event.actual, event.category
        )
        pattern_id = self.compute_pattern_id(fingerprint)

        # 步骤 3: 去重检查
        if fingerprint in self._registry["patterns"]:
            need_perm = False
            self._increment_count(fingerprint)
            logger.info("模式已存在（指纹 %s），无需永久化", fingerprint)
        else:
            need_perm = True
            logger.info("新模式发现（指纹 %s），需要永久化", fingerprint)

        # 步骤 4: Gap 分析
        gap = self.analyze_gap(event.claimed, event.expected, event.actual)

        # 步骤 5: 查找相似模式
        similar = self.find_similar_patterns(event.error_signature, event.category)

        pattern = ErrorPattern(
            pattern_id=pattern_id,
            error_signature=event.error_signature,
            claimed=event.claimed,
            expected=event.expected,
            actual=event.actual,
            gap_analysis=gap,
            severity=event.severity,
            category=event.category,
            source_event_type=event.event_type,
            source_agent=event.source_agent,
            source_context_id=event.context_id,
            constraint_text=event.constraint_text,
            pattern_fingerprint=fingerprint,
            need_permanentization=need_perm,
            similar_patterns=similar,
        )

        # 步骤 6: 如果是新模式，立即注册
        if need_perm:
            self._register_pattern(pattern)
            self._save_registry()

        return pattern

    def extract_from_file(self, filepath: str) -> ErrorPattern:
        """从 JSON 文件萃取错误模式。"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return self.extract(data)

    def extract_from_stdin(self) -> ErrorPattern:
        """从 stdin 读取 JSON 并萃取。"""
        data = json.load(sys.stdin)
        return self.extract(data)

    # ── Registry Queries ─────────────────────────────────────────────────

    def get_registry_stats(self) -> dict[str, Any]:
        """获取注册表统计信息。"""
        patterns = self._registry.get("patterns", {})
        by_category: dict[str, int] = {}
        for entry in patterns.values():
            cat = entry.get("category", "unknown")
            by_category[cat] = by_category.get(cat, 0) + 1
        return {
            "total_patterns": len(patterns),
            "by_category": by_category,
            "updated_at": self._registry.get("updated_at"),
        }

    def is_known_pattern(self, error_signature: str, actual: str, category: str) -> bool:
        """快速检查模式是否已知。"""
        fp = self.compute_fingerprint(error_signature, actual, category)
        return fp in self._registry["patterns"]


# ── CLI Entry Point ─────────────────────────────────────────────────────────


def main() -> None:
    """CLI 入口。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="RetroOnto Experience Extractor — 从 H 函数拦截事件自动萃取错误模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  echo '{"event_type":"H_intercept","error_signature":"portproxy zombie","actual":"端口仍不可达","category":"infrastructure","claimed":"修复已完成","expected":"端口可达"}' | python experience_extractor.py
  python experience_extractor.py --input event.json --output pattern.json
  python experience_extractor.py --stats
        """,
    )
    parser.add_argument("--input", "-i", help="输入 JSON 文件路径（默认 stdin）")
    parser.add_argument("--output", "-o", help="输出 JSON 文件路径（默认 stdout）")
    parser.add_argument("--stats", action="store_true", help="显示注册表统计信息")
    parser.add_argument("--registry", default="", help="注册表 JSON 文件路径（覆盖默认）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志输出")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    extractor = ExperienceExtractor(
        registry_path=args.registry if args.registry else None
    )

    if args.stats:
        stats = extractor.get_registry_stats()
        json.dump(stats, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return

    try:
        if args.input:
            pattern = extractor.extract_from_file(args.input)
        else:
            pattern = extractor.extract_from_stdin()

        output = {
            "pattern_id": pattern.pattern_id,
            "pattern_fingerprint": pattern.pattern_fingerprint,
            "error_signature": pattern.error_signature,
            "gap_analysis": pattern.gap_analysis,
            "severity": pattern.severity,
            "category": pattern.category,
            "need_permanentization": pattern.need_permanentization,
            "similar_patterns": pattern.similar_patterns,
            "source": {
                "event_type": pattern.source_event_type,
                "agent": pattern.source_agent,
                "context_id": pattern.source_context_id,
            },
            "claimed": pattern.claimed,
            "expected": pattern.expected,
            "actual": pattern.actual,
            "constraint_text": pattern.constraint_text,
            "extracted_at": pattern.extracted_at,
        }

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            logger.info("输出已写入: %s", args.output)
        else:
            json.dump(output, sys.stdout, indent=2, ensure_ascii=False)
            print()

    except (json.JSONDecodeError, ValueError) as e:
        logger.error("萃取失败: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
