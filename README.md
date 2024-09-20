# Air780E 短信转发桥

基于 **Air780E（合宙 4G Cat.1）模组** 的短信收发/转发服务，通过 **USB 串口 AT 命令** 与模组通信（本设备 USB ID `19d1:0001` BYD EigenComm Compo），提供 RESTful API 与 Web 管理页面。

> 项目只使用短信类 AT 命令（`AT+CMGF` / `AT+CMGS` / `AT+CMGL` / `AT+CMGD` 等），**不启用 PPP/NCM 数据拨号，不消耗 SIM 卡流量**。

## 功能

- 接收短信：轮询 `AT+CMGL` 读新短信，**全部入库 SQLite 后**逐条删除并定时 `AT+CMGD=1,2` 清理设备存储，不会长期占用模组/SIM 存储
- 发送短信：REST 接口**先入库**，后台串行发送，自动切换 GSM 7bit / UCS2（支持中文、长短信自动分段）
- 通知：钉钉 / 企业微信（群机器人）/ 飞书 / Email 各自独立配置，**底层统一使用 Apprise**；Webhook 为独立 HTTP 推送配置
- 串口通信：`pyserial` 单读者线程 + 命令锁，支持断线自动重连、FTDI/cdc_acm 均可
- 数据库：SQLite（WAL），存短信、通知配置、API 密钥、转发日志
- 后端：FastAPI + Uvicorn
- 前端：静态单页管理（状态/收发/通知/密钥/日志），JWT 登录
- API：RESTful，Bearer API Key 鉴权，可在页面创建/吊销密钥（含最近使用时间）
- 打包：Docker / docker-compose，串口直通容器，数据卷持久化

## 目录结构

```
app/
  main.py        FastAPI 入口（lifespan 启动 worker）
  atdevice.py    串口 AT 层（命令锁、超时、CMGS 提示符、UCS2）
  worker.py      后台线程：重连、轮询收短信、发送队列、状态
  forwarder.py   通知转发器（Apprise 钉钉/企微/飞书/Email + Webhook）
  database.py    SQLite（线程安全、WAL）
  auth.py        管理端 JWT + API Key 鉴权
  api/           REST 路由（messages/device/keys/notify/auth）
  static/        前端单页（index.html / app.js / style.css）
  cli.py         不经 Web 直接调试硬件的命令行
udev/            串口权限/避让 ModemManager 规则
scripts/         test_send_10086.sh 测试发送到 10086
```

## 硬件准备

1. 把 Air780E 串口板通过 USB 接入主机，确认枚举为 `19d1:0001`：
   ```bash
   lsusb   # Bus ... ID 19d1:0001 BYD EigenComm Compo
   ls /dev/ttyACM*
   ```
   本板枚举为 3 个 CDC-ACM 口（`ttyACM0/1/2`），其中一个（或两个）是 AT 命令口，`ttyACM1` 是数据/诊断口。
2. 安装 udev 规则（普通用户可访问 + 标记为 AT 主端口，避免 ModemManager 抢占）：
   ```bash
   sudo cp udev/99-air780-eigencomm.rules /etc/udev/rules.d/
   sudo udevadm control --reload && sudo udevadm trigger
   sudo usermod -aG dialout $USER   # 重新登录生效
   ```
3. 插入 SIM 卡。确认注册与信号：
   ```bash
   .venv/bin/python -m app.cli status     # 应有 +CREG: 0,1(或5) 与 +CSQ 非 99
   ```

> 若无法访问串口，检查：`ls -l /dev/ttyACM*` 属组是否为 `dialout`；ModemManager 是否占用：
> `systemctl stop ModemManager` 或按 udev 规则中的 `ID_MM_PORT_TYPE_AT_PRIMARY` 标记。

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
DB_PATH=./data/bridge.db \
    .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000 ，**首次部署**页面会引导创建管理员账户（用户名 + 密码），之后每次用该账户登录。管理员账号哈希后保存在数据库 `admins` 表。

## Docker 运行

```bash
# 构建
docker build -t air780-bridge:latest .

# 直接运行（把设备串口映射进容器）
docker run -d --name air780-bridge --restart unless-stopped \
  --device /dev/ttyACM0:/dev/ttyACM0 \
  --device /dev/ttyACM1:/dev/ttyACM1 \
  --device /dev/ttyACM2:/dev/ttyACM2 \
  -p 8000:8000 \
  -e DEVICE_PORT=auto \
  -e JWT_SECRET=your-own-random-string \
  -v $(pwd)/data:/data \
  air780-bridge:latest

# 或使用编排
docker compose up -d --build
```

