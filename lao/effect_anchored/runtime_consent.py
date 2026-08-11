"""
Runtime Consent — LAO v3.1 创始人验收锚点
===========================================

运行时 3 个真实授权窗口（创始人验收: "能实际看到授权窗口弹出"）。

  1. 路由授权     route: 模型路由切换前需用户授权确认(不是无提示切 deepseek/glm)
  2. 数据清洗授权 clean: 本地/敏感数据处理前需授权(不能静默清洗)
  3. 多模态切换    multimodal: 检测到多模态需求·自动切换前需授权(不静默处理)

每个授权都是**真实交互弹窗**(input y/n), 非代码隐含。
授权结果持久化到本地 JSON, 已授权过的动作不重复打扰, 但首次必须弹窗。

用法(安装/运行时):
  rc = RuntimeConsent(store_path="~/.lao/runtime_consent.json")
  rc.route_gate("deepseek -> glm", task="翻译")      # 弹窗: 授权路由切换?
  rc.clean_gate("data/raw/user.csv")                 # 弹窗: 授权清洗?
  rc.multimodal_gate(model="qwen3.8-max")            # 弹窗: 授权多模态切换?

CLI 场景下弹窗是阻塞式 input(), 创始人能实际看到窗口并回答。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeConsent:
    """运行时授权三窗口(真实交互·非隐含)。"""

    # 授权动作类别(用于持久化记忆: 已授权则不再重复弹窗)
    K_ROUTE = "route"
    K_CLEAN = "clean"
    K_MULTIMODAL = "multimodal"

    def __init__(self, store_path: Optional[str] = None,
                 interactive: bool = True, default_deny: bool = True):
        self.store_path = store_path or os.path.join(
            os.path.expanduser("~"), ".lao", "runtime_consent.json")
        self.interactive = interactive      # True=真实弹窗(input) / False=自动按默认
        self.default_deny = default_deny    # 非交互时: True默认拒绝(安全) / False默认允许
        self._decisions: Dict[str, Any] = {}
        self._load()

    # -- 持久化 -------------------------------------------------------------

    def _load(self) -> None:
        if self.store_path and os.path.exists(self.store_path):
            try:
                with open(self.store_path) as f:
                    self._decisions = json.load(f)
            except (json.JSONDecodeError, OSError, TypeError):
                self._decisions = {}

    def _save(self) -> None:
        if not self.store_path:
            return
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        with open(self.store_path, "w") as f:
            json.dump(self._decisions, f, ensure_ascii=False, indent=2)

    def _remember(self, category: str, action_id: str, granted: bool) -> None:
        self._decisions.setdefault(category, {})[action_id] = {
            "granted": bool(granted), "at": _now_iso(),
        }
        self._save()

    def _was_granted(self, category: str, action_id: str) -> Optional[bool]:
        rec = self._decisions.get(category, {}).get(action_id)
        return rec.get("granted") if rec else None

    # -- 通用弹窗 -----------------------------------------------------------

    def _prompt(self, title: str, detail: str,
                category: str, action_id: str) -> bool:
        """真实交互弹窗(阻塞式 input), 返回用户是否授权。"""
        print("\n" + "=" * 62)
        print(f"🔐 LAO 授权确认 · {title}")
        print("=" * 62)
        print(detail)
        # CLI 交互
        if self.interactive:
            try:
                ans = input("  是否授权? (y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("  (无输入, 默认拒绝)")
                ans = "n"
            granted = ans in ("y", "yes", "是", "1")
        else:  # 非交互: 按默认(安全优先)
            granted = not self.default_deny
            print(f"  (非交互模式, 默认{'允许' if granted else '拒绝'})")
        self._remember(category, action_id, granted)
        print("=" * 62)
        return granted

    # -- ① 路由授权 ---------------------------------------------------------

    def route_gate(self, from_model: str, to_model: str,
                   task: str = "", action_id: Optional[str] = None) -> bool:
        """路由切换前授权确认(非无提示切换 deepseek/glm 等)。

        Args:
            from_model: 当前模型(如 deepseek-v4-pro)
            to_model:   目标模型(如 glm-5.2)
            task:       任务类型(翻译/代码/推理...)
        Returns:
            True=用户授权切换 / False=拒绝(保持原模型)
        """
        aid = action_id or f"{from_model}→{to_model}"
        prior = self._was_granted(self.K_ROUTE, aid)
        if prior is not None:
            return bool(prior)   # 已决定过, 不重复打扰
        detail = (
            f"  路由切换: {from_model} → {to_model}\n"
            + (f"  任务类型: {task}\n" if task else "")
            + "  ⚠️ 切换模型影响本次任务的 cost/质量/速度。"
        )
        granted = self._prompt("模型路由授权", detail, self.K_ROUTE, aid)
        return granted

    # -- ② 数据清洗授权 -------------------------------------------------------

    def clean_gate(self, target: str, sensitive: bool = True,
                   action_id: Optional[str] = None) -> bool:
        """本地/敏感数据处理前授权(不能静默清洗)。

        Args:
            target: 数据路径/说明(如 data/raw/user.csv)
            sensitive: 是否敏感数据(PII/用户数据)
        """
        aid = action_id or f"clean:{target}"
        prior = self._was_granted(self.K_CLEAN, aid)
        if prior is not None:
            return bool(prior)
        detail = (
            f"  数据清洗目标: {target}\n"
            + ("  🔒 标记为敏感数据(可能含用户个人信息)\n" if sensitive else "  非敏感数据\n")
            + "  清洗操作不可逆, 请确认范围。"
        )
        granted = self._prompt("数据清洗授权", detail, self.K_CLEAN, aid)
        return granted

    # -- ③ 多模态自动切换授权 ---------------------------------------------------

    def multimodal_gate(self, model: str, modality: str = "image",
                        action_id: Optional[str] = None) -> bool:
        """检测到多模态需求时·自动切换前授权(不静默处理)。

        Args:
            model: 检测到需切换的多模态模型
            modality: 模态(image/video/audio)
        """
        aid = action_id or f"multimodal:{model}"
        prior = self._was_granted(self.K_MULTIMODAL, aid)
        if prior is not None:
            return bool(prior)
        detail = (
            f"  检测到 {modality} 多模态输入\n"
            f"  建议切换模型: {model}(需要多模态能力)\n"
            "  自动切换会改变本次调用, 请确认。"
        )
        granted = self._prompt("多模态切换授权", detail, self.K_MULTIMODAL, aid)
        return granted

    # -- 状态/审计 -----------------------------------------------------------

    def decisions(self) -> Dict[str, Any]:
        """已做过的授权决策(审计)。"""
        return dict(self._decisions)

    def reset(self, category: Optional[str] = None) -> None:
        """重置某类授权记忆(下次重新弹窗)。"""
        if category:
            self._decisions.pop(category, None)
        else:
            self._decisions = {}
        self._save()
