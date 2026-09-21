# Air780E 短信转发桥 REST API

基于 FastAPI 的短信收发/转发服务对外接口。本文档覆盖全部 HTTP 端点、请求/响应格式、鉴权方式与示例。

- Base URL：`http://<主机>:8000`（Docker compose 部署时为 `http://<主机>:8000`，见 `docker-compose.yml`）
- 数据格式：`application/json`
- 交互式文档（FastAPI 自带）：`http://<主机>:8000/docs`（Swagger UI）与 `/redoc`
- 时间字段除标注外均为本地时间字符串，格式 `YYYY-MM-DD HH:MM:SS`

---

## 1. 鉴权

全部业务端点要求请求头 `Authorization: Bearer <token>`，token 两种都接受：

| Token 类型 | 来源 | 用途示例 |
|---|---|---|
| 管理端 JWT | `POST /api/auth/login`（或首次部署 `POST /api/auth/setup`） | Web 管理页面 |
| API Key | 页面「API 密钥」创建，格式 `ak_…`，仅创建时显示一次 | 外部脚本/第三方调用 |

鉴权规则：

- **公开**（无需鉴权）：`/api/health`、`/api/version`、`/api/auth/setup-required`、`/api/auth/setup`、`/api/auth/login`
- **JWT 专属**：`/api/auth/change-password`（需管理端 JWT）
- **其余**：JWT 或 API Key 都可以（`/api/messages/*`、`/api/device/*`、`/api/keys/*`、`/api/notify-configs*`、`/api/forward-logs`、`/api/tasks/*`、`/api/contacts/*`）

鉴权失败统一返回：

```json
// 401 Unauthorized
{ "detail": "Not authenticated or invalid credentials" }
```

管理端 JWT 有效期默认 72 小时（`JWT_EXPIRES_HOURS`），算法 HS256。

### 获取 API Key（curl）

```bash
# 1. 管理员登录
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-password"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# 2. 创建 API Key（key 仅此一次返回，请保存）
curl -s -X POST http://localhost:8000/api/keys \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"my-script"}'
```

---

## 2. 端点总览

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| GET | `/api/health` | 服务存活 + 后台 worker 状态 | 公开 |
| GET | `/api/version` | 项目版本号 | 公开 |
| GET | `/api/auth/setup-required` | 首次部署探测（是否需创建管理员） | 公开 |
| POST | `/api/auth/setup` | 创建首个管理员，返回 JWT | 公开 |
| POST | `/api/auth/login` | 管理员登录，返回 JWT | 公开 |
| POST | `/api/auth/change-password` | 修改当前管理员密码 | JWT |
| GET | `/api/messages` | 短信列表（分页/过滤/按号码聊天窗口） | Bearer |
| GET | `/api/messages/conversations` | 按号码聚合的会话列表 | Bearer |
| GET | `/api/messages/{id}` | 短信详情（含 AT 原始返回） | Bearer |
| POST | `/api/messages/send` | 发送短信（入队，异步发送） | Bearer |
| GET | `/api/device/status` | 设备状态 + 消息统计 | Bearer |
| POST | `/api/device/reconnect` | 请求重连串口 | Bearer |
| GET | `/api/device/sms-capable` | 当前端口是否支持短信（文本模式） | Bearer |
| GET | `/api/keys` | API 密钥列表 | Bearer |
| POST | `/api/keys` | 创建 API 密钥 | Bearer |
| DELETE | `/api/keys/{id}` | 永久删除密钥 | Bearer |
| POST | `/api/keys/{id}/revoke` | 吊销密钥（停用） | Bearer |
| POST | `/api/keys/{id}/enable` | 恢复密钥 | Bearer |
| GET | `/api/notify-configs/types` | 各通知渠道配置字段定义 | Bearer |
| GET | `/api/notify-configs` | 通知配置列表（含脱敏目标） | Bearer |
| POST | `/api/notify-configs` | 新增通知配置 | Bearer |
| PUT | `/api/notify-configs/{id}` | 修改通知配置 | Bearer |
| DELETE | `/api/notify-configs/{id}` | 删除通知配置 | Bearer |
| POST | `/api/notify-configs/{id}/test` | 推送一条测试通知并记录日志 | Bearer |
| GET | `/api/forward-logs` | 转发/通知日志 | Bearer |
| GET | `/api/tasks` | 定时任务列表 | Bearer |
| POST | `/api/tasks` | 创建定时任务 | Bearer |
| PUT | `/api/tasks/{id}` | 修改定时任务 | Bearer |
| DELETE | `/api/tasks/{id}` | 删除定时任务 | Bearer |
| POST | `/api/tasks/{id}/trigger` | 立即触发一次定时任务 | Bearer |
| GET | `/api/contacts` | 联系人列表 | Bearer |
| POST | `/api/contacts` | 新增联系人 | Bearer |
| PUT | `/api/contacts/{id}` | 修改联系人 | Bearer |
| DELETE | `/api/contacts/{id}` | 删除联系人 | Bearer |

