#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram 失效群自动清理 · 定时任务

功能: 全量探测对话 -> 复核空群 -> (可选)自动删除 -> 记录日志

用法:
  python3 auto_clean.py --once                # 跑一次就退出(配合 cron)
  python3 auto_clean.py --daemon --interval 24  # 常驻, 每 24 小时跑一次
  python3 auto_clean.py --once --auto-delete  # 跑一次并真正删除

安全设计:
  1. 默认【只报告不删除】, 必须显式 --auto-delete 才真删
  2. 白名单保护: whitelist 文件里的群永不删除
  3. 删除判定三重验证: 0 条消息 + hidden_prehistory=False + 成员数=0
  4. 单次删除数量上限 MAX_DELETE_PER_RUN, 异常时熔断
  5. restricted(敏感内容标记) 绝不当作失效

环境变量:
  TG_APP_ID / TG_API_HASH   凭证(优先级: 环境变量 > 工作目录 config.json)
  TG_SESSION_JSON           gotd session 路径 (默认 ~/.telegram-mcp/session.json)
  TG_CLEAN_DIR              工作目录 (默认 = 脚本所在目录)
  TG_LOG_DIR                日志目录 (默认 = 工作目录/log)

session 来源(优先级从高到低):
  1. <工作目录>/tg.session     telethon 原生 session, 由 login.py 生成
  2. TG_SESSION_JSON 指向的 gotd session.json (旧部署兼容)
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import time
import traceback
from datetime import datetime

from telethon import TelegramClient
from telethon.crypto import AuthKey
from telethon.errors import (ChannelInvalidError, ChannelPrivateError,
                             ChatForbiddenError, FloodWaitError,
                             UserDeactivatedError, UserDeletedError)
from telethon.sessions import StringSession
from telethon.tl.functions.channels import (GetFullChannelRequest,
                                            LeaveChannelRequest)
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.tl.types import (Channel, ChannelForbidden, Chat, ChatForbidden,
                               User)

# ---------------- 配置 ----------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_api():
    """api_id/api_hash: 环境变量 > config.json (工作目录下, 已被 .gitignore 排除)"""
    d = {}
    try:
        with open(os.path.join(_SCRIPT_DIR, "config.json"),
                  encoding="utf-8") as f:
            d.update({k: v for k, v in json.load(f).items()
                      if k in ("api_id", "api_hash")})
    except Exception:
        pass
    api_id = os.getenv("TG_APP_ID") or d.get("api_id")
    api_hash = os.getenv("TG_API_HASH") or d.get("api_hash")
    if not (api_id and api_hash):
        sys.exit('ERROR: 缺少 api_id/api_hash。请设置环境变量 TG_APP_ID / TG_API_HASH, '
                 '或在工作目录创建 config.json: {"api_id": 12345, "api_hash": "xxxx"} '
                 '(参考 my.telegram.org, 切勿提交到 git)')
    return int(api_id), api_hash


SESSION_JSON = os.getenv("TG_SESSION_JSON",
                         os.path.expanduser("~/.telegram-mcp/session.json"))
# 工作目录: 默认取脚本所在目录, 便于整体搬迁
WORK_DIR = os.getenv("TG_CLEAN_DIR", _SCRIPT_DIR)
LOG_DIR = os.getenv("TG_LOG_DIR", os.path.join(WORK_DIR, "log"))
LOG_FILE = os.path.join(LOG_DIR, "clean.log")
HISTORY_FILE = os.path.join(WORK_DIR, "history.json")
WHITELIST_FILE = os.path.join(WORK_DIR, "whitelist.txt")

READ_LIMIT = 50          # 每个对话尝试读取的消息数
SLEEP_BETWEEN = 0.6      # 探测间隔(秒), 避免触发限流
SLEEP_DELETE = 5         # 删除间隔(秒)
MAX_DELETE_PER_RUN = 50  # 单次最多删除数量(熔断保护)
FLOOD_WAIT_CAP = 60      # 遇到限流最多等待的秒数(超过则跳过)


