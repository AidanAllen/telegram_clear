#!/bin/bash
# Telegram 失效群清理 - 启动器(供 cron / 手动调用)
# 路径自适应: 放在哪就在哪运行, 无需修改
BASE="$(cd "$(dirname "$0")" && pwd)"
PY="$BASE/tgvenv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

# --auto-delete: 三重验证(0条消息+非隐藏历史+0成员)全过才删
# 想退回"只报告"模式, 删掉 --auto-delete 即可, 不用动 crontab
setsid nohup "$PY" -u "$BASE/auto_clean.py" --once --auto-delete \
    >> "$BASE/log/cron.log" 2>&1 < /dev/null &
echo "started pid $!  (日志: $BASE/log/)"
exit 0
