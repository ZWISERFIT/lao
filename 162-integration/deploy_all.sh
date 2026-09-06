#!/bin/bash
# 162号 P1-A集成施工令 · 统一部署脚本
# 执行顺序：T2→T5→T7→T3→T8→T6→T9
# 创始人批示：花钱不要紧，重要是有专门的agent跟踪钱花到那里

set -e
echo "=========================================="
echo "162号 P1-A集成施工令 · 统一部署"
echo "=========================================="

# 检查 router_r3.py
ROUTER="/home/agentuser/lao-release/lao/effect_anchored/routing/lao_router_server.py"
if [ ! -f "$ROUTER" ]; then
    echo "ERROR: router_r3.py not found at $ROUTER"
    exit 1
fi

# 检查所有P1-A模块目录
MODULES=("task_identity" "token_dictionary" "task_state_machine" "context_pruning" "ral_interface" "cost_reconciliation" "evaluation_suite")
for mod in "${MODULES[@]}"; do
    if [ ! -d "/home/agentuser/lao-release/$mod" ]; then
        echo "ERROR: Module $mod not found. Copy P1-A modules first."
        exit 1
    fi
done

echo ""
echo "Step 1/7: T2 任务身份层..."
python3 T2/patches/t2_patch_router_r3.py
echo ""
echo "Step 2/7: T5 Token字段字典..."
python3 T5/patches/t5_patch_router_r3.py
echo ""
echo "Step 3/7: T7 任务状态机..."
python3 T7/patches/t7_patch_router_r3.py
echo ""
echo "Step 4/7: T3 上下文剪枝..."
python3 T3/patches/t3_patch_router_r3.py
echo ""
echo "Step 5/7: T8 RAL接口..."
python3 T8/patches/t8_patch_router_r3.py
echo ""
echo "Step 6/7: T6 成本对账..."
python3 T6/patches/t6_patch_router_r3.py
echo ""
echo "Step 7/7: T9 评测集..."
python3 T9/patches/t9_patch_router_r3.py
echo ""

echo "=========================================="
echo "All 7 gates patched. Running verification..."
echo "=========================================="

# 验证
python3 T2/verify_t2.py

echo ""
echo "Restarting LAO router..."
sudo systemctl restart lao-router

echo ""
echo "=========================================="
echo "162号 P1-A集成完成！"
echo "=========================================="
echo ""
echo "Health endpoints:"
echo "  curl http://localhost:8000/health/pruning"
echo "  curl http://localhost:8000/health/ral"
echo "  curl http://localhost:8000/health/reconciliation"
echo "  curl http://localhost:8000/health/evaluation"
echo ""
echo "Rollback (if needed):"
echo "  cp router_r3.py.bak.t2 router_r3.py"
echo "  sudo systemctl restart lao-router"
