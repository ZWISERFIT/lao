"""
规则注册表 — LAO v2 经验复利层持久化存储
============================================

核心能力：所有生成的约束规则都在这里持久化存储 + 可查询 + 可调试。

数据结构：
registry/
├── constraints/    # 所有已注册的Constraint（JSON序列化）
├── anchors/        # M-function锚点数据
├── logs/           # 约束命中日志
└── schemas/        # 约束Schema定义

类比：
LLM的参数在权重文件里（不可读·不可改）
LAO的约束在规则注册表里（可读·可改·可调试·可审计）
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
import json
import os
import glob
import hashlib


class RuleRegistry:
    """
    规则注册表 — LAO v2 的持久化经验库
    
    用法:
        reg = RuleRegistry("/path/to/registry")
        reg.register(constraint)
        reg.query(domain="behavior", level="red")
        reg.hit("C-BMC-001-...")  → 记录命中
    """
    
    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "evolution",
                "registry",
            )
        self.base_dir = base_dir
        self.constraints_dir = os.path.join(base_dir, "constraints")
        self.anchors_dir = os.path.join(base_dir, "anchors")
        self.logs_dir = os.path.join(base_dir, "logs")
        
        # 内存缓存（加速查询）
        self._constraints: Dict[str, dict] = {}
        self._anchors: Dict[str, str] = {}
        
        self._ensure_dirs()
        self._load_cache()
    
    def _ensure_dirs(self):
        for d in [self.constraints_dir, self.anchors_dir, self.logs_dir]:
            os.makedirs(d, exist_ok=True)
    
    def _load_cache(self):
        """从磁盘加载所有约束到内存"""
        for fpath in glob.glob(os.path.join(self.constraints_dir, "*.json")):
            try:
                with open(fpath) as f:
                    c = json.load(f)
                    self._constraints[c["id"]] = c
            except (json.JSONDecodeError, KeyError):
                continue
        
        for fpath in glob.glob(os.path.join(self.anchors_dir, "*.json")):
            try:
                with open(fpath) as f:
                    a = json.load(f)
                    self._anchors[a["key"]] = a["value"]
            except (json.JSONDecodeError, KeyError):
                continue
    
    def register(self, constraint) -> str:
        """
        注册一条约束到注册表
        
        自动去重（相同rule+trigger的组合不重复注册）
        
        Args:
            constraint: Constraint对象或dict
        
        Returns:
            约束ID
        """
        if hasattr(constraint, "to_dict"):
            data = constraint.to_dict()
        else:
            data = constraint
        
        cid = data["id"]
        
        # 去重检查
        dedup_key = f"{data.get('rule','')}|{data.get('trigger_pattern','')}"
        for existing in self._constraints.values():
            existing_key = f"{existing.get('rule','')}|{existing.get('trigger_pattern','')}"
            if existing_key == dedup_key:
                return existing["id"]  # 已存在，返回已有ID
        
        data["registered_at"] = datetime.now(timezone.utc).isoformat()
        
        # 写入磁盘
        fpath = os.path.join(self.constraints_dir, f"{cid}.json")
        with open(fpath, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        self._constraints[cid] = data
        return cid
    
    def register_anchor(self, key: str, value: str, source: str = "lao-v2"):
        """注册M-function锚点"""
        data = {
            "key": key,
            "value": value,
            "source": source,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        fpath = os.path.join(self.anchors_dir, f"{key}.json")
        with open(fpath, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self._anchors[key] = value
    
    def hit(self, constraint_id: str, context: str = "") -> bool:
        """
        记录一次约束命中
        
        Args:
            constraint_id: 约束ID
            context: 命中时的上下文（可选）
        
        Returns:
            True=命中成功记录
        """
        if constraint_id not in self._constraints:
            return False
        
        c = self._constraints[constraint_id]
        c["hit_count"] = c.get("hit_count", 0) + 1
        c["last_hit_at"] = datetime.now(timezone.utc).isoformat()
        
        # 更新磁盘
        fpath = os.path.join(self.constraints_dir, f"{constraint_id}.json")
        with open(fpath, "w") as f:
            json.dump(c, f, indent=2, ensure_ascii=False)
        
        # 命中文日志
        log_entry = {
            "constraint_id": constraint_id,
            "rule": c.get("rule", ""),
            "hit_at": datetime.now(timezone.utc).isoformat(),
            "context": context[:200],
        }
        log_file = os.path.join(
            self.logs_dir,
            f"hits-{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl",
        )
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        
        return True
    
    def query(
        self,
        domain: Optional[str] = None,
        level: Optional[str] = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> List[dict]:
        """
        查询约束
        
        Args:
            domain: 按域筛选
            level: 按级别筛选
            active_only: 仅活跃的
            limit: 最大返回数
        """
        results = []
        for c in self._constraints.values():
            if active_only and not c.get("active", True):
                continue
            if domain and c.get("domain") != domain:
                continue
            if level and c.get("level") != level:
                continue
            results.append(c)
        
        results.sort(key=lambda x: x.get("registered_at", ""), reverse=True)
        return results[:limit]
    
    def get(self, constraint_id: str) -> Optional[dict]:
        """获取具体某条约束"""
        return self._constraints.get(constraint_id)
    
    def get_anchor(self, key: str) -> Optional[str]:
        """获取锚点值"""
        return self._anchors.get(key)
    
    def deactivate(self, constraint_id: str) -> bool:
        """禁用一条约束"""
        if constraint_id not in self._constraints:
            return False
        self._constraints[constraint_id]["active"] = False
        
        fpath = os.path.join(self.constraints_dir, f"{constraint_id}.json")
        with open(fpath, "w") as f:
            json.dump(self._constraints[constraint_id], f, indent=2, ensure_ascii=False)
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        """注册表统计"""
        total = len(self._constraints)
        active = sum(1 for c in self._constraints.values() if c.get("active", True))
        
        domains = {}
        levels = {}
        for c in self._constraints.values():
            d = c.get("domain", "unknown")
            domains[d] = domains.get(d, 0) + 1
            lv = c.get("level", "unknown")
            levels[lv] = levels.get(lv, 0) + 1
        
        return {
            "total_constraints": total,
            "active_constraints": active,
            "disabled_constraints": total - active,
            "total_anchors": len(self._anchors),
            "by_domain": domains,
            "by_level": levels,
        }
