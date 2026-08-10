#!/usr/bin/env python3
"""
RetroOnto Dynamic Engine v0.1 — constraint_generator.py

核心职责：从错误模式（error_pattern）生成可执行 Python 约束代码。
不是 JSON，不是 bash脚本——生成的是真正的 Python 类代码。

输出：可 import 的 .py 约束文件，放到 retroonto/constraints/ 目录。

约束基类设计：
  - Constraint 基类：check() + auto_fix() + description + severity
  - 命名规范：C_{category_key}_{counter}_exec.py
  - 生成的代码有完整 docstring、类型提示、独立运行能力

设计原则：
  - 纯 Python 3 标准库，零外部依赖
  - CLI 入口支持 --help, --input, --output-dir, --dry-run
  - 可独立运行，也可 import 到其他模块
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Constraint Base Class Template ─────────────────────────────────────────

CONSTRAINT_BASE_CODE = '''#!/usr/bin/env python3
"""
RetroOnto Auto-Generated Constraint

约束ID:  {constraint_id}
严重程度:  {severity}
分类:      {category}
来源错误:  {source_error_id}
生成时间:  {generated_at}
指纹:      {fingerprint}

{description}
"""

from __future__ import annotations

import hashlib
import logging
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class Constraint(ABC):
    """RetroOnto 约束基类 — 所有自动生成的约束继承此类"""

    constraint_id: str = "{constraint_id}"
    severity: str = "{severity}"
    description: str = "{short_desc}"
    source_error_id: str = "{source_error_id}"
    generated_at: str = "{generated_at}"

    @abstractmethod
    def check(self) -> bool:
        """执行约束检测。返回 True 表示通过，False 表示违反。"""
        ...

    def auto_fix(self) -> bool:
        """尝试自动修复。返回 True 表示修复成功，False 表示不可自动修复。
        子类可覆盖此方法提供具体的自动修复逻辑。
        默认返回 False（不可自动修复）。"""
        return False

    def fingerprint(self) -> str:
        """返回约束指纹（SHA256），用于去重和版本追踪。"""
        raw = f"{{self.constraint_id}}|{{self.description}}|{{self.source_error_id}}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def __repr__(self) -> str:
        return f"Constraint({{self.constraint_id}}, {{self.severity}})"


class {class_name}(Constraint):
    """{description}"""

    def check(self) -> bool:
        """{check_doc}"""
        # TODO: 根据具体错误模式实现检测逻辑
        # 占位符实现 — 默认为通过，需根据实际约束定制
{check_template}

    def auto_fix(self) -> bool:
        """{fix_doc}"""
        # TODO: 实现自动修复逻辑（如果可行）
{fix_template}


# ── 独立运行入口 ────────────────────────────────────────────────────

def main() -> None:
    """独立运行约束检测。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    constraint = {class_name}()
    logger.info("执行约束: %s [%s]", constraint.constraint_id, constraint.severity)

    try:
        if constraint.check():
            logger.info("✅ 约束通过: %s", constraint.constraint_id)
            sys.exit(0)
        else:
            logger.error("🔴 约束违反: %s", constraint.constraint_id)
            logger.info("尝试自动修复...")
            if constraint.auto_fix():
                logger.info("✅ 自动修复成功")
                sys.exit(0)
            else:
                logger.error("🔴 无法自动修复")
                sys.exit(1)
    except Exception as e:
        logger.error("🔴 约束检测异常: %s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
'''


# ── Category → key mapping ─────────────────────────────────────────────────

CATEGORY_KEY_MAP: dict[str, str] = {
    "infrastructure": "infra",
    "coordination": "coord",
    "cognitive": "cogn",
}


# ── Constraint Generator ───────────────────────────────────────────────────


class ConstraintGenerator:
    """
    从 error_pattern 对象生成可执行 Python 约束代码。

    生成策略：
      - infrastructure 类 → 检查端口/服务/文件可达性
      - coordination 类 → 检查 Agent 间通信协议
      - cognitive 类 → 检查决策回溯链

    每个约束是一个 Constraint 子类，有 check() 和 auto_fix() 方法。
    """

    DEFAULT_OUTPUT_DIR: str = ""

    def __init__(self, output_dir: Optional[str] = None) -> None:
        """
        Args:
            output_dir: 约束输出目录。
                        默认：../constraints/ （相对于 engine/ 的父目录）
        """
        if output_dir is None:
            if self.DEFAULT_OUTPUT_DIR:
                output_dir = self.DEFAULT_OUTPUT_DIR
            else:
                output_dir = str(
                    Path(__file__).resolve().parent.parent / "constraints"
                )
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("约束输出目录: %s", self.output_dir)

    def get_next_counter(self, category: str) -> int:
        """为给定分类计算下一个可用编号。"""
        cat_key = CATEGORY_KEY_MAP.get(category, category[:4])
        prefix = f"C_{cat_key}_"
        max_n = 0
        for f in self.output_dir.glob(f"{prefix}*_exec.py"):
            stem = f.stem.replace("_exec", "")
            # 提取数字部分
            parts = stem.split("_")
            try:
                n = int(parts[-1])
                if n > max_n:
                    max_n = n
            except (ValueError, IndexError):
                pass
        return max_n + 1

    def generate_class_name(self, constraint_id: str) -> str:
        """从约束 ID 生成合法的 Python 类名。"""
        # C_infra_001 → ConstraintInfra001
        clean = constraint_id.replace("-", "_").replace(".", "_")
        parts = clean.split("_")
        return "".join(p.capitalize() for p in parts)

    @staticmethod
    def generate_check_template(category: str, error_signature: str, actual: str) -> str:
        """
        根据分类和错误特征生成 check() 方法的模板代码（0 缩进，由调用方 indent）。

        启发式：
          - infrastructure → 端口/服务可达性检测（socket）
          - coordination → Agent 通信检查
          - cognitive → 决策链回溯验证
        """
        safe_sig = shlex.quote(error_signature)[:100]
        safe_actual = shlex.quote(actual)[:100]

        if category == "infrastructure":
            return f"""# 基础设施可达性检测
# 错误特征: {safe_sig}
# 实际现象: {safe_actual}
import socket

# 尝试解析关键服务地址
# TODO: 根据实际错误模式替换检测目标
# 示例: 检测 portproxy 残留
# result = subprocess.run(["netsh", "interface", "portproxy", "show", "all"], ...)
logger.info("基础设施可达性检测 (占位符)")
return True"""

        elif category == "coordination":
            return f"""# Agent 间协调检测
# 错误特征: {safe_sig}
# 实际现象: {safe_actual}
import os

# 检查 Agent-Bus 通信状态
# TODO: 根据实际错误模式替换检测目标
bus_path = os.path.expanduser("~/.openclaw/shared/bus/agent-bus.sh")
if not os.path.exists(bus_path):
    logger.warning("Agent-Bus 不可用")
    return False
logger.info("Agent 协调检测通过")
return True"""

        elif category == "cognitive":
            return f"""# 认知决策链回溯验证
# 错误特征: {safe_sig}
# 实际现象: {safe_actual}
import os

# 检查决策追踪文件
# TODO: 根据实际错误模式替换检测目标
trace_dir = os.path.expanduser("~/.openclaw/shared/decision-traces/")
if not os.path.exists(trace_dir):
    logger.warning("决策追踪目录不存在")
    return False
logger.info("决策链回溯验证通过")
return True"""

        else:
            return f"""# 通用约束检测
# 错误特征: {safe_sig}
# 实际现象: {safe_actual}
logger.info("通用约束检测 (占位符)")
return True"""

    @staticmethod
    def generate_fix_template(category: str) -> str:
        """根据分类生成 auto_fix() 方法模板（0 缩进，由调用方 indent）。"""
        if category == "infrastructure":
            return """# 基础设施自动修复
import subprocess

logger.info("尝试基础设施自动修复...")
# TODO: 根据具体错误实现修复逻辑
# 例如: subprocess.run(["systemctl", "restart", "service"], check=False)
logger.warning("自动修复不可用（需定制）")
return False"""

        elif category == "coordination":
            return """# Agent 协调自动修复
logger.info("尝试协调层面修复...")
# TODO: 根据具体错误实现修复逻辑
logger.warning("自动修复不可用（需定制）")
return False"""

        else:
            return """# 认知决策自动修复
logger.info("认知层面不可自动修复（需人工介入）")
return False"""

    # ── Code Generation ──────────────────────────────────────────────────

    def generate_constraint_code(
        self,
        error_pattern: dict[str, Any],
        constraint_id: Optional[str] = None,
    ) -> str:
        """
        从错误模式字典生成完整的 Python 约束代码字符串。

        Args:
            error_pattern: 错误模式字典（来自 experience_extractor 输出）
            constraint_id: 可选，手动指定约束 ID，默认自动生成

        Returns:
            完整的 Python 源代码字符串
        """
        category = error_pattern.get("category", "infrastructure")
        cat_key = CATEGORY_KEY_MAP.get(category, category[:4])
        counter = self.get_next_counter(category)
        source_error_id = error_pattern.get("pattern_id", f"ERR-{counter:03d}")

        if constraint_id is None:
            constraint_id = f"C_{cat_key}_{counter:03d}"

        class_name = self.generate_class_name(constraint_id)
        fingerprint = error_pattern.get("pattern_fingerprint", "unknown")
        severity = error_pattern.get("severity", "🟡")
        generated_at = datetime.now(timezone.utc).isoformat()

        # 描述信息
        error_sig = error_pattern.get("error_signature", "unknown error")
        actual = error_pattern.get("actual", "")
        gap = error_pattern.get("gap_analysis", "")
        short_desc = f"{error_sig}: {gap[:80]}"
        description = (
            f"从错误模式自动生成的约束。\\n"
            f"\\n"
            f"错误签名: {error_sig}\\n"
            f"Gap分析:   {gap}\\n"
            f"声称:       {error_pattern.get('claimed', 'N/A')}\\n"
            f"实际:       {actual}\\n"
            f"来源Agent:  {error_pattern.get('source', {}).get('agent', 'Tristan')}"
        )

        check_template = self.generate_check_template(category, error_sig, actual)
        fix_template = self.generate_fix_template(category)

        check_doc = f"检测 {error_sig} 类错误"
        fix_doc = "自动修复此约束"

        code = CONSTRAINT_BASE_CODE.format(
            constraint_id=constraint_id,
            severity=severity,
            category=category,
            source_error_id=source_error_id,
            generated_at=generated_at,
            fingerprint=fingerprint,
            description=description,
            short_desc=short_desc,
            class_name=class_name,
            check_doc=check_doc,
            check_template=textwrap.indent(check_template, "        "),
            fix_doc=fix_doc,
            fix_template=textwrap.indent(fix_template, "        "),
        )

        return code

    def write_constraint(
        self,
        error_pattern: dict[str, Any],
        constraint_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> Optional[Path]:
        """
        生成并写入约束文件到磁盘。

        Args:
            error_pattern: 错误模式字典
            constraint_id: 可选约束 ID
            dry_run: 如果为 True，不实际写入文件

        Returns:
            写入文件的 Path 对象，dry_run 时返回 None
        """
        category = error_pattern.get("category", "infrastructure")
        cat_key = CATEGORY_KEY_MAP.get(category, category[:4])

        if constraint_id is None:
            counter = self.get_next_counter(category)
            constraint_id = f"C_{cat_key}_{counter:03d}"

        code = self.generate_constraint_code(error_pattern, constraint_id)

        filename = f"{constraint_id}_exec.py"
        filepath = self.output_dir / filename

        if dry_run:
            logger.info("[DRY RUN] 将写入: %s (%d 字节)", filepath, len(code))
            return None

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
        # 确保可执行
        os.chmod(filepath, 0o755)
        logger.info("✅ 约束已写入: %s", filepath)
        return filepath

    def generate_and_write(
        self,
        error_pattern: dict[str, Any],
        dry_run: bool = False,
    ) -> tuple[Optional[Path], str]:
        """
        一步完成：生成 + 写入。返回 (文件路径, 约束 ID)。

        Args:
            error_pattern: 错误模式字典
            dry_run: 如果为 True，不实际写入

        Returns:
            (文件 Path 或 None, 约束 ID 字符串)
        """
        category = error_pattern.get("category", "infrastructure")
        cat_key = CATEGORY_KEY_MAP.get(category, category[:4])
        counter = self.get_next_counter(category)
        constraint_id = f"C_{cat_key}_{counter:03d}"

        filepath = self.write_constraint(error_pattern, constraint_id, dry_run)
        return filepath, constraint_id


# ── CLI Entry Point ─────────────────────────────────────────────────────────


def main() -> None:
    """CLI 入口。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="RetroOnto Constraint Generator — 从错误模式生成可执行 Python 约束代码",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从萃取器输出生成约束
  python experience_extractor.py --input event.json | python constraint_generator.py

  # 从文件读取错误模式
  python constraint_generator.py --input pattern.json --output-dir ../constraints/

  # 干跑模式（不写入文件）
  python constraint_generator.py --input pattern.json --dry-run
        """,
    )
    parser.add_argument("--input", "-i", help="错误模式 JSON 文件路径（默认 stdin）")
    parser.add_argument(
        "--output-dir", "-d",
        default="",
        help="约束输出目录（默认 ../constraints/）",
    )
    parser.add_argument("--dry-run", action="store_true", help="干跑模式，不写入文件")
    parser.add_argument("--stdout", action="store_true", help="输出生成的代码到 stdout")
    parser.add_argument("--constraint-id", default=None, help="手动指定约束 ID")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志输出")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # 读取输入
    try:
        if args.input:
            with open(args.input, "r", encoding="utf-8") as f:
                error_pattern = json.load(f)
        else:
            raw = sys.stdin.read()
            if not raw.strip():
                logger.error("stdin 无数据")
                sys.exit(1)
            error_pattern = json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("读取输入失败: %s", e)
        sys.exit(1)

    # 初始化生成器
    output_dir = args.output_dir if args.output_dir else None
    generator = ConstraintGenerator(output_dir=output_dir)

    if args.stdout:
        # 只输出代码，不写入文件
        code = generator.generate_constraint_code(
            error_pattern, constraint_id=args.constraint_id
        )
        print(code)
        return

    # 正常生成 + 写入
    filepath, constraint_id = generator.generate_and_write(
        error_pattern, dry_run=args.dry_run
    )

    result = {
        "constraint_id": constraint_id,
        "file": str(filepath) if filepath else "(dry-run)",
        "dry_run": args.dry_run,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
