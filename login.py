#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键登录 Telegram, 生成 session 文件

用法(服务器上, 需能直连 Telegram):
  python3 login.py                       # 交互输入 api_id/api_hash/手机号
  python3 login.py --api-id X --api-hash Y --phone +8613...
  python3 login.py --proxy socks5://127.0.0.1:1080   # 本地/需代理时

流程: 发送验证码 -> 你在 Telegram 客户端查收 -> 输入验证码 -> (如开2FA再输密码)
产物: 脚本同目录下 tg.session, auto_clean.py 自动优先使用它
"""
import argparse
import asyncio
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config():
    """api_id/api_hash 读取优先级: 命令行 > 环境变量 > config.json > 交互输入"""
    cfg = {}
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    if os.path.exists(cfg_path):
        try:
            import json
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[warn] config.json 解析失败, 忽略: {e}")
    return cfg


def ask(prompt, default=None):
    tip = f"{prompt}" + (f" [默认 {default}]" if default else "") + ": "
    v = input(tip).strip()
    return v or default


async def main(a):
    from telethon import TelegramClient

    cfg = load_config()

    api_id = a.api_id or os.getenv("TG_APP_ID") or cfg.get("api_id") or ask("api_id (my.telegram.org 获取)")
    api_hash = a.api_hash or os.getenv("TG_API_HASH") or cfg.get("api_hash") or ask("api_hash")
    phone = a.phone or os.getenv("TG_PHONE") or cfg.get("phone") or ask("手机号(国际格式, 如 +8613800138000)")
    if not (api_id and api_hash and phone):
        print("ERROR: api_id / api_hash / 手机号 缺失")
        return 1
    api_id = int(api_id)

    session_file = a.session or os.path.join(SCRIPT_DIR, "tg.session")
    if os.path.exists(session_file) and not a.new:
        print(f"已存在 {session_file}, 如需重新登录请加 --new")

    kwargs = {}
    if a.proxy:
        import socks
        host, port = a.proxy.rsplit(":", 1)
        scheme = "socks5"
        if "://" in host:
            scheme, host = host.split("://", 1)
        port = int(port)
        kwargs = dict(proxy=(socks.SOCKS5, host, port))
        print(f"使用代理: {scheme}://{host}:{port}")

    print(f"\n正在连接 Telegram (session -> {session_file}) ...")
    client = TelegramClient(session_file, api_id, api_hash, **kwargs)
    await client.start(phone=phone)   # 交互输入验证码; 若开2FA会再要密码

    me = await client.get_me()
    print("\n" + "=" * 50)
    print(f"登录成功: {me.first_name} (id {me.id})")
    print(f"session 已保存: {session_file}")
    print("=" * 50)
    print("\n下一步:")
    print("  python3 doctor.py     # 体检: session/网络/依赖/cron")
    print("  python3 auto_clean.py --once   # 试跑一次(默认只报告)")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Telegram 一键登录")
    p.add_argument("--api-id", help="Telegram api_id")
    p.add_argument("--api-hash", help="Telegram api_hash")
    p.add_argument("--phone", help="手机号(国际格式)")
    p.add_argument("--proxy", help="代理, 如 socks5://127.0.0.1:1080")
    p.add_argument("--session", help="session 文件路径(默认 ./tg.session)")
    p.add_argument("--new", action="store_true", help="覆盖已有 session 重新登录")
    sys.exit(asyncio.run(main(p.parse_args())))