def log(msg, also_print=True):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    if also_print:
        print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_whitelist():
    """白名单: 每行一个群名或用户名(可带@), 永不删除"""
    wl = set()
    if os.path.exists(WHITELIST_FILE):
        try:
            with open(WHITELIST_FILE, encoding="utf-8") as f:
                for line in f:
                    s = line.strip().lstrip("@")
                    if s and not s.startswith("#"):
                        wl.add(s)
        except Exception as e:
            log(f"读取白名单失败: {e}")
    return wl


def load_session():
    """返回 TelegramClient 可用的 session:
    优先 telethon 原生 tg.session(login.py 生成), 否则转换 gotd session.json"""
    native = os.path.join(WORK_DIR, "tg.session")
    if os.path.exists(native):
        return native  # 文件路径, TelegramClient 直接接受

    with open(SESSION_JSON, encoding="utf-8") as f:
        s = json.load(f)
    data = s["Data"]
    auth_key = base64.b64decode(data["AuthKey"])
    dc_id = data["DC"]
    cfg = data.get("Config") or {}
    ip, port = None, 443
    for o in (cfg.get("DCOptions") or []):
        if o.get("ID") == dc_id and not o.get("Ipv6") and not o.get("MediaOnly"):
            ip, port = o.get("IPAddress"), o.get("Port")
            break
    if not ip:
        for o in (cfg.get("DCOptions") or []):
            if o.get("ID") == dc_id and not o.get("Ipv6"):
                ip, port = o.get("IPAddress"), o.get("Port")
                break
    sess = StringSession()
    sess.set_dc(dc_id, ip, port)
    sess._auth_key = AuthKey(auth_key)
    return sess


# ---------------- 探测 ----------------
async def scan_all(client):
    """全量探测: 用预加载实体缓存读消息, 绕开解析限流"""
    log("拉取全部对话实体...")
    dialogs = await client.get_dialogs(limit=None)
    log(f"共 {len(dialogs)} 个对话, 开始逐个读取消息")

    results = []
    stat = {"OK": 0, "EMPTY": 0, "DEAD": 0, "FLOOD": 0, "ERROR": 0}

    for i, d in enumerate(dialogs, 1):
        e = d.entity
        title = getattr(e, "title", None) or getattr(e, "first_name", None) or ""
        uname = getattr(e, "username", None)
        kind = type(e).__name__
        rec = {"i": i, "id": getattr(e, "id", None), "title": title,
               "username": uname, "kind": kind, "status": "", "detail": ""}

        # 类型层判定
        if isinstance(e, (ChannelForbidden, ChatForbidden)):
            rec.update(status="DEAD", detail=f"{kind}: 不可访问")
            stat["DEAD"] += 1
        elif isinstance(e, User) and getattr(e, "deleted", False):
            rec.update(status="DEAD", detail="账号已注销")
            stat["DEAD"] += 1
        elif isinstance(e, Chat) and getattr(e, "deactivated", False):
            rec.update(status="DEAD", detail="群组已停用/解散")
            stat["DEAD"] += 1
        else:
            try:
                msgs = await client.get_messages(e, limit=READ_LIMIT)
                n = len(msgs) if msgs else 0
                if n == 0:
                    rec.update(status="EMPTY", detail="0 条消息")
                    stat["EMPTY"] += 1
                else:
                    rec.update(status="OK", detail=f"{n} 条消息")
                    stat["OK"] += 1
            except (ChannelInvalidError, ChannelPrivateError, ChatForbiddenError):
                rec.update(status="DEAD", detail="群组不存在/无权限")
                stat["DEAD"] += 1
            except (UserDeactivatedError, UserDeletedError):
                rec.update(status="DEAD", detail="账号已注销")
                stat["DEAD"] += 1
            except FloodWaitError as ex:
                rec.update(status="FLOOD", detail=f"限流 {ex.seconds}s")
                stat["FLOOD"] += 1
                wait = min(ex.seconds, FLOOD_WAIT_CAP)
                log(f"  [{i}] 限流, 等待 {wait}s")
                await asyncio.sleep(wait)
            except Exception as ex:
                rec.update(status="ERROR", detail=f"{type(ex).__name__}: {str(ex)[:50]}")
                stat["ERROR"] += 1

        if rec["status"] in ("DEAD", "EMPTY", "ERROR"):
            log(f"  [{i}] {rec['status']:5s} {title[:30]}  {rec['detail'][:36]}")
        elif i % 100 == 0:
            log(f"  ... 已处理 {i}/{len(dialogs)}")

        results.append(rec)
        await asyncio.sleep(SLEEP_BETWEEN)

    return {"total": len(dialogs), "stat": stat, "results": results}


