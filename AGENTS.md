# AGENTS.md

面向在此仓库工作的 AI/编码代理的上下文说明。

## 项目是什么

基于合宙 Air780E（4G Cat.1）模组的短信收发/转发桥：

- 通过 USB 串口（cdc_acm，USB ID `19d1:0001` BYD EigenComm Compo）以 AT 命令与模组通信
- 只使用短信类 AT 命令（`AT+CMGF/CMGS/CMGL/CMGD` 等），**不使用 PPP/NCM 数据拨号，不消耗 SIM 流量**
- 提供 REST API（`/api/*`）与静态单页管理界面
- 收到短信入库 SQLite（WAL）后通过 Apprise 推送通知（钉钉/企微/飞书/Telegram/Email/自定义 Apprise/Webhook），另支持独立 Webhook HTTP 直推
- 支持定时短信任务、联系人、API 密钥、管理端 JWT、来电（+CLIP）通知

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + Uvicorn（Python 3.12，`.venv`） |
| 串口 | pyserial（单读者线程 + 命令锁） |
| 数据库 | SQLite（WAL，线程安全，路径由 `DB_PATH` 指定） |
| 通知 | apprise + httpx（webhook 直发） |
| 鉴权 | PyJWT（HS256，有效期 `JWT_EXPIRES_HOURS`）+ PBKDF2-SHA256（24 万轮） |
| 前端 | 静态单页（无构建步骤）：`index.html` + `app.js` + `i18n.js` + `style.css` |

依赖锁定在 `requirements.txt`（精确版本）。Docker 镜像：`ghcr.io/znhocn/air780e-bridge`（GitHub Actions 自动发布）。

## 目录结构

```
app/
  main.py        FastAPI 入口（lifespan 启动 worker/scheduler，挂载所有 router + 静态目录）
  config.py      环境变量配置（Settings 单例 `settings`）
  database.py    SQLite 封装（cursor 上下文管理器，write 加锁）
  atdevice.py    串口 AT 层（命令锁、超时、CMGS 提示符、UCS2/GSM 7bit）
  worker.py      后台线程（SerialWorker）：重连、轮询收短信、发送队列、状态采集
  forwarder.py   Forwarder：Apprise 推送 + Webhook + 来电/短信通知 + 转发日志
  scheduler.py   SchedulerService：定时短信任务
  auth.py        管理端 JWT + API Key 鉴权（`authenticate` / `require_admin_user`）
  schema.py      Pydantic 请求/响应模型
  version.py     项目版本号（发版时同步修改）
  cli.py         不经 Web 直接调试硬件的命令行
  api/           REST 路由（auth/messages/device/keys/notify/tasks/contacts）
  static/        前端单页（index.html / app.js / i18n.js / style.css / _audit.js）
udev/            串口权限/避让 ModemManager 规则（99-air780e-eigencomm.rules，gitignored）
scripts/         test_send_10086.sh（gitignored）
docs/            API.md（REST API 完整文档，改动端点后同步更新）
data/            SQLite 数据库（gitignored）
```

## 环境变量（app/config.py）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEVICE_PORT` | `auto` | `auto` 时在 `DEVICE_PROBE_PORTS` 里探测首个响应 `AT` 的口 |
| `DEVICE_BAUDRATE` | `115200` | 波特率 |
| `DEVICE_PROBE_PORTS` | `/dev/ttyACM0,/dev/ttyACM1,/dev/ttyACM2` | 逗号分隔候选串口 |
| `DB_PATH` | `/data/bridge.db` | SQLite 路径（本地开发常用 `./data/bridge.db`） |
| `JWT_SECRET` | 空 | 为空时首次启动自动生成并入库（重启不失效） |
| `JWT_EXPIRES_HOURS` | `72` | 管理端登录有效期 |
| `POLL_INTERVAL` | `5.0` | 收短信轮询间隔（秒） |
| `CONNECT_RETRY_INTERVAL` | `3.0` | 断线重连间隔（秒） |
| `STATUS_INTERVAL` | `30.0` | 状态采集间隔（秒） |

## REST API 概览