`docker-compose.yml` 不再内嵌 `environment`，全部走代码内置默认值（串口自动探测、`DB_PATH=/data/bridge.db`、`JWT_SECRET` 首次启动自动生成并入库），开箱即用。需要自定义时，在 `docker-compose.yml` 补一段 `environment:`，或改用 `docker run -e` 覆盖上面的配置项。

> **首次部署不需要配置密码**：打开 http://<主机>:8888 ，页面显示「创建管理员账户」，填入用户名和至少 8 位密码即完成，成功后自动进入管理界面。

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `DEVICE_PORT` | `auto` | 串口名；`auto` 时在 `DEVICE_PROBE_PORTS` 里探测第一个响应 `AT` 的口 |
| `DEVICE_PROBE_PORTS` | `/dev/ttyACM0,/dev/ttyACM1,/dev/ttyACM2` | 自动探测候选列表 |
| `DEVICE_BAUDRATE` | `115200` | 波特率 |
| `DB_PATH` | `/data/bridge.db` | SQLite 路径（容器内建议挂载 `/data` 卷），**含管理员账户** |
| `ADMIN_PASSWORD` | 空 | 可选，兼容旧部署：设置后启动时播种一个名为 `admin` 的账户（仅当库中无任何管理员时） |
| `JWT_SECRET` | 首次启动自动生成并入库 | 管理端 JWT 签名密钥（重启不失效） |
| `JWT_EXPIRES_HOURS` | `72` | 管理端登录有效期 |
| `POLL_INTERVAL` | `5` | 收短信轮询间隔（秒） |

## REST API

鉴权头统一为 `Authorization: Bearer <token>`，token 两种都接受：

- **管理端 JWT**：`POST /api/auth/login` 用用户名/密码换取，Web 管理页面登录后自动携带；
- **API Key**：在页面「API 密钥」创建（仅显示一次），供外部脚本/第三方调用。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/api/auth/setup-required` | 首次部署探测：`true` 表示尚无管理员，需先创建 |
| POST | `/api/auth/setup` | 创建首个管理员 `{username, password}`（仅当库中无管理员时可用），返回 JWT |
| POST | `/api/auth/login` | 管理端登录（body: `{username, password}`），返回 JWT |
| GET  | `/api/messages?direction=in\|out&page=1&page_size=50&q=` | 短信列表 |
| GET  | `/api/messages/{id}` | 短信详情（含 AT 原始返回） |
| POST | `/api/messages/send` | 发送短信 `{to, content}`，返回 `{id,status:"queued"}` |
| GET  | `/api/device/status` | 设备状态（连接/IMEI/信号/运营商/注册） |
| POST | `/api/device/reconnect` | 请求重连串口 |
| GET  | `/api/keys` | 密钥列表（含最近使用时间，管理端 JWT） |
| POST | `/api/keys` | 创建密钥 `{name}`，返回明文 key 一次 |
| DELETE | `/api/keys/{id}` | 吊销密钥 |
| POST | `/api/keys/{id}/enable` | 恢复密钥 |
| GET  | `/api/notify-configs/types` | 各通知渠道的配置字段定义（供前端渲染表单） |
| GET  | `/api/notify-configs` | 通知配置列表（含脱敏目标） |
| POST | `/api/notify-configs` | 新增通知配置 `{name,type,enabled,match_from,match_contains,params}` |
| PUT  | `/api/notify-configs/{id}` | 修改通知配置 |
| DELETE | `/api/notify-configs/{id}` | 删除通知配置 |
| POST | `/api/notify-configs/{id}/test` | 推送一条测试通知并记录日志 |
| GET  | `/api/forward-logs` | 转发/通知日志 |

### 发送示例（测试发给 10086）

```bash
curl -X POST http://localhost:8000/api/messages/send \
  -H "Authorization: Bearer <API_KEY>" -H "Content-Type: application/json" \
  -d '{"to":"10086","content":"hello"}'
