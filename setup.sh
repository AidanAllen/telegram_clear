#!/bin/bash
# 一键安装: 依赖 + 目录 + 白名单 + (可选)cron
# 在【能直连 Telegram 的服务器】上执行:  bash setup.sh
set -e

BASE="$(cd "$(dirname "$0")" && pwd)"
VENV="$BASE/tgvenv"
PY="$VENV/bin/python"
RUN_AT="04:00"                      # 每日运行时刻, 可改

echo "=========================================="
echo " Telegram 失效群自动清理 · 一键安装"
echo " 项目目录: $BASE"
echo "=========================================="

# ---------- 1. Python ----------
if ! command -v python3 >/dev/null 2>&1; then
    echo "[FAIL] 未找到 python3, 请先安装: apt install -y python3 python3-venv"
    exit 1
fi
echo "[1/6] python3 $(python3 -V 2>&1 | cut -d' ' -f2) ✓"

# ---------- 2. venv + 依赖 ----------
if [ ! -x "$PY" ]; then
    echo "[2/6] 创建虚拟环境 $VENV ..."
    python3 -m venv "$VENV" || {
        echo "      venv 模块缺失, 安装: apt install -y python3-venv"; exit 1; }
fi
echo "[2/6] 安装依赖 telethon ..."
"$VENV/bin/pip" install -q --upgrade pip 2>/dev/null || true
"$VENV/bin/pip" install -q telethon
"$PY" -c "import telethon; print('      telethon', telethon.__version__, '✓')"

# ---------- 3. 目录结构 ----------
echo "[3/6] 初始化目录 ..."
mkdir -p "$BASE/log"
[ -f "$BASE/whitelist.txt" ] || \
    printf '%s\n' '# 白名单: 每行一个群名或用户名(可带@), 命中的永不删除' \
        '# 例: 我的重要群' > "$BASE/whitelist.txt"
echo "      log/ 与 whitelist.txt 就绪 ✓"

# ---------- 4. cron ----------
echo "[4/6] 配置 cron (每天 $RUN_AT 运行一次) ..."
CRON_LINE="0 ${RUN_AT%%:*} * * * $BASE/run.sh  # tg失效群清理(自动删除)"
if crontab -l 2>/dev/null | grep -qF "$BASE/run.sh"; then
    echo "      已存在, 跳过 ✓"
else
    (crontab -l 2>/dev/null | grep -vF "$BASE/run.sh"; echo "$CRON_LINE") | crontab -
    echo "      已添加: $CRON_LINE"
fi

# ---------- 5. 现有定时任务(纯展示) ----------
echo "[5/6] 当前 crontab:"
crontab -l 2>/dev/null | grep -vE '^#|^$' | sed 's/^/      /' || echo "      (空)"

# ---------- 6. 下一步指引 ----------
echo "[6/6] 安装完成 ✓"
echo
echo "=========================================="
echo " 下一步(首次使用必做):"
echo "   $PY $BASE/login.py"
echo "   → 输入 api_id / api_hash / 手机号, 到 Telegram 收验证码"
echo
echo " 常用命令:"
echo "   $PY $BASE/doctor.py          # 一键体检(问题诊断)"
echo "   $BASE/run.sh                 # 手动跑一次(后台)"
echo "   tail -f $BASE/log/clean.log  # 看实时日志"
echo
echo " 默认【自动删除已开启】(run.sh 内含 --auto-delete),"
echo " 想改为只报告不删除: 编辑 run.sh 删掉 --auto-delete 即可。"
echo "=========================================="
