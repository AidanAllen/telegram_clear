#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键体检 / 故障诊断

检查 7 项, 逐项输出 [OK]/[FAIL]/[WARN] 与修复建议:
  1. Python 与依赖(telethon)     2. 目录结构
  3. session 文件存在            4. session 有效性(真实连接测试)
  5. 网络到 Telegram DC 连通性    6. cron 定时任务
  7. 最近日志中的异常(FLOOD/未授权)

用法:
  python3 doctor.py
  python3 doctor.py --skip-net   # 跳过网络与登录测试(快速模式)
"""
import argparse
import asyncio
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.getenv("TG_CLEAN_DIR", SCRIPT_DIR)
LOG_DIR = os.getenv("TG_LOG_DIR", os.path.join(WORK_DIR, "log"))
CLEAN_LOG = os.path.join(LOG_DIR, "clean.log")

RESULTS = []


def record(name, ok, detail, fix=None):
    tag = "[OK]  " if ok is True else ("[WARN]" if ok is None else "[FAIL]")
    RESULTS.append((ok, name))
    print(f"{tag} {name}: {detail}")
    if fix and ok is not True:
        print(f"       修复: {fix}")


def check_python_deps():
    v = sys.version_info
    record("Python", v >= (3, 8), f"{v.major}.{v.minor}.{v.micro}",
           "升级到 Python 3.8+")
    try:
        import telethon
        record("telethon", True, f"v{telethon.__version__}")
    except ImportError:
        record("telethon", False, "未安装",
               "pip install telethon (或 ./setup.sh)")
    try:
        import socks  # noqa: F401  仅本地代理场景需要
        record("pysocks(可选)", True, "已安装(本地代理场景用)")
    except ImportError:
        record("pysocks(可选)", None, "未安装(服务器直连不需要)")


def check_dirs():
    for d in (LOG_DIR,):
        record(f"目录 {os.path.relpath(d, SCRIPT_DIR)}",
               os.path.isdir(d), d,
               f"mkdir -p {d}")
    wl = os.path.join(WORK_DIR, "whitelist.txt")
    record("whitelist.txt", os.path.exists(wl),
           "存在" if os.path.exists(wl) else "缺失",
           f"touch {wl}")


def find_sessions():
    """返回可用的 session: tg.session(原生) 或 gotd session.json"""
    native = os.path.join(WORK_DIR, "tg.session")
    gotd = os.path.expanduser(os.getenv(
        "TG_SESSION_JSON", "~/.telegram-mcp/session.json"))
    if os.path.exists(native):
        return native, "telethon 原生"
    if os.path.exists(gotd):
        return gotd, "gotd session.json"
    return None, None


def check_session_file():
    s, kind = find_sessions()
    if s:
        record("session 文件", True, f"{s} ({kind})")
        return s, kind
    record("session 文件", False, "未找到 tg.session / session.json",
           "python3 login.py  (一键登录)")
    return None, None


def check_net():
    """TCP 直连 Telegram DC"""
    import socket
    for host, port in (("149.154.167.51", 443), ("149.154.175.53", 443)):
        try:
            with socket.create_connection((host, port), timeout=8):
                record(f"网络 {host}", True, "可达")
                return True
        except OSError as e:
            record(f"网络 {host}", False, f"不可达 ({e})",
                   "服务器需在境外可直连 Telegram; 本地请配置代理")
    return False


async def check_session_alive(session_path, kind):
    from telethon import TelegramClient
    from auto_clean import load_session, load_api

    api_id, api_hash = load_api()
    try:
        sess = session_path if kind == "telethon 原生" else load_session()
        client = TelegramClient(sess, api_id, api_hash, timeout=15)
        await asyncio.wait_for(client.connect(), timeout=20)
        ok = await client.is_user_authorized()
        if ok:
            me = await client.get_me()
            record("session 有效性", True,
                   f"已授权, 账号 {me.first_name} (id {me.id})")
        else:
            record("session 有效性", False, "未授权(已过期或被吊销)",
                   "python3 login.py --new  重新登录")
        await client.disconnect()
    except Exception as e:
        record("session 有效性", False, f"{type(e).__name__}: {e}",
               "网络不通请先看上一项; 网络正常则是 session 损坏, "
               "python3 login.py --new 重新登录")


def check_cron():
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception as e:
        record("cron", None, f"无法读取 crontab ({e})")
        return
    hit = [l for l in out.splitlines()
           if "tg_clear" in l and not l.startswith("#")]
    if hit:
        record("cron 定时任务", True, hit[0].strip())
    else:
        record("cron 定时任务", None, "未配置(手动运行也可)",
               "crontab -e 添加: 0 4 * * * <项目路径>/run.sh")


def check_recent_log():
    if not os.path.exists(CLEAN_LOG):
        record("最近日志", None, "暂无运行记录")
        return
    try:
        with open(CLEAN_LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-200:]
    except Exception as e:
        record("最近日志", None, f"读取失败: {e}")
        return
    issues = []
    for l in lines:
        if re.search(r"限流|FLOOD_WAIT|FLOOD=[1-9]", l):
            issues.append(("限流", l.strip()[:80]))
        if "未授权" in l or (
                "session" in l.lower() and "error" in l.lower()):
            issues.append(("session 异常", l.strip()[:80]))
    if not issues:
        last = next((l.strip() for l in reversed(lines) if "执行结束" in l), "")
        record("最近日志", True, f"无异常。{last}")
    else:
        seen = {}
        for kind, text in issues:
            seen.setdefault(kind, text)
        record("最近日志", None,
               "; ".join(f"{k}: {t}" for k, t in seen.items()),
               "限流会自动等待重试; 频繁限流请把 SLEEP_BETWEEN 调大; "
               "session 异常则 login.py --new")


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--skip-net", action="store_true",
                   help="跳过网络与登录实测(快速模式)")
    args = a.parse_args()

    print("=" * 60)
    print("Telegram 失效群清理 · 一键体检")
    print("=" * 60)
    check_python_deps()
    check_dirs()
    s, kind = check_session_file()
    if not args.skip_net:
        if check_net() and s:
            asyncio.run(check_session_alive(s, kind))
    check_cron()
    check_recent_log()

    print("-" * 60)
    fails = sum(1 for ok, _ in RESULTS if ok is False)
    warns = sum(1 for ok, _ in RESULTS if ok is None)
    if fails == 0:
        print(f"结论: {warns} 个警告, 无致命问题 ✅")
        return 0
    print(f"结论: {fails} 个致命问题 ⛔, 按上面「修复」列处理")
    return 1


if __name__ == "__main__":
    sys.exit(main())
