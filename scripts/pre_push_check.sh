#!/bin/bash
# 推送门 —— 推之前必须过这三件。推不过就别推。
#
# 用法：bash scripts/pre_push_check.sh [基线ref，默认 origin/main]
# 词表外置（2026-10-27）：仓内不放任何个人/项目关键词。来源＝环境变量 HARD_KW / SOFT_KW →
# 仓外文件（$GUANGSHU_SCAN_WORDS_FILE 或 $HOME/.optiflow/scan-words.env，两行 HARD_KW='...' / SOFT_KW='...'）。
# 反面教材：词表原先硬写在仓里，而本脚本又被自身扫描排除，门永远发现不了自己。
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
_WORDS_FILE="${GUANGSHU_SCAN_WORDS_FILE:-$HOME/.optiflow/scan-words.env}"
if [ -f "$_WORDS_FILE" ]; then . "$_WORDS_FILE"; fi
# 通用本机路径（不含个人信息）：POSIX 家目录 + Windows 用户目录；已脱敏的 Public / <user> 占位放行
_HARD_DEFAULT='/Users/|C:\\Users\\'
_SAFE_PLACEHOLDER='C:\\Users\\(Public|<user>)|C:/Users/(Public|<user>)'
HARD_KW="${HARD_KW:-$_HARD_DEFAULT}"
SOFT_KW="${SOFT_KW:-}"
PUBLIC="${PUBLIC:-1}"
if [ -z "$SOFT_KW" ]; then
  echo "  ⚠ 项目名词表未配置（$_WORDS_FILE）→ 软门未执行，按未过处理"
  FAIL=1
fi
if ! git rev-parse --verify "$BASE" >/dev/null 2>&1; then
  echo "  ⚠ 基线 $BASE 不存在 → 改为全树扫（首次推送前就是这样）"
  SCAN_CMD="git grep -InE"
  SCAN_ARGS=""   # 交给 git grep 自己找
  hits_hard=$(git grep -InE "$HARD_KW" -- . ':!scripts/pre_push_check.sh' 2>/dev/null | grep -vE "$_SAFE_PLACEHOLDER" | wc -l | tr -d ' ')
  if [ -n "$SOFT_KW" ]; then
    hits_soft=$(git grep -InE "$SOFT_KW" -- . 2>/dev/null | wc -l | tr -d ' ')
  else
    hits_soft=0
  fi
else
  hits_hard=$(git diff "$BASE"..HEAD -- . ':!scripts/pre_push_check.sh' 2>/dev/null | grep -E '^\+' | grep -vE "$_SAFE_PLACEHOLDER" | grep -Ec "$HARD_KW" || true)
  if [ -n "$SOFT_KW" ]; then
    hits_soft=$(git diff "$BASE"..HEAD -- . 2>/dev/null | grep -E '^\+' | grep -Ec "$SOFT_KW" || true)
  else
    hits_soft=0
  fi
fi
if [ "${hits_hard:-0}" -gt 0 ]; then
  echo "  ✗ 本机路径命中 $hits_hard 行（任何仓都不该留）"
  git grep -InE "$HARD_KW" -- . ':!scripts/pre_push_check.sh' | grep -vE "$_SAFE_PLACEHOLDER" | head -8 | cut -c1-150 | sed 's/^/    /'
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
echo "== ⑤ 跨仓同图断言（解析器受控收编把关，给 GUANGSHU_DC 才跑）=="
if [ -n "${GUANGSHU_DC:-}" ]; then
  if "$PY" scripts/cross_parser_parity.py >/tmp/parity_parser.out 2>&1; then
    tail -3 /tmp/parity_parser.out | sed 's/^/  /'
  else
    echo "  ✗ 收编副本与真相源分叉："; tail -12 /tmp/parity_parser.out | sed 's/^/    /'; FAIL=1
  fi
else
  echo "  ⚠ 跳过（未给 GUANGSHU_DC —— src/parser 收编副本没与 dialux-compiler 真相源比过）"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "════ 门通过：可以推 ════"
else
  echo "════ 门未过：先修，别推 ════"
fi
exit "$FAIL"