鉴权头 `Authorization: Bearer <token>`，接受管理端 JWT 或启用状态的 API Key（`/api/auth/*` 的 setup/login/setup-required 及 `/api/health`、`/api/version` 公开；`/api/auth/change-password` 仅 JWT）。完整字段与示例见 `docs/API.md`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 存活 + worker 状态 |
| GET | `/api/version` | 版本号 |
| GET | `/api/auth/setup-required` | 首次部署探测 |
| POST | `/api/auth/setup` | 创建首个管理员，返回 JWT |
| POST | `/api/auth/login` | 登录，返回 JWT |
| POST | `/api/auth/change-password` | 修改当前管理员密码 |
| GET | `/api/messages` | 短信列表（direction/sender/q/peer/before_id/page/page_size） |
| GET | `/api/messages/conversations` | 按号码聚合会话 |
| GET | `/api/messages/{id}` | 短信详情（含 `raw` AT 原始返回） |
| POST | `/api/messages/send` | 发送短信（202，异步入队） |
| GET | `/api/device/status` | 设备状态 + 消息统计 |
| POST | `/api/device/reconnect` | 请求重连串口 |
| GET | `/api/device/sms-capable` | 端口文本模式短信能力 |
| GET / POST | `/api/keys` | 密钥列表 / 创建（明文仅返回一次） |
| DELETE | `/api/keys/{id}` | 永久删除 |
| POST | `/api/keys/{id}/revoke` \| `/enable` | 停用 / 恢复 |
| GET | `/api/notify-configs/types` | 各渠道字段定义 |
| GET / POST | `/api/notify-configs` | 配置列表 / 新增 |
| PUT / DELETE | `/api/notify-configs/{id}` | 修改 / 删除 |
| POST | `/api/notify-configs/{id}/test` | 推送测试通知并记日志 |
| GET | `/api/forward-logs` | 转发/通知日志 |
| GET / POST | `/api/tasks` | 定时任务列表 / 创建 |
| PUT / DELETE | `/api/tasks/{id}` | 修改 / 删除 |
| POST | `/api/tasks/{id}/trigger` | 立即触发一次 |
| GET / POST | `/api/contacts` | 联系人列表 / 新增 |
| PUT / DELETE | `/api/contacts/{id}` | 修改 / 删除 |

## 通知渠道与字段

`type` ∈ `dingtalk` / `wecom` / `feishu` / `telegram` / `email` / `webhook` / `apprise`。`params` 必填/可选字段：

| type | 必填 | 可选 |
|---|---|---|
| `dingtalk` | `token` | `secret`（加签）、`phone` |
| `wecom` | `botkey`（key 或完整 Webhook 地址） | — |
| `feishu` | `token`（token 或完整 Webhook 地址） | — |
| `telegram` | `bot_token`、`chat_id`（可含 `@`） | — |
| `email` | `smtp_host`、`user`、`password`、`from`、`to` | `smtp_port`（默认 587）、`mode`（`starttls`/`ssl`） |
| `webhook` | `url` | — |
| `apprise` | `url`（任意受支持服务完整地址） | — |

过滤：`match_from`（发件号包含子串）、`match_contains`（内容包含子串），留空 = 不限制。短信/来电推送成功与否写入 `forward_logs`。

Webhook 推送 body：

```json
{ "event": "sms", "direction": "in", "sender": "10086", "receiver": "",
  "content": "余额50元", "received_at": "2026-09-20 00:15:45" }
```

`event`：`sms`（短信）或 `call`（来电，内容固定 `Incoming call`）；2xx 视为成功。

## 短信收发技术要点

