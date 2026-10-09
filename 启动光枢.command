#!/bin/bash
# 光枢 / OptiFlow 一键启动（macOS）
# 双击运行：起本地服务并自动打开浏览器；关掉本窗口服务即停。
#
# 依赖：Python 3.11+（见 requirements.txt）。
# 解释器解析顺序：OPTIFLOW_PYTHON 环境变量 → 常见 conda 环境 → PATH 上的 python3。
cd "$(dirname "$0")" || exit 1
export PYTHONPATH=src
PORT="${1:-8765}"
URL="http://127.0.0.1:${PORT}"

resolve_python() {
  if [ -n "${OPTIFLOW_PYTHON:-}" ]; then
    printf '%s' "$OPTIFLOW_PYTHON"; return
  fi
  for env in "$HOME/anaconda3/envs/workshop" "$HOME/miniconda3/envs/workshop"; do
    [ -x "$env/bin/python3" ] && { printf '%s' "$env/bin/python3"; return; }
  done
  command -v python3
}

PY="$(resolve_python)"
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  echo "未找到可用的 Python 解释器。"
  echo "请安装 Python 3.11+，或设置 OPTIFLOW_PYTHON=/path/to/python3 后重试。"
  read -r -p "按回车退出…" _
  exit 1
fi

echo "光枢 / OptiFlow 启动中…"
echo "解释器：${PY}"
echo "浏览器地址：${URL}"
echo "（用完后关掉这个窗口即可停止服务）"
echo ""

(sleep 1.5 && open "${URL}") &
exec "$PY" -m optiflow.api --port "${PORT}"