---

## 3. 状态与版本

### GET /api/health

无鉴权。返回服务存活与后台串口 worker 状态。

```json
{ "ok": true, "worker": { "connected": true, "port": "/dev/ttyACM0" } }
```

### GET /api/version

无鉴权。返回项目版本号（维护于 `app/version.py`，页面左下角同源显示）。

```json
{ "version": "1.0.0" }
```

---

## 4. 认证（/api/auth）

### GET /api/auth/setup-required

首次部署探测：`true` 表示尚无管理员，需先走 `/api/auth/setup`。

```json
{ "setup_required": true }
```

### POST /api/auth/setup

创建首个管理员（仅在库中无管理员时可用）。成功返回 `201`。

请求：

```json
{ "username": "admin", "password": "your-password-8+" }
```

约束：`username` 2–64 字符；`password` 8–128 字符。

响应 `201`：

```json
{ "ok": true, "token": "<jwt>" }
```

错误：

| 状态码 | 场景 |
|---|---|
| 409 | 已存在管理员（请登录）/ 用户名已存在 |
| 422 | 用户名长度不合法 |

### POST /api/auth/login

管理员登录。成功返回 `201` 之外的 `200`。

请求：

```json
{ "username": "admin", "password": "your-password" }
```

响应 `200`：

```json
{ "token": "<jwt>" }
```

错误：`401` 用户名或密码错误。

### POST /api/auth/change-password

需管理端 JWT。修改当前登录管理员的密码。

请求：

```json
{ "current_password": "old-password", "new_password": "new-password-8+" }
```

响应 `200`：`{ "ok": true }`

错误：

| 状态码 | 场景 |
|---|---|
| 401 | 未认证 / 无效凭证 |
| 403 | 当前密码错误 |
| 404 | 管理员不存在 |
| 422 | 新密码与当前密码相同 / 长度不合法 |

---

## 5. 短信（/api/messages）

### GET /api/messages

短信列表。排序默认按 `id` 倒序（新的在前）。

Query 参数（全部可选）：

| 参数 | 类型 | 说明 |
|---|---|---|
| `direction` | string | `in`（收）或 `out`（发），仅这两个值 |
| `sender` | string | 发件人模糊匹配（LIKE %..%） |
| `q` | string | 内容模糊匹配（LIKE %..%） |
| `peer` | string | 按号码过滤（收/发均可，兼容 `86/+/0086` 前缀变体） |
| `before_id` | int | 只取 `id < before_id` 的更早消息（用于上翻加载） |
| `page` | int | 页码，≥1，默认 1 |
| `page_size` | int | 每页条数，1–200，默认 50 |

响应 `200`（分页结构）：

```json
{
  "items": [
    {
      "id": 3,
      "direction": "out",
      "sender": null,
      "receiver": "10086",
      "content": "hello",
      "status": "sent",
      "created_at": "2026-09-21 15:12:37"
    }
  ],
  "total": 3,
  "page": 1,
  "page_size": 50
}
```

> `status` 取值：收到的短信为 `stored`；发送为 `queued` / `sending` / `sent` / `failed`。
> 传了 `peer` 时走“聊天窗口”分页：固定取该号码**最新一页**，按时间从旧到新返回（便于直接渲染聊天界面）。

### GET /api/messages/conversations

把最近消息按号码聚合为会话列表（聊天侧边栏）。固定按最新 `limit` 条聚合，按最后活动时间倒序；无消息的联系人也会列出（`count: 0`）。

Query 参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `q` | string | 内容模糊过滤 |
| `limit` | int | 扫描的消息条数上限，1–1000，默认 200 |

响应 `200`（数组，每个元素）：

```json
{
  "peer": "10086",
  "contact_name": "客服短信",
  "count": 2,
  "last_content": "hello",
  "last_status": "sent",
  "last_direction": "out",
  "last_at": "2026-09-21 15:12:37"
}
```

### GET /api/messages/{message_id}

单条短信详情（含 `raw` 字段的 AT 原始返回）。

响应 `200`：