- **收**：每 `POLL_INTERVAL` 轮询 `AT+CMGL`（无参 = REC UNREAD；Air780E 不支持数字枚举如 `AT+CMGL=4`，会返回 `+CMS ERROR: 500`）→ 解析（含引号内带逗号时间戳、UCS2 引号包裹的正文行）→ **长短信拼接**（Air780E TEXT 模式会剥掉 UDH，多段超长短信的每一段都返回完整可读文本且 SCTS 时间戳逐秒相同；同一 `(发件号 + 精确 SCTS)` 的分段在同一轮轮询到达时按 index 顺序拼接为**一条**消息入库 → 推送一次 → 逐段 `AT+CMGD` 删除）→ 去重（10 分钟内同号码同内容）→ 入库 `stored`。跨轮轮询才到全的碎片无法归并（罕见，保持现状）。每轮后再 `AT+CMGD=1,2` 清残留。设备/SIM 存储不保留短信。
- **发**：REST 先入库 `queued` → worker 串行发送（一条锁，避免与轮询撞车）→ `sending` → `sent`/`failed`。按内容是否含非 ASCII 自动选发送方式：纯 ASCII 单条走 **TEXT 模式**（`AT+CSCS="GSM"` + `AT+CSMP=17,167,0,0`，160 字符/条，`AT+CMGS="<号码>"` + ctrl-Z 提交）；**长短信（多条）一律走 PDU 拼接**（`AT+CMGF=0` + `AT+CMGS=<PDU长度>` + PDU hex + ctrl-Z，发完恢复 `AT+CMGF=1`）：ASCII 用 DCS=0 GSM 7bit（153 字符/段），中文/非 ASCII 用 DCS=8 UCS2（67 字符/段），每段带连接 UDH（`05 00 03 <ref> <total> <seq>`），收方重组为**一条**长短信。**必须用 PDU 发中文**：Air780E 的 TEXT 模式 `AT+CMGS` 会把正文原样（不转码 hex）当载荷发出，中文会乱码；PDU 的 GSM 7bit（GSM 03.38 表 + 0x1B 扩展）与 UCS2 均由我们自组（SCA=00 用 SIM 短信中心、地址 semi-octet、DCS、VP=A7=24h），并用 `AT+CMGW`/`AT+CMGR` 实测逐字节回读验证。内容超 255 段（GSM7 约 39k 字符 / UCS2 约 17k 字符）会被拒绝。
- **状态**：`AT+CSQ`（信号）、`AT+CREG?`（注册，`0` 未注册 / `1` 已注册 / `5` 漫游）、`AT+COPS?`、`AT+CGMM/CGMR/CGSN`、`AT+CPIN?`、`AT+CCID`；每 `STATUS_INTERVAL` 采集一次。
- **断线**：读异常触发 `on_fatal`，worker 每隔 `CONNECT_RETRY_INTERVAL` 自动重连并重新握手（`AT` / `ATE0` / `AT+CMGF=1` / `AT+CSCS="UCS2"` / `AT+CNMI=2,1,0,0,0` / `AT+CLIP=1`）。
- **来电**：`+CLIP` 事件（120 秒内同号去重）→ 按 `channel='call'` 相关日志计数，推送“来电”通知。

## 常见问题速查

- `+CREG: 0,0` / `+CMS ERROR: 331`：未插 SIM、SIM 松动或无信号 → 查 `AT+CPIN?`。
- 打不开串口 / ModemManager 抢口：装 udev 规则；必要时 `systemctl stop ModemManager`。
- 两个口都响应 AT：`ttyACM0` 与 `ttyACM2` 均可；固定 `DEVICE_PORT` 其一。
- 收不到短信：确认 `AT+CMGF=1`、`AT+CNMI=2,1,0,0,0`、插 SIM 后能收任意短信，再看轮询 `AT+CMGL`。
- 发送失败 `raw` 带 `+CMS ERROR: xxx`：xxx 按 AT 标准码对应（331=无网络/未插 SIM）。

## 约定与注意事项

- **无测试套件，无 lint 配置**：改动后至少用 `py_compile` 验证语法；涉及前端改动保持三个静态文件引用一致（`index.html` / `app.js` / `i18n.js`）。
- **版本号**：`app/version.py` 在发新版时同步修改，页面左下角与 `/api/version` 同源。
- **i18n**：界面文案在 `app/static/i18n.js`（`zh`/`en` 两套 key），新增文案需同时补两种语言。
- **API 文档**：`docs/API.md` 由手写维护，新增/修改端点后需同步更新。
- **gitignored 目录**：`data/`、`udev/`、`scripts/`、`.venv/`、`__pycache__`、`*.db*`。改动这些路径的文件不会被 git 追踪，需单独留意。
- **运行中的服务**：该仓库目录当前可能有 uvicorn（root）正在运行并占用 `data/bridge.db`（WAL）；直接用 `sudo` 写库需谨慎并可先备份。若改串口/启动相关代码，需重启服务或 `POST /api/device/reconnect` 生效（串口层改动需重启）。
- **设备名**：产品/模组名统一为 `Air780E`；代码内标识符（docker 名、logger 名、localStorage key）使用小写 `air780e`。避免出现裸 `Air780`。
- **鉴权**：`app/auth.py::authenticate` 同时接受管理端 JWT 与启用状态的 API Key；新增需要鉴权的路由应采用该依赖。
- **短信状态**：收到为 `stored`；发送为 `queued` → `sending` → `sent`/`failed`（`raw` 存 AT 返回，如 `+CMS ERROR: 331`=无网络/未插 SIM）。
- 端口 `8000` 是 docker-compose 宿主机映射（容器内 8000）；裸机 uvicorn 默认 8000。