# ---------------- 复核 ----------------
async def verify_empty(client, empties, whitelist):
    """三重验证: 0消息 + hidden_prehistory=False + 成员数=0"""
    log(f"复核 {len(empties)} 个空群...")
    confirmed, skipped = [], []

    for r in empties:
        title, uname = r["title"], r.get("username")
        if title in whitelist or (uname and uname in whitelist):
            skipped.append({**r, "reason": "白名单保护"})
            log(f"  [跳过] {title[:30]} (白名单)")
            continue

        try:
            ent = await client.get_entity(r["id"]) if r.get("id") else None
        except Exception:
            ent = None
        if ent is None:
            skipped.append({**r, "reason": "无法获取实体"})
            continue

        n = 0
        try:
            msgs = await client.get_messages(ent, limit=READ_LIMIT)
            n = len(msgs) if msgs else 0
        except Exception:
            pass

        hidden = None
        members = None
        try:
            full = await client(GetFullChannelRequest(ent))
            hidden = getattr(full.full_chat, "hidden_prehistory", None)
            members = getattr(full.full_chat, "participants_count", None)
        except Exception as ex:
            log(f"  [warn] 获取频道信息失败 {title[:24]}: {type(ex).__name__}")

        if hidden is True:
            r.update(verdict="频道正常(隐藏历史)", msgs=n, members=members)
            skipped.append(r)
            log(f"  [保留] {title[:28]:30s} 隐藏历史=True (频道正常)")
        elif n > 0:
            r.update(verdict="正常(有消息)", msgs=n, members=members)
            skipped.append(r)
        else:
            if members == 0:
                r.update(verdict="确实为空(可删)", msgs=0, members=0, entity_id=r["id"])
                confirmed.append(r)
                log(f"  [可删] {title[:28]:30s} msgs=0 members=0")
            else:
                r.update(verdict=f"待观察(成员数={members})", msgs=0, members=members)
                skipped.append(r)
                log(f"  [保留] {title[:28]:30s} msgs=0 但成员数={members}")

        await asyncio.sleep(2)

    return confirmed, skipped


# ---------------- 删除 ----------------
async def do_delete(client, targets, dry_run=True):
    log(f"{'[预览] ' if dry_run else ''}处理 {len(targets)} 个待删对话")
    if dry_run:
        for i, t in enumerate(targets, 1):
            log(f"  预览 {i}. {t['title']}")
        return 0, len(targets)

    cache = {}
    try:
        dialogs = await client.get_dialogs(limit=None)
        for d in dialogs:
            e = d.entity
            cache[getattr(e, "id", None)] = e
    except Exception as e:
        log(f"预加载失败: {e}")

    ok = fail = 0
    for i, t in enumerate(targets[:MAX_DELETE_PER_RUN], 1):
        ent = cache.get(t.get("entity_id") or t.get("id"))
        try:
            if ent is None:
                ent = await client.get_entity(t.get("entity_id") or t["id"])
            if isinstance(ent, Channel):
                await client(LeaveChannelRequest(ent))
                act = "退出频道"
            else:
                await client(DeleteHistoryRequest(peer=ent, max_id=0,
                                                  just_clear=False, revoke=False))
                act = "删除对话"
            ok += 1
            log(f"  ✓ [{i}] {t['title'][:30]:32s} {act}")
        except FloodWaitError as e:
            fail += 1
            log(f"  ✗ [{i}] {t['title'][:28]} 限流 {e.seconds}s")
            await asyncio.sleep(min(e.seconds, FLOOD_WAIT_CAP))
        except Exception as e:
            fail += 1
            log(f"  ✗ [{i}] {t['title'][:28]} {type(e).__name__}: {str(e)[:40]}")
        await asyncio.sleep(SLEEP_DELETE)
    return ok, fail


