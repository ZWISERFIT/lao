"""
轻量路由决策函数 — 产品包第一层
=================================

创始人说：路由决策就是产品包第一层。

当DeepSeek不可用或成本检测为高时→自动切换到Qwen。

决策逻辑：
1. 优先 DeepSeek 独立 key（per-Agent，零共享）
2. DeepSeek 失败/超时 → fallback 到 Qwen DashScope
3. 成本感知：当 DeepSeek 成本 > 阈值 → 轻量任务切 Qwen Flash
"""

from typing import Dict, List, Optional, Tuple


class RouterDecision:
    """
    路由决策层
    
    处理 Gateway 级别的 provider 选择逻辑。
    EAOE 的 LAO Router 负责细粒度路由，
    这个模块负责 Gateway config 层面的 fallback 链配置。
    """
    
    # 9 Agent 的完整 fallback 链
    AGENT_ROUTES = {
        "shuyu": {
            "primary": "deepseek-shuyu/deepseek-v4-pro",
            "fallbacks": [
                "deepseek-shuyu/deepseek-v4-flash",
                "qwen/qwen-plus",
                "qwen/qwen-max",
            ],
        },
        "baron": {
            "primary": "deepseek-baron/deepseek-v4-flash",
            "fallbacks": [
                "deepseek-baron/deepseek-v4-flash",
                "qwen/qwen-plus",
                "qwen/qwen-flash",
            ],
        },
        "ethan": {
            "primary": "deepseek-ethan/deepseek-v4-flash",
            "fallbacks": [
                "deepseek-ethan/deepseek-v4-flash",
                "qwen/qwen-flash",
            ],
        },
        "tristan": {
            "primary": "deepseek-tristan/deepseek-v4-pro",
            "fallbacks": [
                "deepseek-tristan/deepseek-v4-flash",
                "qwen/qwen-plus",
                "qwen/qwen-flash",
            ],
        },
        "stella": {
            "primary": "deepseek-stella/deepseek-v4-flash",
            "fallbacks": [
                "deepseek-stella/deepseek-v4-flash",
                "qwen/qwen-plus",
                "qwen/qwen-flash",
            ],
        },
        "nova": {
            "primary": "deepseek-nova/deepseek-v4-flash",
            "fallbacks": [
                "deepseek-nova/deepseek-v4-flash",
                "qwen/qwen-flash",
            ],
        },
        "luna": {
            "primary": "deepseek-luna/deepseek-v4-flash",
            "fallbacks": [
                "deepseek-luna/deepseek-v4-flash",
                "qwen/qwen-plus",
                "qwen/qwen-flash",
            ],
        },
        "zeus": {
            "primary": "deepseek-zeus/deepseek-v4-pro",
            "fallbacks": [
                "deepseek-zeus/deepseek-v4-flash",
                "qwen/qwen-plus",
                "qwen/qwen-max",
            ],
        },
        "momo_bridge": {
            "primary": "deepseek-momo/deepseek-v4-flash",
            "fallbacks": [
                "deepseek-momo/deepseek-v4-flash",
                "qwen/qwen-plus",
                "qwen/qwen-flash",
            ],
        },
    }
    
    # 成本感知模型映射
    # 轻量任务 → Qwen Flash（便宜）
    # 中等任务 → DeepSeek Flash / Qwen Plus
    # 重任务 → DeepSeek Pro / Qwen Max
    COST_MODEL_MAP = {
        "v4-pro": {"cheap": "qwen/qwen-plus", "cost_threshold_percent": 80},
        "v4-flash": {"cheap": "qwen/qwen-flash", "cost_threshold_percent": 70},
    }
    
    @classmethod
    def get_route(cls, agent_id: str) -> dict:
        """获取一个 Agent 的完整路由配置"""
        route = cls.AGENT_ROUTES.get(agent_id)
        if not route:
            route = cls.AGENT_ROUTES.get("tristan")  # fallback 到 tristan
        return route
    
    @classmethod
    def evaluate_fallback(cls, agent_id: str, deepseek_unavailable: bool = False, cost_high: bool = False) -> str:
        """
        评估当前应该用哪个模型
        
        Args:
            agent_id: Agent ID
            deepseek_unavailable: DeepSeek 是否不可用
            cost_high: 当前成本是否过高
        
        Returns:
            应该使用的模型字符串，如 "deepseek-shuyu/deepseek-v4-flash"
            或 "qwen/qwen-plus"
        """
        route = cls.get_route(agent_id)
        
        if deepseek_unavailable:
            # DeepSeek 不可用 → 直接切 Qwen
            return route["fallbacks"][0]  # 第一个非DeepSeek fallback
        
        if cost_high:
            # 成本过高 → 判断主模型类别
            primary = route["primary"]
            if "v4-pro" in primary:
                # Pro 模型贵 → 切到 Flash 或 Qwen
                return route["fallbacks"][0]
            elif "v4-flash" in primary:
                # Flash 已经是最便宜的了 → 切 Qwen Flash
                return "qwen/qwen-flash"
        
        return route["primary"]
    
    @classmethod
    def get_gateway_config_block(cls, agent_id: str) -> dict:
        """
        生成 Gateway 配置块（可直接写入 openclaw.json）
        
        返回格式：
        {
            "primary": "deepseek-shuyu/deepseek-v4-pro",
            "fallbacks": ["deepseek-shuyu/deepseek-v4-flash", "qwen/qwen-plus"]
        }
        """
        route = cls.get_route(agent_id)
        return {
            "primary": route["primary"],
            "fallbacks": route["fallbacks"],
        }


# 快速测试
if __name__ == "__main__":
    print("RouterDecision 路由决策测试")
    print("=" * 50)
    
    for aid in ["shuyu", "zeus", "tristan", "luna", "nova"]:
        route = RouterDecision.get_route(aid)
        print(f"\n  [{aid}]")
        print(f"    primary: {route['primary']}")
        print(f"    fallbacks: {route['fallbacks']}")
        print(f"    normal → {RouterDecision.evaluate_fallback(aid)}")
        print(f"    cost_high → {RouterDecision.evaluate_fallback(aid, cost_high=True)}")
        print(f"    deepseek_down → {RouterDecision.evaluate_fallback(aid, deepseek_unavailable=True)}")
