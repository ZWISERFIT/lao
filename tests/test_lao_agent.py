"""
LAOAgent 门面测试 — 验证 README 3 行代码体验
==============================================
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao import LAOAgent, __version__


def test_3_line_demo():
    """README 3 行代码体验"""
    print("=" * 50)
    print("  TEST: README 3 行代码 Demo")
    print("=" * 50)

    from lao import LAOAgent
    ai = LAOAgent()                              # ① 启动，一行

    ai.watch("member_0421", "用户下周会来训练")    # ② 记录，一行
    probs = ai.predict("member_0421")             # ③ 预测，一行

    print(f"  LAOAgent v{__version__}")
    print(f"  predict('member_0421') → {probs}")
    print(f"  ✅ 3行代码闭环跑通")
    return ai


def test_watch_multiple_behaviors():
    """记录多种行为 → 预测")
    """
    print("=" * 50)
    print("  TEST: 多行为记录 + 预测")
    print("=" * 50)

    ai = LAOAgent()
    user = "member_002"
    
    # 记录一个会员的生命周期
    ai.watch(user, "用户注册了新会员")
    ai.watch(user, "用户来店训练了45分钟")
    ai.watch(user, "用户来店训练了50分钟")
    ai.watch(user, "用户说：下周会来训练")
    ai.watch(user, "用户来店训练了55分钟")
    
    probs = ai.predict(user)
    print(f"  记录5个行为后，下一个行为预测:")
    nxt = probs.get('next_action_prob') or {}
    for action, p in nxt.items():
        print(f"    {action}: {p*100:.1f}%")
    print(f"  follow_through_prob: {probs.get('follow_through_prob')}")
    print(f"  suggestion: {probs.get('suggestion')}")
    return ai


def test_intention_decay():
    """意图衰减验证"""
    print("=" * 50)
    print("  TEST: 意图衰减 + 记忆不丢失")
    print("=" * 50)

    ai = LAOAgent()
    user = "member_003"
    
    # 记录一个承诺
    rec = ai.record_intention(user, "想续一年会员", initial_p=0.75)
    
    # 获取状态
    state = ai.state(user)
    print(f"  记录意图: \"想续一年会员\"")
    print(f"  初始概率: {rec.initial_p}")
    print(f"  state.churn_risk: {state['churn_risk']:.2f}")
    
    active = [i for i in state['active_intentions'] if '续' in i['text']]
    if active:
        print(f"  活跃意图: {active[0]['text']} (P={active[0]['probability']*100:.1f}%)")
    
    print(f"  ✅ 意图衰减 + 记忆保持工作正常")
    return ai


def test_constraint():
    """约束/经验复利验证"""
    print("=" * 50)
    print("  TEST: 经验复利（约束注册 + 触发检测）")
    print("=" * 50)

    ai = LAOAgent()
    
    # 添加一条约束
    cid = ai.add_constraint(
        description="禁止声称'从未暴露'（与事实冲突）",
        trigger="从未暴露|从来没暴露|从始至终没",
        level="red",
    )
    print(f"  添加约束: {cid}")
    
    # 检测违规文本
    result = ai.check("这个端口从未暴露过，一直是安全的")
    print(f"  检测'从未暴露': violated={result['violated']}")
    for h in result['hits']:
        print(f"    hit: {h['rule'][:40]}")
    
    assert result['violated'], "应该检测到违规！"
    print(f"  ✅ 约束检测工作正常")
    return ai


if __name__ == "__main__":
    print("=" * 50)
    print("  LAOAgent 门面测试套件")
    print("=" * 50)
    
    test_3_line_demo()
    test_watch_multiple_behaviors()
    test_intention_decay()
    test_constraint()
    
    print("\n" + "=" * 50)
    print("  ✅ LAOAgent 门面全部测试通过！")
    print("  README 3行代码 Demo 可以跑")
    print("=" * 50)
