"""L4 交互确认层测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_high_confidence_auto():
    from effect_anchored.interaction.interaction_gate import InteractionGate
    gate = InteractionGate(mode="sdk")
    result = gate.check(type("Event",(),{"confidence":0.98,"reason":"测试"})(), confidence=0.98)
    assert result.needs_user == False
    assert result.auto_action == "blocked"

def test_low_confidence_push():
    from effect_anchored.interaction.interaction_gate import InteractionGate
    gate = InteractionGate(mode="sdk")
    result = gate.check(type("Event",(),{"confidence":0.6,"reason":"测试"})(), confidence=0.6)
    assert result.needs_user == True

def test_confirm():
    from effect_anchored.interaction.interaction_gate import InteractionGate
    gate = InteractionGate(mode="sdk")
    result = gate.confirm("test-1", "confirm_block")
    assert result.permanent == True
    assert result.anchor_created == True

def test_ui_backends():
    from effect_anchored.interaction.ui_backends import CLIBackend, SDKBackend, APIBackend
    cli = CLIBackend()
    assert cli is not None
    sdk = SDKBackend()
    assert sdk.pending == []
    api = APIBackend(webhook_url="http://test.example/webhook")
    result = api.prompt("测试", ["选项1", "选项2"])
    assert result.get("webhook") == "http://test.example/webhook"
    print("  ✅ test_ui_backends PASSED")

if __name__ == "__main__":
    test_high_confidence_auto()
    test_low_confidence_push()
    test_confirm()
    test_ui_backends()
    print("\n✅ L4 交互确认层测试全部通过")
