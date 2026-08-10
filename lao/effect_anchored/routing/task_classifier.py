"""
TaskClassifier — 功耗感知式任务分类 v2.0
========================================

根据任务关键词将任务映射到六个难度层级之一。
v2.0: 新增code层级 — Qoder Kimi-Code/GLM-5.2代码生成专用
"""


class TaskClassifier:
    """功耗感知式任务分类"""

    TIERS = {
        "ultra_light": [
            "心跳检查",
            "心跳回复",
            "心跳",
            "状态检查",
            "数据汇总",
            "格式转换",
            "hello",
            "ping",
            "realtime",
            "实时",
            "heartbeat",
        ],
        "light": [
            "模板填充",
            "简单查询",
            "格式转换",
            "日报",
            "摘要",
        ],
        "code": [
            "写代码", "编程", "实现函数", "写一个", "代码生成",
            "python", "javascript", "写个脚本", "写函数", "写类",
            "写接口", "写API", "写个测试", "写测试",
            "code", "function", "class", "implement",
            "refactor", "重构", "debug", "调试",
            "单元测试", "集成测试",
        ],
        # 2026-08-08 Shuyu裁定(Stella成本矩阵v0.2): cn_explain/cn_creative → Qwen（省2-4×·GLM截断浪费）
        "cn_explain": [
            "解释", "说明", "讲解", "科普", "介绍", "阐述",
            "为什么", "怎么回事", "含义", "意思", "原理",
            "explain", "explain_cn",
        ],
        "cn_creative": [
            "文案", "写诗", "写故事", "故事", "创意", "宣传语", "口号",
            "标题", "剧本", "写一篇文章", "作文", "小说",
            "小红书", "朋友圈", "creative_cn", "cn_creative",
        ],
        "medium": [
            "行业调研",
            "内容撰写",
            "分析摘要",
            "代码审查",
            "技术文档",
            "代码分析",
        ],
        "heavy": [
            "战略推理",
            "BP内容",
            "叙事架构",
            "资本分析",
            "估值建模",
            "架构设计",
        ],
        "reasoning": [
            "估值建模",
            "多步推理",
            "复杂数学",
            "财务模型",
            "推理",
            "逻辑推导",
        ],
    }

    def classify(self, task: str) -> str:
        """将任务文本分类到难度层级。

        Args:
            task: 任务描述文本。

        Returns:
            层级标识: "ultra_light" | "light" | "code" | "medium" | "heavy" | "reasoning"
        """
        task_lower = task.lower()
        # 先检查高优先级层级（更具体的匹配）
        # 2026-08-08 Shuyu裁定: cn_creative优先于code（"写一个故事"→创意类非代码）
        for tier in ["reasoning", "cn_creative", "code"]:
            for kw in self.TIERS[tier]:
                if kw in task_lower:
                    return tier
        # 2026-08-08 Shuyu裁定: cn_explain 新增分类（code之后，避免"解释代码"被误分流）
        for tier in ["cn_explain"]:
            for kw in self.TIERS[tier]:
                if kw in task_lower:
                    return tier
        # 再检查常规层级
        for tier in ["ultra_light", "light", "heavy", "medium"]:
            for kw in self.TIERS[tier]:
                if kw in task_lower:
                    return tier
        return "medium"  # default