```json
{
  "id": 3,
  "direction": "out",
  "sender": null,
  "receiver": "10086",
  "content": "hello",
  "status": "failed",
  "raw": "[GSM] >|+CMS ERROR: 331",
  "created_at": "2026-09-21 15:12:37"
}
```

错误：`404` 短信不存在。

### POST /api/messages/send

发送短信。只负责**入库入队**，实际发送由后台串口 worker 串行执行（短信状态随后变为 `sent` / `failed`）。成功返回 `202`。

请求：

```json
{ "to": "10086", "content": "你好，这是一条测试短信" }
```

响应 `202`：

```json
{ "id": 3, "status": "queued" }
```

错误：

| 状态码 | 场景 |
|---|---|
| 422 | 号码或内容为空 |
| 503 | 串口服务未启动 |

---

## 6. 设备（/api/device）

### GET /api/device/status

设备状态（worker 实时采集）与消息统计。

响应 `200`：

```json
{
  "connected": true,
  "port": "/dev/ttyACM0",
  "config_err": "",
  "model": "Air780E",
  "fw_version": "...",
  "imei": "86...",
  "sim_state": "ready",
  "ccid": "89860...",
  "operator": "CHINA MOBILE",
  "network_type": "LTE",
  "reg_state": "1",
  "csq_rssi": 22,
  "csq_ber": 0,
  "rssi_dbm": -69,
  "checked_at": "2026-09-21 16:00:00",
  "messages_total": 3,
  "messages_in": 0,
  "messages_out_pending": 0,
  "messages_today_in": 0,
  "messages_today_out": 2,
  "messages_today_failed": 1,
  "calls_total": 0
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `connected` | 串口是否已连接 |
| `port` | 当前串口 |
| `config_err` | 配置/连接错误信息 |
| `model` / `fw_version` | 模组型号 / 固件版本（`AT+CGMM` / `AT+CGMR`） |
| `imei` / `ccid` | IMEI / SIM 卡 ICCID |
| `sim_state` | `ready` / `locked` / `absent` / `fail` 等 |
| `operator` | 运营商名称（`AT+COPS?`），未注册时为空 |
| `network_type` | 网络制式（`AT+COPS?` 的 AcT 字段）：`GSM` / `LTE` / `NB-IoT` 等，未知为空 |
| `reg_state` | 网络注册状态（`AT+CREG?`）：`0` 未注册 / `1` 已注册 / `5` 漫游 |
| `csq_rssi` / `csq_ber` | 信号强度 0–31 与误码率（`AT+CSQ`）；`99` 为不可测 |
| `rssi_dbm` | 估算的 RSSI（dBm），不可测时为 `null` |
| `checked_at` | 状态采集时间 |
| `messages_*` | 消息统计：总数 / 接收 / 发送待处理 / 今日收 / 今日发 / 今日失败 |
| `calls_total` | 来电通知累计次数（`forward_logs` 中 `channel='call'`） |

### POST /api/device/reconnect

请求重连串口（worker 会自动重连）。响应 `200`：

```json
{ "ok": true, "detail": "reconnect requested (worker will restart automatically)" }
```

### GET /api/device/sms-capable

检查当前端口的文本模式短信能力（发送 `AT+CSMS?`）。

响应 `200`：

```json
{ "capable": true, "detail": ["+CSMS: 1,1,1,1", "OK"] }
```

未连接时：`{ "capable": false, "detail": "not connected" }`

---

## 7. API 密钥（/api/keys）

### GET /api/keys

密钥列表（最新在前）。

响应 `200`：

```json
[
  {
    "id": 1,
    "name": "test",
    "key_prefix": "ak_Bo6sN",
    "active": 1,
    "created_at": "2026-09-20 19:51:40",
    "last_used": "2026-09-21 15:00:00"
  }
]
```

### POST /api/keys

创建密钥。成功返回 `201`，明文 `key` **仅此一次返回**。

请求：`{ "name": "my-script" }`（1–64 字符）

响应 `201`：

```json
{ "id": 2, "name": "my-script", "key": "ak_xxxx...", "key_prefix": "ak_xxxx", "created_at": "2026-09-21 16:00:00" }
```

### DELETE /api/keys/{key_id}

永久删除。成功：`{ "ok": true }`；`404` 密钥不存在。

### POST /api/keys/{key_id}/revoke

吊销（`active=0`，停用）。成功：`{ "ok": true }`；`404` 密钥不存在。

### POST /api/keys/{key_id}/enable

恢复（`active=1`）。成功：`{ "ok": true }`；`404` 密钥不存在。

---

## 8. 通知配置（/api/notify-configs、/api/forward-logs）

### GET /api/notify-configs/types

各渠道的配置字段定义（前端据此渲染表单，后端据此校验）。

响应 `200`：

```json
{
  "dingtalk": {
    "label": { "zh": "钉钉", "en": "DingTalk" },
    "fields": [
      { "key": "token", "type": "password", "required": true, "labels": { "zh": "...", "en": "..." }, "placeholders": { "zh": "...", "en": "..." } }
    ]
  },
  "wecom": { "...": "..." },
  "feishu": { "...": "..." },
  "telegram": { "...": "..." },
  "email": { "...": "..." },
  "webhook": { "...": "..." },
  "apprise": { "...": "..." }
}
```

### GET /api/notify-configs

配置列表（`target` 为脱敏后的目标地址）。

响应 `200`：

```json
[
  {
    "id": 1,
    "name": "DingTalk",
    "type": "dingtalk",
    "type_label": { "zh": "钉钉", "en": "DingTalk" },
    "enabled": 1,
    "match_from": "",
    "match_contains": "",
    "params": { "token": "879b..." },
    "target": "dingtalk://879b****57",
    "created_at": "2026-09-20 19:51:58"
  }
]
```

### POST /api/notify-configs

新增配置（返回 `201`）。请求/响应结构与下述 PUT 相同（响应带 `id`）。

### PUT /api/notify-configs/{cfg_id}

修改配置。请求体 `NotifyIn`：

```json
{
  "name": "DingTalk",
  "type": "dingtalk",
  "enabled": true,
  "match_from": "10086",
  "match_contains": "",
  "params": { "token": "879b..." }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 1–64 字符 |
| `type` | string | `dingtalk` / `wecom` / `feishu` / `telegram` / `email` / `webhook` / `apprise` |
| `enabled` | bool | 是否启用，默认 `true` |
| `match_from` | string | 发件号码包含子串才推送；留空 = 不限制 |
| `match_contains` | string | 内容包含子串才推送；留空 = 不限制 |
| `params` | object | 渠道参数，见下 |

各渠道 `params` 必填/可选字段：

| type | 必填 | 可选 |
|---|---|---|
| `dingtalk` | `token`（access_token / API Key） | `secret`（加签密钥）、`phone`（接收手机号） |
| `wecom` | `botkey`（群机器人 key 或完整 Webhook 地址） | — |
| `feishu` | `token`（机器人 token 或完整 Webhook 地址） | — |
| `telegram` | `bot_token`、`chat_id`（可含 `@`） | — |
| `email` | `smtp_host`、`user`、`password`、`from`、`to` | `smtp_port`（默认 587）、`mode`：`starttls`(587) / `ssl`(465) |
| `webhook` | `url` | — |
| `apprise` | `url`（任意受支持服务的完整 Apprise URL，如 `tgram://…`、`slack://…`） | — |

校验失败（渠道类型不支持 / 缺少必填项 / Apprise 地址无法解析）返回 `422`。

响应 `200`（PUT）与 `201`（POST）：同 GET 列表的单个元素结构（含 `id`、`type_label`、`target`）。

错误：

| 状态码 | 场景 |
|---|---|
| 401 | 未认证 |
| 404 | 配置不存在（PUT/DELETE/测试） |
| 422 | 渠道不支持 / 缺少必填参数 / 参数无法校验 |

### DELETE /api/notify-configs/{cfg_id}

删除配置。成功：`{ "ok": true }`；`404` 配置不存在。

### POST /api/notify-configs/{cfg_id}/test

向该配置推送一条测试通知，并写入转发日志（测试内容固定为 `This is a test notification from the Air780E SMS Bridge`）。

响应 `200`：

```json
{ "ok": true, "status_code": null, "error": null }
```

失败时 `ok: false`，`error` 为错误信息。`404` 配置不存在。

### GET /api/forward-logs

转发/通知日志（最新在前）。

Query 参数：`limit`（默认 100）、`message_id`（>0 时只查该短信的日志）。

响应 `200`：

```json
{
  "total": 3,
  "items": [
    {
      "id": 3,
      "message_id": null,
      "sender": "Test",
      "content": "This is a test notification from the Air780E SMS Bridge",
      "rule_name": "DingTalk",
      "webhook_url": "dingtalk://879b****57",
      "channel": "dingtalk",
      "success": 1,
      "status_code": null,
      "error": null,
      "created_at": "2026-09-21 14:58:25"
    }
  ]
}
```

---

## 9. 定时任务（/api/tasks）

### GET /api/tasks

任务列表。

响应 `200`：

```json
[
  {
    "id": 1,
    "name": "new",
    "enabled": 1,
    "interval_days": 7,
    "phone": "10086",
    "content": "hi",
    "last_run_at": "2026-09-21 15:12:37",
    "last_status": "failed",
    "last_msg_id": 3,
    "created_at": "2026-09-21 15:12:24"
  }
]
```

`last_status`：`never`（未运行）/ `running` / `success` / `failed`。

### POST /api/tasks

创建任务（返回 `201`）。请求体 `TaskIn`：

```json
{ "name": "周报", "enabled": true, "interval_days": 7, "phone": "10086", "content": "hello" }
```

字段约束：`name` 1–64；`interval_days` 1–365（默认 7）；`phone` / `content` 非空。

错误：`422` 号码或内容为空。

### PUT /api/tasks/{task_id}

修改任务。请求体同上。`404` 任务不存在；`422` 号码/内容为空。

### DELETE /api/tasks/{task_id}

删除任务。成功：`{ "ok": true }`；`404` 任务不存在。

### POST /api/tasks/{task_id}/trigger

立即触发一次（发送任务内容到 `phone`）。

响应 `200`：`{ "ok": true, "detail": "task triggered" }`

错误：`404` 任务不存在（或 scheduler 无法触发）；`503` 调度服务未启动。

---

## 10. 联系人（/api/contacts）

### GET /api/contacts

联系人列表（按名字、id 排序）。

响应 `200`：

```json
[
  { "id": 1, "name": "客服短信", "phone": "10086", "note": "欠费提醒", "created_at": "2026-09-20 20:00:00" }
]
```

### POST /api/contacts

新增联系人（返回 `201`）。请求体 `ContactIn`：

```json
{ "name": "客服短信", "phone": "10086", "note": "欠费提醒" }
```

字段约束：`name` 1–64；`phone` ≤40；`note` ≤200（默认空）。

错误：

| 状态码 | 场景 |
|---|---|
| 409 | 该号码已存在（`phone` 唯一约束，PUT 同样可能触发） |
| 422 | 名字或号码为空 |

### PUT /api/contacts/{contact_id}

修改联系人。请求体同上。`404` 联系人不存在。

### DELETE /api/contacts/{contact_id}

删除联系人。成功：`{ "ok": true }`；`404` 联系人不存在。

---

## 11. Webhook 推送格式

当通知配置类型为 `webhook` 时，收到短信或来电会向 `params.url` 发起 `HTTP POST`（`Content-Type: application/json`），body：

```json
{
  "event": "sms",
  "direction": "in",
  "sender": "10086",
  "receiver": "",
  "content": "余额50元",
  "received_at": "2026-09-20 00:15:45"
}
```

- `event`：`sms`（短信）或 `call`（来电通知，内容固定 `Incoming call`）。
- 收到 2xx 视为推送成功，其余视为失败（错误信息记录到转发日志，最长 300 字符）。
- 超时默认 10 秒。

---

## 12. 通用错误格式

业务错误统一为 FastAPI 风格：

```json
{ "detail": "错误描述" }
```

| 状态码 | 含义 |
|---|---|
| 400 | 请求错误 |
| 401 | 未认证 / token 无效或失效 |
| 404 | 资源不存在 |
| 409 | 冲突（如管理员已存在、号码重复） |
| 422 | 参数校验失败 |
| 503 | 后台服务未就绪 |

---

## 13. curl 示例

```bash
BASE=http://localhost:8000
TOKEN=$(curl -s -X POST $BASE/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-password"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
KEY="ak_your_api_key"   # 或直接用 API Key

# 查看设备状态
curl -s $BASE/api/device/status -H "Authorization: Bearer $KEY"

# 发送短信（异步）
curl -s -X POST $BASE/api/messages/send \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"to":"10086","content":"你好"}'

# 列出收到的短信
curl -s "$BASE/api/messages?direction=in&page=1&page_size=20" -H "Authorization: Bearer $KEY"

# 创建钉钉通知配置
curl -s -X POST $BASE/api/notify-configs \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"name":"钉钉","type":"dingtalk","enabled":true,"params":{"token":"<access_token>"}}'

# 测试通知配置
curl -s -X POST $BASE/api/notify-configs/1/test -H "Authorization: Bearer $KEY"

# 一键测试发送到 10086（仓库脚本）
API_KEY=$KEY ./scripts/test_send_10086.sh "你好"
```