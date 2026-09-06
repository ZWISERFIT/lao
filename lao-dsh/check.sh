#!/bin/bash
cd ~/lao-release/lao-dsh
echo "=== Syntax Check ==="
PASS=0
FAIL=0
for f in src/*.js; do
  if node --check "$f" 2>&1; then
    echo "  OK  $f"
    PASS=$((PASS+1))
  else
    echo "  FAIL $f"
    FAIL=$((FAIL+1))
  fi
done
echo ""
echo "=== File List ==="
ls -la src/
echo ""
echo "=== Result: $PASS passed, $FAIL failed ==="
