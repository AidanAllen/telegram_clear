# Telegram 失效群自动清理

自动检测并清理 Telegram 账号里「不可访问、没有任何信息」的群聊/频道。
**三条命令完成部署**：安装 → 登录 → 自动运行，出问题一键体检。

```bash
bash setup.sh      # ① 一键安装(依赖+目录+定时任务)
python3 login.py   # ② 一键登录(收验证码, 生成 session)
python3 doctor.py  # ③ 一键体检(7 项自检, 出问题看这里)
```

> 需要部署在**能直连 Telegram 的服务器**上（境外 VPS）。
> 本地 Windows 也可运行（`login.py --proxy socks5://127.0.0.1:1080`），但定时任务建议放服务器。

---

## 目录

- [快速开始](#快速开始)
- [命令速查](#命令速查)
- [工作原理](#工作原理)
- [问题处理](#问题处理)
- [安全机制](#安全机制)
- [配置](#配置)
- [项目结构](#项目结构)
- [常见问题 FAQ](#常见问题-faq)

---

## 快速开始

### 第 0 步：准备凭证

登录需要 `api_id` 和 `api_hash`（只需一次，2 分钟）：

1. 打开 https://my.telegram.org ，用手机号登录
2. 进入「API development tools」，随便填个应用名提交
3. 记下 **App api_id**（数字）和 **App api_hash**（字母数字）

### 第 1 步：上传并安装

把整个项目传到服务器（`scp` / `sftp` 均可），然后：

```bash
bash setup.sh
```

自动完成：venv 虚拟环境 → telethon 依赖 → `log/` 目录 → 白名单初始化 → cron 定时任务（每天 04:00）。
**幂等设计**，重复执行无副作用，依赖装过会跳过。

### 第 2 步：登录（生成 session）

```bash
python3 login.py
# 或显式传参:
python3 login.py --api-id 12345 --api-hash abcdef --phone +8613800138000
```

流程：发送验证码 → 打开你的 Telegram 查收 → 输入验证码 →（若开了两步验证再输密码）。
成功后生成 `tg.session`，脚本自动优先使用。

### 第 3 步：体检 + 试跑

```bash
python3 doctor.py              # 7 项自检: 依赖/目录/session/网络/cron/日志
python3 auto_clean.py --once   # 试跑一次(只报告, 不删除)
bash run.sh                    # 正式后台运行(自动删除模式)
```

至此部署完成，之后每天 04:00 自动运行，无需任何人工干预。

---

## 命令速查

| 命令 | 作用 |
|------|------|
| `bash setup.sh` | 一键安装/修复环境（幂等） |
| `python3 login.py` | 一键登录生成 session（重登录加 `--new`） |
| `python3 doctor.py` | 一键体检诊断（快速模式 `--skip-net`） |
| `bash run.sh` | 手动后台跑一次（自动删除模式） |
| `python3 auto_clean.py --once` | 前台跑一次（默认只报告） |
| `python3 auto_clean.py --once --auto-delete` | 前台跑一次并删除 |
| `python3 auto_clean.py --daemon --interval 12` | 常驻进程模式（替代 cron） |
| `tail -f log/clean.log` | 实时看运行日志 |

查看最近一次报告：

```bash
python3 - << 'PY'
import json
d = json.load(open("last_report.json"))
print(d["stat"])
print("已删:", d["deleted"], "明细:", d["deleted_titles"])
PY
```

---

## 工作原理

```
cron (每天 04:00)
  └─ run.sh (setsid nohup, 断开 SSH 不受影响)
       └─ auto_clean.py --once --auto-delete
            ① get_dialogs() 一次性预加载全部实体缓存   ← 绕开限流的关键
            ② 逐个读消息, 分类: OK / EMPTY / DEAD / FLOOD
            ③ 对 EMPTY 做三重复核
            ④ 三重验证全过才执行删除(退出频道/删对话)
            ⑤ 写报告 last_report.json 与日志 log/
```

### 判定标准（三重验证，缺一不删）

| # | 条件 | 含义 |
|---|------|------|
| 1 | `get_messages` 返回 0 条 | 读不到任何消息 |
| 2 | `hidden_prehistory = False` | 不是"新成员隐藏历史"（那种频道正常） |
| 3 | `participants_count = 0` | 0 成员 = 频道已废弃 |

> ⚠️ **`restricted` 标志 ≠ 失效**。它是 Telegram 的敏感内容/年龄限制标记，
> 这类群能正常访问、消息照常可读，脚本绝不将其当作失效。

---

## 问题处理

**第一反应永远是**：

```bash
python3 doctor.py
```

7 项自检逐项给出 `[OK]/[WARN]/[FAIL]` 和对应修复命令。对照表：

| 症状 | doctor 显示 | 修复 |
|------|-------------|------|
| 依赖缺失 | `telethon: 未安装` | `bash setup.sh` |
| 从未登录 / session 丢失 | `session 文件: 未找到` | `python3 login.py` |
| session 过期或被吊销 | `session 有效性: 未授权` | `python3 login.py --new` |
| 服务器连不上 Telegram | `网络 149.154.x.x: 不可达` | 换可直连 Telegram 的境外服务器 |
| 忘了有没有配定时 | `cron 定时任务: 未配置` | `bash setup.sh`（会自动补） |
| 频繁限流 | `最近日志: 限流` | 脚本会自动等待重试；频繁出现把 `SLEEP_BETWEEN` 调大到 1~2 |
| 没跑过的痕迹 | `最近日志: 暂无运行记录` | `bash run.sh` 跑一次看 `log/clean.log` |

### 手动排查命令

```bash
tail -50 log/clean.log                     # 最近日志
tail -20 log/cron.log                      # cron 输出(含报错栈)
crontab -l                                 # 定时任务
ps -eo cmd | grep 'auto[_]clean'           # 是否在运行(注意[_]写法)
```

---

## 安全机制

| 机制 | 说明 |
|------|------|
| 三重验证 | 0 条消息 + `hidden_prehistory=False` + 0 成员，缺一不删 |
| 白名单 | `whitelist.txt` 命中的群永不删除（一行一个群名/用户名） |
| 熔断上限 | 单次最多删 50 个（`MAX_DELETE_PER_RUN`） |
| restricted 不误判 | 敏感内容标记绝不当作失效 |
| 默认只报告 | 直接运行 `auto_clean.py` 不加 `--auto-delete` 绝不删东西 |

> 已删除的群：公开频道可通过用户名重新加入；无用户名的私密群移除后无法恢复。
> 建议首次使用先跑几天"只报告"模式观察，再开自动删除。

---

## 配置

### 定时任务

`setup.sh` 默认配 `0 4 * * *`（每天 04:00）。想改时间，编辑 `setup.sh` 顶部 `RUN_AT` 重跑，
或直接 `crontab -e` 修改：

```
0 4 * * * /path/to/tg_clear/run.sh  # tg失效群清理(自动删除)
```

> `crontab -e` 编辑过快会报 `No modification made`（时间戳同秒判定），稍等几秒再保存。

想退回"只报告"模式：编辑 `run.sh` 删掉 `--auto-delete` 即可，**不用动 crontab**。

### 运行参数（auto_clean.py 顶部常量）

```python
READ_LIMIT = 50          # 每个对话读取的消息数
SLEEP_BETWEEN = 0.6      # 探测间隔(秒), 调大更保守
SLEEP_DELETE = 5         # 删除间隔(秒)
MAX_DELETE_PER_RUN = 50  # 单次删除上限(熔断保护)
FLOOD_WAIT_CAP = 60      # 遇限流最多等待秒数
```

### 凭证与路径（环境变量，一般不用动）

```bash
TG_APP_ID / TG_API_HASH   # 凭证(也可写 config.json: {"api_id":..,"api_hash":..})
TG_SESSION_JSON           # gotd 旧 session 路径(默认 ~/.telegram-mcp/session.json)
TG_CLEAN_DIR              # 工作目录(默认 = 脚本所在目录)
TG_LOG_DIR                # 日志目录(默认 = 工作目录/log)
```

### pip 依赖

```bash
pip install -r requirements.txt
# 或分开装:
pip install telethon    # 服务器端运行(PEP 668 环境用 venv, setup.sh 已处理)
pip install paramiko    # 本地 Windows 用 ssh_util.py 管理时
pip install pysocks     # 仅本地走 socks 代理登录时需要
```

---

## 项目结构

```
├── setup.sh          # 一键安装(venv+依赖+目录+cron)
├── login.py          # 一键登录(生成 tg.session)
├── doctor.py         # 一键体检/故障诊断(7 项自检)
├── auto_clean.py     # 主脚本: 探测→复核→删除→报告
├── run.sh            # 启动器(cron 入口, 路径自适应)
├── requirements.txt  # pip 依赖
├── whitelist.txt     # 白名单(运行后生成, 可编辑)
└── log/              # 日志目录
    ├── clean.log     #   脚本运行日志
    └── cron.log      #   cron/run.sh 输出
```

产物：`last_report.json`（最近报告）、`history.json`（历史 200 条）、`tg.session`（登录凭证）。

历史排查工具（可选保留）：`scan_all.py` / `verify_empty.py` / `delete_groups.py` /
`scan_states.py` / `probe_deep.py` / `detect_dead_groups.py` / `tg_probe.py`。

### 从本地 Windows 管理服务器

```bash
python -m pip install paramiko        # 本地依赖
export MSYS_NO_PATHCONV=1             # Git Bash 必需, 否则 /root 被转 Windows 路径
export TG_SSH_HOST=<服务器IP或域名> TG_SSH_USER=<用户名> TG_SSH_PASSWORD='<密码>'
python ssh_util.py exec "<命令>"
python ssh_util.py put <本地文件> <远程路径>
python ssh_util.py get <远程文件> <本地文件>
```

---

## 常见问题 FAQ

**Q: 服务器在国内/连不上 Telegram 怎么办？**
登录和运行都需要直连 Telegram 的 MTProto 服务器。换境外 VPS，或 `login.py --proxy socks5://host:port`（需 `pip install pysocks`）。

**Q: 会被 Telegram 封号吗？**
每天 1 次全量、探测间隔 0.6s 的节奏在实测中零限流。不要并发多实例、不要提高频率。
脚本内置 FLOOD 自动等待与 50 个/次的删除熔断。

**Q: session 能用多久？**
正常可长期使用。异地登录、Telegram 安全策略、长期不活跃可能导致失效——
`doctor.py` 会检测出来，跑 `login.py --new` 重新登录即可。

**Q: 误删了想要的群怎么办？**
公开频道（有用户名）可重新加入；私密群不可恢复。**把重要群加进 `whitelist.txt`**。

**Q: 为什么探测某些群返回 FLOOD？**
脚本会自动等待重试。历史教训：限流打在"按用户名解析实体"上（曾触发 22 小时禁令），
本项目的 `get_dialogs()` 预加载实体方案已绕开该限制。

**Q: 想看每天删了什么？**
`last_report.json` 的 `deleted_titles` 字段；或 `grep '✓' log/clean.log`。

---

## 已知坑（部署经验）

1. `pkill -f auto_clean.py` 会把含该关键字的 SSH 命令自己也杀掉；检查进程用 `ps -eo cmd | grep 'auto[_]clean'`。
2. Ubuntu 24+ 的 PEP 668 禁止系统 pip 装包 → `setup.sh` 自动用 venv 规避。
3. gotd/`telegram-mcp.exe` 连 MTProto 不读 `HTTP_PROXY`，须 `ALL_PROXY=socks5://...`（仅本地场景需要，本项目已改用 Telethon 原生登录）。
4. 「对话列表无 last_message 时间」≠ 失效，接口常不返回该字段。
