#!/usr/bin/env python3
"""
RetroOnto Dynamic Engine v0.1 — rule_registry.py

核心职责：管理自动生成规则的版本谱系 + 冲突检测 + 废弃标记。

功能：
  - SQLite 存储：追踪每个规则的完整生命周期
  - 版本谱系：每次规则更新时记录 previous_version_id
  - 冲突检测：新规则与已有规则语义冲突时发出警告
  - 废弃标记：规则过时 → deprecated → 不会被 ferrum 执行
  - 查询 API：list_active(), get_by_id(), get_by_error_source(), get_deprecated()

数据模型（rule_registry 表）：
  rule_id                TEXT PRIMARY KEY
  version                INTEGER NOT NULL
  status                 TEXT NOT NULL  -- active | deprecated | superseded
  error_source_id        TEXT           -- 来源错误模式 ID
  constraint_file        TEXT           -- 生成的 .py 文件路径
  constraint_id          TEXT           -- 约束 ID (C_infra_001 等)
  created_ts             TEXT NOT NULL  -- ISO8601
  updated_ts             TEXT           -- ISO8601
  previous_version_id    TEXT
  superseded_by          TEXT
  conflict_with          TEXT           -- JSON array
  fingerprint            TEXT NOT NULL  -- 模式指纹
  times_triggered        INTEGER DEFAULT 0
  last_triggered_ts      TEXT
  severity               TEXT
  category               TEXT
  description            TEXT

设计原则：
  - 纯 Python 3 标准库，零外部依赖（使用内置 sqlite3）
  - CLI 入口支持 --help, --list, --deprecate, --query
  - 可独立运行，也可 import 到其他模块
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── SQL Schema ──────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rule_registry (
    rule_id             TEXT PRIMARY KEY,
    version             INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','deprecated','superseded')),
    error_source_id     TEXT,
    constraint_file     TEXT,
    constraint_id       TEXT,
    created_ts          TEXT NOT NULL,
    updated_ts          TEXT,
    previous_version_id TEXT,
    superseded_by       TEXT,
    conflict_with       TEXT,  -- JSON array
    fingerprint         TEXT NOT NULL,
    times_triggered     INTEGER NOT NULL DEFAULT 0,
    last_triggered_ts   TEXT,
    severity            TEXT DEFAULT '🟡',
    category            TEXT DEFAULT 'infrastructure',
    description         TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_rule_status ON rule_registry(status);
CREATE INDEX IF NOT EXISTS idx_rule_fingerprint ON rule_registry(fingerprint);
CREATE INDEX IF NOT EXISTS idx_rule_error_source ON rule_registry(error_source_id);
CREATE INDEX IF NOT EXISTS idx_rule_category ON rule_registry(category);
CREATE INDEX IF NOT EXISTS idx_rule_created ON rule_registry(created_ts);
"""


# ── Rule Registry ──────────────────────────────────────────────────────────


