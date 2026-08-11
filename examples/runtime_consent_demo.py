#!/usr/bin/env python3
"""
LAO 运行时 3 授权窗口 — 创始人/Stella 验收演示
================================================

运行本脚本, 3 个授权窗口会**真实弹出**(阻塞式 input), 创始人可逐一确认:

  ① 模型路由授权:    deepseek-v4-pro → glm-5.2 (切换模型需授权)
  ② 数据清洗授权:    data/raw/user.csv (敏感数据处理需授权)
  ③ 多模态切换授权:  qwen-vl-max (检测到图片输入·自动切换需授权)

用法:
  python3 examples/runtime_consent_demo.py

每个窗口输入 y=授权 / n=拒绝。
授权结果持久化到 ~/.lao/runtime_consent.json, 已授权动作不再重复弹窗。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.runtime_consent import RuntimeConsent


def main() -> int:
    rc = RuntimeConsent()   # 真实交互模式(默认 interactive=True)

    print("\n🚀 LAO 运行时授权验收演示 — 3 个授权窗口将依次弹出")
    print("   (授权结果会记住, 已同意的动作不再重复打扰)")

    # ① 路由授权
    granted1 = rc.route_gate("deepseek-v4-pro", "glm-5.2", task="翻译")
    print(f"   → 路由授权结果: {'✅ 已授权切换' if granted1 else '⛔ 拒绝·保持原模型'}\n")

    # ② 数据清洗授权
    granted2 = rc.clean_gate("data/raw/user_profiles.csv", sensitive=True)
    print(f"   → 数据清洗授权结果: {'✅ 已授权清洗' if granted2 else '⛔ 拒绝·不清洗'}\n")

    # ③ 多模态切换授权
    granted3 = rc.multimodal_gate("qwen-vl-max", modality="image")
    print(f"   → 多模态切换授权结果: {'✅ 已授权自动切换' if granted3 else '⛔ 拒绝·不切换'}\n")

    print("=" * 62)
    print("验收汇总: 3 个授权窗口均已弹出并记录")
    for cat, acts in rc.decisions().items():
        for aid, dec in acts.items():
            print(f"   [{cat}] {aid} → {'授权' if dec['granted'] else '拒绝'}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