# 或一键测试脚本
API_KEY=<API_KEY> ./scripts/test_send_10086.sh "你好"
```

发送为异步队列：接口返回 `queued` 后，后台 worker 实际发送，短信状态在列表里变为 `sent` / `failed`（`raw` 字段含 AT 返回，如 `+CMS ERROR: 331` 表示无网络服务/未插 SIM）。

### 通知设置

在页面「通知设置」逐条配置通知渠道。每条配置可设置：
- **渠道**：`dingtalk`（钉钉）/ `wecom`（企业微信群机器人）/ `feishu`（飞书）/ `email`（Email）/ `webhook`（HTTP）
- **字段**：随渠道不同（机器人 token/签名、SMTP 服务器/账号/授权码/收发件人、Webhook URL 等），页面按渠道动态渲染；可点「测试」推送一条测试通知
- **过滤**：`match_from`（发件号码包含子串）、`match_contains`（内容包含子串），留空=不限制，命中才推送
- **通知内容**：传入的短信（`#id`、来自、时间、正文）

钉钉/企微/飞书/Email 底层均通过 Apprise 发送（对应 `dingtalk://`、`wecombot://`、`feishu://`、`mailtos://` 四种自建 Apprise URL，仅作语法校验不在界面暴露）；Webhook 为独立 HTTP POST，推送 JSON：
```json
{ "id": 2, "direction": "in", "sender": "10086",
  "receiver": "", "content": "余额50元", "received_at": "2026-09-20 00:15:45" }
```
推送成功/失败均写入 `forward_logs`（渠道、配置名、脱敏目标、结果、错误）。旧版 `forward_rules` 启动时自动迁移为 `webhook` 类型配置。

## 常见问题

- **`+CREG: 0,0` 无法注册 / `+CMS ERROR: 331`**：未插 SIM、SIM 松动或无信号，检查 `AT+CPIN?`（应返回 `+CPIN: READY`；`+CME ERROR: 10` 表示 SIM 未插入）。
- **无法打开串口 / ModemManager 抢口**：安装 udev 规则；必要时 `systemctl stop ModemManager`。
- **两个口都响应 AT**：本模组 `ttyACM0` 与 `ttyACM2` 均可作为 AT 口；`DEVICE_PORT` 固定其一即可，其余口请勿同时占用。
- **SIM 数据流量**：本项目从不发起 `AT+CGDATA` / `AT+CGACT` / PPP，仅走短信 AT 命令，不会消耗数据流量。
- **收不到短信**：确认 `AT+CMGF=1` 文本模式、`AT+CNMI=2,1,0,0,0`、插入 SIM 后可收任意短信再测试；接收侧每 `POLL_INTERVAL` 秒轮询 `AT+CMGL=4`。
- **旧版本升级（曾用 ADMIN_PASSWORD）**：老部署升级后，如未设置 `ADMIN_PASSWORD`，首次打开页面会引导你创建管理员账户（旧 env 密码不再生效）；若想沿用，可在环境变量里设置 `ADMIN_PASSWORD=<旧密码>`，启动时自动播种名为 `admin` 的账号（仅当库中无任何管理员时）。
- **忘记管理员密码**：停容器后手动清库（有数据卷时 `docker volume rm` 需谨慎）或直接操作 SQLite：`UPDATE admins SET password_hash='…', salt='…' WHERE username='…'`（hash 为 PBKDF2-SHA256，可用 `python -c "import hashlib;print(hashlib.pbkdf2_hmac('sha256',b'新密码',bytes.fromhex('盐'),240000).hex())"` 生成）。

## 技术说明

- 收短信：`AT+CMGL=4` 拉取全部短信 → 解析（含引号内逗号的时间戳）→ 去重（10 分钟内同号码同内容）→ **入库 SQLite** → 按通知配置推送 → 逐条 `AT+CMGD` 删除 → 每次轮询后再 `AT+CMGD=1,2` 清掉已读+已发送残留。短信与日志只存在于 SQLite，设备/SIM 存储不保留。
- 发短信：按内容是否含非 ASCII 自动选 `CSCS="GSM"`（160 字符/条）或 `CSCS="UCS2"`（67 字符/条，中文/长短信自动分段），通过 `AT+CMGS` + ctrl-Z 提交；一条锁串行发送，避免与轮询撞车。
- 串口层：读线程 + `CommandResult` 事件模型；命令带超时；读异常触发 `on_fatal`，worker 自动重连整个握手。