class RuleRegistry:
    """
    规则版本谱系管理器。

    使用 SQLite 存储所有规则的完整生命周期信息。
    支持版本谱系、冲突检测、废弃标记。
    """

    DEFAULT_DB_PATH: str = ""

    def __init__(self, db_path: Optional[str] = None) -> None:
        """
        Args:
            db_path: SQLite 数据库路径。
                     默认：同目录下的 rule_registry.db
        """
        if db_path is None:
            if self.DEFAULT_DB_PATH:
                db_path = self.DEFAULT_DB_PATH
            else:
                db_path = str(
                    Path(__file__).resolve().parent / "rule_registry.db"
                )
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        """延迟获取数据库连接（自动重连）。"""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _init_db(self) -> None:
        """初始化数据库和表。"""
        try:
            self.conn.executescript(SCHEMA_SQL)
            self.conn.commit()
            logger.info("数据库已初始化: %s", self.db_path)
        except sqlite3.Error as e:
            logger.error("数据库初始化失败: %s", e)
            raise

    def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── CRUD Operations ─────────────────────────────────────────────────

    def register(
        self,
        rule_id: str,
        fingerprint: str,
        constraint_file: str = "",
        constraint_id: str = "",
        error_source_id: str = "",
        severity: str = "🟡",
        category: str = "infrastructure",
        description: str = "",
        version: int = 1,
        previous_version_id: Optional[str] = None,
        conflict_with: Optional[list[str]] = None,
    ) -> bool:
        """
        注册新规则。

        Args:
            rule_id: 规则唯一 ID
            fingerprint: SHA256 模式指纹
            constraint_file: 生成的 .py 约束文件路径
            constraint_id: 约束 ID
            error_source_id: 来源错误模式 ID
            severity: 严重程度
            category: 分类
            description: 规则描述
            version: 版本号
            previous_version_id: 上一版本 ID（版本谱系）
            conflict_with: 与此规则冲突的其他规则 ID 列表

        Returns:
            True 如果注册成功，False 如果已存在
        """
        now = datetime.now(timezone.utc).isoformat()

        conflicts_json = json.dumps(conflict_with) if conflict_with else None

        try:
            self.conn.execute(
                """
                INSERT INTO rule_registry (
                    rule_id, version, status, error_source_id,
                    constraint_file, constraint_id, created_ts, updated_ts,
                    previous_version_id, fingerprint,
                    severity, category, description,
                    conflict_with
                ) VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id, version, error_source_id,
                    constraint_file, constraint_id, now, now,
                    previous_version_id, fingerprint,
                    severity, category, description,
                    conflicts_json,
                ),
            )
            self.conn.commit()
            logger.info("规则已注册: %s (v%d)", rule_id, version)
            return True
        except sqlite3.IntegrityError:
            logger.warning("规则已存在: %s", rule_id)
            return False

    def update_version(
        self,
        old_rule_id: str,
        new_rule_id: str,
        new_fingerprint: str,
        new_constraint_file: str = "",
        new_description: str = "",
    ) -> bool:
        """
        规则版本升级：废弃旧版 → 创建新版，建立谱系。

        Args:
            old_rule_id: 旧规则 ID（将被标记为 superseded）
            new_rule_id: 新规则 ID
            new_fingerprint: 新指纹
            new_constraint_file: 新约束文件路径
            new_description: 新描述

        Returns:
            True 成功
        """
        old_rule = self.get_by_id(old_rule_id)
        if not old_rule:
            logger.error("旧规则不存在: %s", old_rule_id)
            return False

        now = datetime.now(timezone.utc).isoformat()
        new_version = dict(old_rule)["version"] + 1

        try:
            # 标记旧规则为 superseded
            self.conn.execute(
                """
                UPDATE rule_registry
                SET status = 'superseded',
                    superseded_by = ?,
                    updated_ts = ?
                WHERE rule_id = ?
                """,
                (new_rule_id, now, old_rule_id),
            )

            # 创建新版
            self.conn.execute(
                """
                INSERT INTO rule_registry (
                    rule_id, version, status, error_source_id,
                    constraint_file, constraint_id, created_ts, updated_ts,
                    previous_version_id, fingerprint,
                    severity, category, description
                ) VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_rule_id,
                    new_version,
                    old_rule["error_source_id"],
                    new_constraint_file,
                    old_rule["constraint_id"],
                    now,
                    now,
                    old_rule_id,
                    new_fingerprint,
                    old_rule["severity"],
                    old_rule["category"],
                    new_description,
                ),
            )
            self.conn.commit()
            logger.info(
                "规则版本升级: %s (v%d) → %s (v%d)",
                old_rule_id, new_version - 1, new_rule_id, new_version,
            )
            return True
        except sqlite3.Error as e:
            logger.error("版本升级失败: %s", e)
            self.conn.rollback()
            return False

    def deprecate(self, rule_id: str, reason: str = "") -> bool:
        """废弃规则（标记为 deprecated，不会被 ferrum 执行）。"""
        now = datetime.now(timezone.utc).isoformat()
        desc = f"Deprecated: {reason}" if reason else "Deprecated"

        try:
            cursor = self.conn.execute(
                """
                UPDATE rule_registry
                SET status = 'deprecated',
                    updated_ts = ?,
                    description = description || ' | ' || ?
                WHERE rule_id = ? AND status = 'active'
                """,
                (now, desc, rule_id),
            )
            self.conn.commit()
            affected = cursor.rowcount
            if affected:
                logger.info("规则已废弃: %s (%s)", rule_id, reason or "无原因")
                return True
            logger.warning("规则不存在或已非 active: %s", rule_id)
            return False
        except sqlite3.Error as e:
            logger.error("废弃失败: %s", e)
            return False

    def record_trigger(self, rule_id: str) -> bool:
        """记录规则被触发（递增计数器）。"""
        now = datetime.now(timezone.utc).isoformat()
        try:
            cursor = self.conn.execute(
                """
                UPDATE rule_registry
                SET times_triggered = times_triggered + 1,
                    last_triggered_ts = ?
                WHERE rule_id = ?
                """,
                (now, rule_id),
            )
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error("记录触发失败: %s", e)
            return False

    # ── Query API ───────────────────────────────────────────────────────

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """将 Row 转为 dict，处理 JSON 字段。"""
        d = dict(row)
        if d.get("conflict_with"):
            try:
                d["conflict_with"] = json.loads(d["conflict_with"])
            except json.JSONDecodeError:
                d["conflict_with"] = []
        else:
            d["conflict_with"] = []
        return d

    def get_by_id(self, rule_id: str) -> Optional[dict[str, Any]]:
        """按 ID 查询规则。"""
        row = self.conn.execute(
            "SELECT * FROM rule_registry WHERE rule_id = ?", (rule_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_active(self) -> list[dict[str, Any]]:
        """列出所有活跃规则。"""
        rows = self.conn.execute(
            "SELECT * FROM rule_registry WHERE status = 'active' ORDER BY created_ts DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_deprecated(self) -> list[dict[str, Any]]:
        """列出所有已废弃规则。"""
        rows = self.conn.execute(
            "SELECT * FROM rule_registry WHERE status = 'deprecated' ORDER BY updated_ts DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_by_error_source(self, error_source_id: str) -> list[dict[str, Any]]:
        """按来源错误 ID 查询关联规则。"""
        rows = self.conn.execute(
            "SELECT * FROM rule_registry WHERE error_source_id = ? ORDER BY version DESC",
            (error_source_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_by_fingerprint(self, fingerprint: str) -> Optional[dict[str, Any]]:
        """按指纹查询规则。"""
        row = self.conn.execute(
            "SELECT * FROM rule_registry WHERE fingerprint = ? AND status = 'active'",
            (fingerprint,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_by_category(self, category: str) -> list[dict[str, Any]]:
        """按分类查询活跃规则。"""
        rows = self.conn.execute(
            "SELECT * FROM rule_registry WHERE category = ? AND status = 'active' ORDER BY created_ts DESC",
            (category,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_version_lineage(self, rule_id: str) -> list[dict[str, Any]]:
        """获取规则的完整版本谱系。"""
        current = self.get_by_id(rule_id)
        if not current:
            return []

        lineage: list[dict[str, Any]] = [current]

        # 回溯前驱
        prev_id = current.get("previous_version_id")
        while prev_id:
            prev = self.get_by_id(prev_id)
            if not prev:
                break
            lineage.insert(0, prev)
            prev_id = prev.get("previous_version_id")

        # 前瞻后继
        next_id = current.get("superseded_by")
        while next_id:
            nxt = self.get_by_id(next_id)
            if not nxt:
                break
            lineage.append(nxt)
            next_id = nxt.get("superseded_by")

        return lineage

    # ── Conflict Detection ──────────────────────────────────────────────

    def detect_conflicts(
        self, fingerprint: str, category: str, constraint_id: str = ""
    ) -> list[str]:
        """
        检测新规则与已有规则的冲突。

        当前策略：
          - 相同指纹 → 确认为重复
          - 同类别的活跃规则过多（>10）→ 标记为潜在冲突
          - 约束 ID 相同前缀 → 可能重复

        Returns:
            冲突的 rule_id 列表（空列表表示无冲突）
        """
        conflicts: list[str] = []

        # 检查相同指纹
        existing = self.get_by_fingerprint(fingerprint)
        if existing:
            conflicts.append(f"DUPLICATE:{existing['rule_id']}")

        # 检查同类别的活跃规则数量
        cat_rules = self.get_by_category(category)
        if len(cat_rules) > 10:
            conflicts.append(f"CATEGORY_SATURATION:{category}({len(cat_rules)})")

        # 检查相同前缀
        if constraint_id:
            prefix = constraint_id.rsplit("_", 1)[0]
            for r in cat_rules:
                existing_cid = r.get("constraint_id", "")
                if existing_cid and existing_cid.startswith(prefix) and existing_cid != constraint_id:
                    conflicts.append(f"PREFIX_CLASH:{r['rule_id']}")
                    break

        if conflicts:
            logger.warning("冲突检测: %s → %s", constraint_id, conflicts)

        return conflicts

    # ── Statistics ──────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取注册表统计信息。"""
        stats: dict[str, Any] = {}
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN status='deprecated' THEN 1 ELSE 0 END) as deprecated,
                SUM(CASE WHEN status='superseded' THEN 1 ELSE 0 END) as superseded,
                SUM(times_triggered) as total_triggers
            FROM rule_registry
            """
        ).fetchone()
        if row:
            stats.update(dict(row))

        # 按分类统计
        cat_rows = self.conn.execute(
            """
            SELECT category, COUNT(*) as cnt
            FROM rule_registry
            WHERE status = 'active'
            GROUP BY category
            """
        ).fetchall()
        stats["by_category"] = {r["category"]: r["cnt"] for r in cat_rows}

        return stats

    # ── Export ──────────────────────────────────────────────────────────

    def export_active_to_json(self) -> str:
        """导出所有活跃规则为 JSON 字符串。"""
        rules = self.list_active()
        return json.dumps(rules, indent=2, ensure_ascii=False)

    def export_all_to_json(self) -> str:
        """导出所有规则为 JSON 字符串。"""
        rows = self.conn.execute(
            "SELECT * FROM rule_registry ORDER BY created_ts DESC"
        ).fetchall()
        rules = [self._row_to_dict(r) for r in rows]
        return json.dumps(rules, indent=2, ensure_ascii=False)


# ── CLI Entry Point ─────────────────────────────────────────────────────────


def main() -> None:
    """CLI 入口。"""
    import argparse

    parser = argparse.ArgumentParser(
        description="RetroOnto Rule Registry — 管理规则版本谱系、冲突检测、废弃标记",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python rule_registry.py --list                # 列出所有活跃规则
  python rule_registry.py --stats               # 统计信息
  python rule_registry.py --deprecate RULE-001 "规则过时"  # 废弃规则
  python rule_registry.py --query RULE-001      # 查询规则详情
  python rule_registry.py --lineage RULE-001    # 查看版本谱系
  python rule_registry.py --export              # 导出所有活跃规则为 JSON
  python rule_registry.py --check-conflicts C_infra_001 --fingerprint abc123 -c infrastructure
        """,
    )
    parser.add_argument("--db", default="", help="SQLite 数据库路径")
    parser.add_argument("--list", action="store_true", help="列出所有活跃规则")
    parser.add_argument("--list-all", action="store_true", help="列出所有规则（含废弃）")
    parser.add_argument("--list-deprecated", action="store_true", help="列出已废弃规则")
    parser.add_argument("--stats", action="store_true", help="显示统计信息")
    parser.add_argument("--query", metavar="RULE_ID", help="查询规则详情")
    parser.add_argument("--deprecate", nargs=2, metavar=("RULE_ID", "REASON"), help="废弃规则")
    parser.add_argument("--lineage", metavar="RULE_ID", help="查看版本谱系")
    parser.add_argument("--export", action="store_true", help="导出所有活跃规则为 JSON")
    parser.add_argument("--check-conflicts", metavar="CONSTRAINT_ID", help="检测冲突")
    parser.add_argument("--fingerprint", default="", help="冲突检测用：模式指纹")
    parser.add_argument("-c", "--category", default="infrastructure", help="冲突检测用：规则分类")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    registry = RuleRegistry(db_path=args.db if args.db else None)

    try:
        if args.list:
            rules = registry.list_active()
            if not rules:
                print("(无活跃规则)")
            else:
                for r in rules:
                    print(
                        f"  [{r['severity']}] {r['rule_id']:30s} "
                        f"v{r['version']} | {r['category']:20s} | "
                        f"触发 {r['times_triggered']} 次 | "
                        f"{r.get('description', '')[:60]}"
                    )
            print(f"\n共 {len(rules)} 条活跃规则")

        elif args.list_all:
            print(registry.export_all_to_json())

        elif args.list_deprecated:
            rules = registry.get_deprecated()
            if not rules:
                print("(无废弃规则)")
            for r in rules:
                print(f"  {r['rule_id']:30s} v{r['version']} | {r.get('description', '')[:60]}")

        elif args.stats:
            stats = registry.get_stats()
            json.dump(stats, sys.stdout, indent=2, ensure_ascii=False)
            print()

        elif args.query:
            rule = registry.get_by_id(args.query)
            if rule:
                json.dump(rule, sys.stdout, indent=2, ensure_ascii=False)
                print()
            else:
                print(f"规则不存在: {args.query}")

        elif args.deprecate:
            rule_id, reason = args.deprecate
            ok = registry.deprecate(rule_id, reason)
            if ok:
                print(f"✅ 已废弃: {rule_id}")
            else:
                print(f"❌ 废弃失败: {rule_id}")

        elif args.lineage:
            lineage = registry.get_version_lineage(args.lineage)
            if not lineage:
                print(f"规则不存在: {args.lineage}")
            else:
                for r in lineage:
                    marker = "→" if r["status"] == "active" else " "
                    print(
                        f"  {marker} [{r['status']}] {r['rule_id']:30s} "
                        f"v{r['version']} | {r['created_ts']}"
                    )

        elif args.check_conflicts:
            conflicts = registry.detect_conflicts(
                args.fingerprint, args.category, args.check_conflicts
            )
            if conflicts:
                print(f"⚠️ 冲突检测: {args.check_conflicts}")
                for c in conflicts:
                    print(f"  冲突: {c}")
            else:
                print(f"✅ 无冲突: {args.check_conflicts}")

        elif args.export:
            print(registry.export_active_to_json())

        else:
            parser.print_help()

    finally:
        registry.close()


if __name__ == "__main__":
    main()
