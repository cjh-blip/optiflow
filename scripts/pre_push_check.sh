#!/bin/bash
# 推送门 —— 推之前必须过这三件。推不过就别推。
#
# 用法：bash scripts/pre_push_check.sh [基线ref，默认 origin/main]
# 环境：GUANGSHU_PY（默认 workshop 解释器）、跨语言断言需要 GUANGSHU_METRICS_JS / GUANGSHU_PKG（可选 GUANGSHU_CRIT）
#
# 为什么要有它：2026-10-13 首次推送时，本机路径与项目名已经**提交进历史**（改工作区没用，得改历史才干净）。
# 从此把「脱敏扫 + 测试 + 断言」写成一道门，而不是靠记得。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

BASE="${1:-origin/main}"
PY="${GUANGSHU_PY:-$HOME/anaconda3/envs/workshop/bin/python3}"
FAIL=0

echo "════ 推送门：$BASE..HEAD ════"
git log --oneline "$BASE"..HEAD 2>/dev/null | head -10 | sed 's/^/  /' || true

echo
echo "== ① 关键词扫（硬门：本机路径；软门：项目名，公开仓才硬拦）=="
HARD_KW='/Users/|cjh1'
SOFT_KW='208|礼堂|深圳技术|SZTU|陈骏皓|Lightman|homework|optics_learning|会议室改造'
PUBLIC="${PUBLIC:-1}"
if ! git rev-parse --verify "$BASE" >/dev/null 2>&1; then
  echo "  ⚠ 基线 $BASE 不存在 → 改为全树扫（首次推送前就是这样）"
  SCAN_CMD="git grep -InE"
  SCAN_ARGS=""   # 交给 git grep 自己找
  hits_hard=$(git grep -InE "$HARD_KW" 2>/dev/null | wc -l | tr -d ' ')
  hits_soft=$(git grep -InE "$SOFT_KW" 2>/dev/null | wc -l | tr -d ' ')
else
  hits_hard=$(git diff "$BASE"..HEAD 2>/dev/null | grep -E '^\+' | grep -Ec "$HARD_KW" || true)
  hits_soft=$(git diff "$BASE"..HEAD 2>/dev/null | grep -E '^\+' | grep -Ec "$SOFT_KW" || true)
fi
if [ "${hits_hard:-0}" -gt 0 ]; then
  echo "  ✗ 本机路径命中 $hits_hard 行（任何仓都不该留）"
  git grep -InE "$HARD_KW" | head -8 | cut -c1-150 | sed 's/^/    /'
  FAIL=1
else
  echo "  ✓ 本机路径 0 命中"
fi
if [ "${hits_soft:-0}" -gt 0 ]; then
  if [ "$PUBLIC" = "1" ]; then
    echo "  ✗ 项目名命中 $hits_soft 行（公开仓硬拦）"
    FAIL=1
  else
    echo "  ⚠ 项目名命中 $hits_soft 行（私有仓：提示级，不拦）"
  fi
else
  echo "  ✓ 项目名 0 命中"
fi

echo
echo "== ② 测试全量 =="
if [ -x "$PY" ]; then
  out=$(PYTHONPATH=src "$PY" -m pytest -q 2>&1); rc=$?
  echo "$out" | tail -2 | sed 's/^/  /'
  [ "$rc" -eq 0 ] || { echo "  ✗ 测试未过（rc=$rc）"; FAIL=1; }
else
  echo "  ⚠ 找不到解释器：$PY（用 GUANGSHU_PY 指定）"; FAIL=1
fi

echo
echo "== ③ 跨语言一致性断言（可选：给了 JS 与结果包路径才跑）=="
if [ -n "${GUANGSHU_METRICS_JS:-}" ] && [ -n "${GUANGSHU_PKG:-}" ]; then
  if node scripts/metrics_parity.mjs >/tmp/parity.out 2>&1; then
    tail -2 /tmp/parity.out | sed 's/^/  /'
  else
    echo "  ✗ 断言未过："; tail -6 /tmp/parity.out | sed 's/^/    /'; FAIL=1
  fi
else
  echo "  ⚠ 跳过（未给 GUANGSHU_METRICS_JS / GUANGSHU_PKG）"
fi

echo
echo "== ④ lint（装了 ruff 才跑）=="
if "$PY" -m ruff --version >/dev/null 2>&1; then
  if "$PY" -m ruff check . >/tmp/ruff.out 2>&1; then
    tail -1 /tmp/ruff.out | sed 's/^/  /'
  else
    echo "  ✗ ruff 有错："; tail -5 /tmp/ruff.out | sed 's/^/    /'; FAIL=1
  fi
else
  echo "  ⚠ 跳过（本环境没装 ruff）"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "════ 门通过：可以推 ════"
else
  echo "════ 门未过：先修，别推 ════"
fi
exit "$FAIL"
