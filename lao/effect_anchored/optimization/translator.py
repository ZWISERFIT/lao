"""
Language Translator — LAO v3.1 P0-11
======================================

语言翻译器: 把技术指标翻译成人话。**永远不推未命中率/延迟ms/403错误码**。

铁律:
  - 技术指标("cache_hit 15%" / "latency 3000ms" / "error 403") 绝不直接显示给用户
  - 全部经本翻译器转为用户友好的人话(什么·为什么·怎么办)
  - 模板从 translator_templates.json 加载(可配置)

用法:
  t = LanguageTranslator()
  msg = t.translate({
      "type": "duplicate_task",
      "metrics": {"dup_count": 7, "task_type": "退款路由"},
  })   # -> {title, body, cta} 全人话
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_TEMPLATES = os.path.join(os.path.dirname(__file__), "translator_templates.json")


def _fmt(value: Any) -> str:
    """格式化数值: 整数去尾0。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class LanguageTranslator:
    """技术指标 → 用户友好人话。"""

    def __init__(self, templates_path: Optional[str] = None):
        self.templates_path = templates_path or DEFAULT_TEMPLATES
        self.templates: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.templates_path) as f:
                data = json.load(f)
            return data.get("six_anomaly_templates", {})
        except (OSError, json.JSONDecodeError, TypeError):
            return {}

    def translate(self, anomaly: Dict[str, Any],
                  metrics_extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """把一条异常翻译成人话(永不出现技术指标)。

        Args:
            anomaly: {"type": str, "metrics": {...}}
            metrics_extra: 额外替换变量(如 candidate_model)。

        Returns:
            {"type", "title", "body", "cta", "is_humanized": True}
        """
        atype = anomaly.get("type", "")
        tmpl = self.templates.get(atype)
        if not tmpl:
            return {
                "type": atype, "title": "检测到需要关注的情况",
                "body": "Agent 注意到一些可以优化的地方, 点这里了解详情。",
                "cta": "了解详情", "is_humanized": True,
            }
        # 变量替换(metrics + extra; 确保只输出模板内容, 不含原始技术指标)
        vars_map: Dict[str, str] = {}
        for k, v in (anomaly.get("metrics") or {}).items():
            vars_map[k] = _fmt(v)
        if metrics_extra:
            for k, v in metrics_extra.items():
                vars_map[k] = _fmt(v)

        def fill(s: str) -> str:
            for k, v in vars_map.items():
                s = s.replace("{" + k + "}", str(v))
            return s

        return {
            "type": atype,
            "title": fill(tmpl["human_title"]),
            "body": fill(tmpl["human_body"]),
            "cta": fill(tmpl["cta"]),
            "is_humanized": True,   # 标记: 已转人话, 可安全展示
        }