# ---------------- 主流程 ----------------
async def run_once(client, auto_delete=False):
    started = datetime.now()
    log("=" * 55)
    log(f"开始执行  (自动删除={'开' if auto_delete else '关(仅报告)'})")

    whitelist = load_whitelist()
    if whitelist:
        log(f"白名单 {len(whitelist)} 条")

    scan = await scan_all(client)
    stat = scan["stat"]
    log(f"探测完成: OK={stat['OK']} EMPTY={stat['EMPTY']} "
        f"DEAD={stat['DEAD']} FLOOD={stat['FLOOD']} ERROR={stat['ERROR']}")

    targets = [r for r in scan["results"] if r["status"] in ("DEAD", "EMPTY")]
    confirmed, skipped = await verify_empty(client, targets, whitelist)
    log(f"复核完成: 确认可删 {len(confirmed)} 个, 保留 {len(skipped)} 个")

    ok = fail = 0
    if confirmed:
        ok, fail = await do_delete(client, confirmed, dry_run=not auto_delete)
        log(f"删除结果: 成功 {ok}, 失败 {fail}")
    else:
        log("无需删除")

    rec = {
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().isoformat(timespec="seconds"),
        "total": scan["total"], "stat": stat,
        "confirmed": len(confirmed), "deleted": ok, "failed": fail,
        "auto_delete": auto_delete,
        "deleted_titles": [t["title"] for t in confirmed[:ok]],
    }
    try:
        hist = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, encoding="utf-8") as f:
                hist = json.load(f)
        hist.append(rec)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist[-200:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"写入历史失败: {e}")

    with open(os.path.join(WORK_DIR, "last_report.json"), "w", encoding="utf-8") as f:
        json.dump({**rec, "confirmed_list": confirmed, "skipped_list": skipped},
                  f, ensure_ascii=False, indent=2)

    log(f"执行结束, 耗时 {(datetime.now() - started).total_seconds():.0f}s")
    return rec


async def amain(args):
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    api_id, api_hash = load_api()
    try:
        sess = load_session()
    except FileNotFoundError:
        log("ERROR: 找不到 session (tg.session / session.json 均缺失)")
        log("       一键登录: python3 login.py")
        return 1
    except Exception as e:
        log(f"ERROR: 加载 session 失败: {type(e).__name__}: {e}")
        log("       重新登录: python3 login.py --new")
        return 1
    client = TelegramClient(sess, api_id, api_hash,
                            connection_retries=5, timeout=30, request_retries=3)
    await client.connect()
    if not await client.is_user_authorized():
        log("ERROR: session 未授权(已过期或被吊销)")
        log("       重新登录: python3 login.py --new")
        return 1
    me = await client.get_me()
    log(f"账号: {me.first_name} (id {me.id})")

    try:
        if args.daemon:
            log(f"常驻模式启动, 每 {args.interval} 小时执行一次")
            while True:
                try:
                    await run_once(client, args.auto_delete)
                except Exception:
                    log("执行异常:\n" + traceback.format_exc())
                log(f"下次执行: {args.interval} 小时后")
                await asyncio.sleep(args.interval * 3600)
        else:
            await run_once(client, args.auto_delete)
    finally:
        await client.disconnect()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Telegram 失效群自动清理")
    p.add_argument("--once", action="store_true", help="跑一次就退出(配合 cron)")
    p.add_argument("--daemon", action="store_true", help="常驻循环")
    p.add_argument("--interval", type=float, default=24, help="常驻间隔小时数")
    p.add_argument("--auto-delete", action="store_true",
                   help="真正删除(默认只报告不删除)")
    a = p.parse_args()
    if not a.once and not a.daemon:
        a.once = True
    sys.exit(asyncio.run(amain(a)))
