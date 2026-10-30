#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务器 SSH 工具: 执行命令 / 上传文件 / 拉取文件

用法:
  python ssh_util.py exec "<命令>"
  python ssh_util.py put  <本地文件> [远程路径]
  python ssh_util.py get  <远程文件> <本地文件>
"""
import os
import sys

HOST = os.getenv("TG_SSH_HOST", "")      # 必须通过环境变量指定
PORT = int(os.getenv("TG_SSH_PORT", "22"))
USER = os.getenv("TG_SSH_USER", "root")
PASSWORD = os.getenv("TG_SSH_PASSWORD", "")
REMOTE_DIR = os.getenv("TG_SSH_DIR", "/root")


def connect():
    if not HOST or not PASSWORD:
        sys.exit("ERROR: 请先设置 TG_SSH_HOST / TG_SSH_USER / TG_SSH_PASSWORD 环境变量")
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, PORT, USER, PASSWORD, timeout=30)
    return c


def do_exec(cmd, timeout=300):
    c = connect()
    try:
        _, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", errors="replace")
        err = e.read().decode("utf-8", errors="replace")
        print(out, end="")
        if err.strip():
            print("--- STDERR ---")
            print(err[:2000])
    finally:
        c.close()


def do_put(local, remote=None):
    """通过 base64 走命令通道上传(SFTP 子系统不可用时更可靠)"""
    import base64
    c = connect()
    try:
        remote = remote or ("/root/" + os.path.basename(local))
        with open(local, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        # 分片写入, 避免命令行过长
        chunk = 4000
        c.exec_command(f"rm -f {remote}.b64", timeout=30)
        for i in range(0, len(b64), chunk):
            part = b64[i:i + chunk]
            c.exec_command(f"printf '%s' '{part}' >> {remote}.b64", timeout=30)
        _, o, e = c.exec_command(
            f"base64 -d {remote}.b64 > {remote} && rm -f {remote}.b64 "
            f"&& wc -c {remote}", timeout=60)
        print(o.read().decode(errors="replace"), end="")
        err = e.read().decode(errors="replace")
        if err.strip():
            print("ERR:", err[:300])
        print(f"uploaded -> {remote}")
    finally:
        c.close()


def do_get(remote, local):
    """通过 cat 走命令通道下载"""
    c = connect()
    try:
        _, o, e = c.exec_command(f"cat {remote}", timeout=120)
        data = o.read()
        with open(local, "wb") as f:
            f.write(data)
        print(f"downloaded -> {local} ({len(data)} bytes)")
    finally:
        c.close()


def do_bg(cmd, logfile):
    """fire-and-forget 后台启动"""
    c = connect()
    try:
        tr = c.get_transport()
        ch = tr.open_session()
        ch.exec_command(
            f"cd {REMOTE_DIR} && nohup {cmd} > {logfile} 2>&1 < /dev/null &")
        ch.close()
        print(f"started -> {logfile}")
    finally:
        c.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    action = sys.argv[1]
    if action == "exec":
        do_exec(sys.argv[2])
    elif action == "bg":
        do_bg(sys.argv[2], sys.argv[3])
    elif action == "put":
        do_put(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif action == "get":
        do_get(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
