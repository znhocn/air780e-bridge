"""短信通知转发

- 钉钉 / 企业微信 / 飞书 / Email：独立配置，底层统一使用 Apprise；
- Webhook：独立配置，走 httpx HTTP POST（JSON）。
"""

import json
import logging
import time
import urllib.parse

import apprise
import httpx

log = logging.getLogger("air780.forward")

TITLE = "Air780 短信通知"

# 渠道显示名（前端据此做双语显示）
CHANNEL_LABELS = {
    "dingtalk": {"zh": "钉钉", "en": "DingTalk"},
    "wecom": {"zh": "企业微信（群机器人）", "en": "WeCom (Group Bot)"},
    "feishu": {"zh": "飞书", "en": "Feishu"},
    "email": {"zh": "Email", "en": "Email"},
    "webhook": {"zh": "Webhook", "en": "Webhook"},
}


def _zf(zh_: str, en_: str):
    return {"zh": zh_, "en": en_}


# 渠道 -> 配置字段（labels / placeholders 双语，前端据此动态渲染表单）
CHANNEL_FIELDS = {
    "dingtalk": [
        {"key": "token", "type": "password", "required": True,
         "labels": _zf("DingTalk access_token / API Key", "DingTalk access_token / API Key"),
         "placeholders": _zf("机器人 access_token 或开放平台 API Key", "Bot access_token or Open Platform API Key")},
        {"key": "secret", "type": "password", "required": False,
         "labels": _zf("加签密钥 Secret（可选）", "Signing Secret (optional)")},
        {"key": "phone", "type": "text", "required": False,
         "labels": _zf("接收手机号（可选，应用消息）", "Recipient phone (optional, app message)")},
    ],
    "wecom": [
        {"key": "botkey", "type": "text", "required": True,
         "labels": _zf("企微群机器人 key（或完整 Webhook 地址）", "WeCom Group Bot key (or full webhook URL)"),
         "placeholders": _zf("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<key>", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<key>")},
    ],
    "feishu": [
        {"key": "token", "type": "text", "required": True,
         "labels": _zf("飞书机器人 Token（或完整 Webhook 地址）", "Feishu bot token (or full webhook URL)"),
         "placeholders": _zf("https://open.feishu.cn/open-apis/bot/v2/hook/<token>", "https://open.feishu.cn/open-apis/bot/v2/hook/<token>")},
    ],
    "email": [
        {"key": "smtp_host", "type": "text", "required": True,
         "labels": _zf("SMTP 服务器", "SMTP Server"), "placeholders": _zf("smtp.qq.com", "smtp.qq.com")},
        {"key": "smtp_port", "type": "text", "required": False,
         "labels": _zf("SMTP 端口", "SMTP Port"), "placeholders": _zf("默认 587（TLS）/ 465（SSL）", "Default 587 (TLS) / 465 (SSL)")},
        {"key": "user", "type": "text", "required": True,
         "labels": _zf("SMTP 用户名", "SMTP username")},
        {"key": "password", "type": "password", "required": True,
         "labels": _zf("SMTP 密码 / 授权码", "SMTP password / app code")},
        {"key": "from", "type": "text", "required": True,
         "labels": _zf("发件人 Email", "From email")},
        {"key": "to", "type": "text", "required": True,
         "labels": _zf("收件人 Email", "To email")},
        {"key": "mode", "type": "select", "required": False,
         "labels": _zf("加密方式", "Encryption"),
         "options": [{"value": "starttls", "labels": _zf("STARTTLS (587)", "STARTTLS (587)")},
                     {"value": "ssl", "labels": _zf("SSL (465)", "SSL (465)")}]},
    ],
    "webhook": [
        {"key": "url", "type": "text", "required": True,
         "labels": _zf("Webhook URL", "Webhook URL"),
         "placeholders": _zf("https://example.com/hook", "https://example.com/hook")},
    ],
}


def _mask(secret: str) -> str:
    s = secret or ""
    if len(s) <= 8:
        return "****"
    return s[:4] + "****" + s[-2:]


def _feishu_token(v: str) -> str:
    v = v.strip().rstrip("/")
    if v.startswith("http"):
        return v.split("/")[-1]
    return v


def _wecom_url(v: str) -> str:
    v = v.strip()
    if v.startswith("wecombot://"):
        return v
    if v.startswith("http"):
        key = urllib.parse.parse_qs(urllib.parse.urlparse(v).query).get("key", [""])[0]
        if key:
            return f"wecombot://{key}"
    return f"wecombot://{v}"


def build_apprise_url(cfg_type: str, params: dict) -> str:
    """根据渠道与参数构建 Apprise URL，缺必要参数时抛 ValueError。"""
    p = {k: (str(v) if v is not None else "") for k, v in (params or {}).items()}
    if cfg_type == "dingtalk":
        token, secret, phone = p.get("token", "").strip(), p.get("secret", "").strip(), p.get("phone", "").strip()
        if not token:
            raise ValueError("缺少 DingTalk access_token")
        cred = f"{urllib.parse.quote(secret, safe='')}@{urllib.parse.quote(token, safe='')}" if secret else urllib.parse.quote(token, safe='')
        url = f"dingtalk://{cred}"
        return f"{url}/{urllib.parse.quote(phone, safe='+-() ')}" if phone else url
    if cfg_type == "wecom":
        if not p.get("botkey", "").strip():
            raise ValueError("缺少企微群机器人 key")
        return _wecom_url(p["botkey"])
    if cfg_type == "feishu":
        if not p.get("token", "").strip():
            raise ValueError("缺少飞书机器人 Token")
        token = _feishu_token(p["token"])
        return f"feishu://{token}"
    if cfg_type == "email":
        host = p.get("smtp_host", "").strip()
        if not host:
            raise ValueError("缺少 SMTP 服务器")
        qs = {"user": p.get("user", "").strip(), "pass": p.get("password", "").strip()}
        if p.get("from", "").strip():
            qs["from"] = p["from"].strip()
        if p.get("to", "").strip():
            qs["to"] = p["to"].strip()
        if p.get("mode", "") == "ssl":
            qs["mode"] = "ssl"
        port = p.get("smtp_port", "").strip()
        base = f"mailtos://{host}" + (f":{port}" if port.isdigit() else "")
        q = "&".join(f"{k}={urllib.parse.quote_plus(v, safe='@.,:; ')}" for k, v in qs.items() if v)
        return f"{base}?{q}" if q else base
    raise ValueError(f"不支持的渠道: {cfg_type}")


def target_display(cfg: dict, params: dict) -> str:
    """日志里的脱敏目标展示。"""
    t = cfg.get("type", ""); p = params
    if t == "webhook":
        return str(p.get("url", ""))
    if t == "dingtalk":
        return f"dingtalk://{_mask(p.get('token', ''))}"
    if t == "wecom":
        return f"wecom://{_mask(p.get('botkey', ''))}"
    if t == "feishu":
        return f"feishu://{_mask(p.get('token', ''))}"
    if t == "email":
        return f"mailtos://{p.get('user', '')}@{p.get('smtp_host', '')}"
    return t


class Forwarder:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def _match(self, cfg: dict, sender: str, content: str) -> bool:
        if cfg.get("match_from") and cfg["match_from"] not in (sender or ""):
            return False
        if cfg.get("match_contains") and cfg["match_contains"] not in (content or ""):
            return False
        return True

    def _parse_params(self, cfg: dict) -> dict:
        try:
            return json.loads(cfg.get("params") or "{}") or {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _body(self, message: dict) -> str:
        return (
            f"短信 #{message.get('id') or '-'}\n"
            f"来自: {message.get('sender') or '-'}\n"
            f"时间: {message.get('created_at') or '-'}\n"
            f"内容:\n{message.get('content') or '-'}"
        )

    def _deliver(self, cfg: dict) -> tuple:
        """返回 (ok, status_code, error)。"""
        params = self._parse_params(cfg)
        t = cfg.get("type", "")
        if t == "webhook":
            payload = {
                "id": cfg.get("message_id"),
                "direction": "in",
                "sender": cfg.get("sender"),
                "receiver": cfg.get("receiver"),
                "content": cfg.get("content"),
                "received_at": cfg.get("created_at"),
            }
            try:
                r = httpx.post(params.get("url", ""), json=payload, timeout=self.timeout)
                ok = 200 <= r.status_code < 300
                return ok, r.status_code, ("" if ok else r.text[:300])
            except Exception as exc:
                return False, None, str(exc)
        url = build_apprise_url(t, params)
        a = apprise.Apprise()
        if not a.add(url):
            raise ValueError(f"无法解析 Apprise 地址: {url}")
        try:
            ok = a.notify(title=TITLE, body=self._body(cfg))
            return bool(ok), None, ("" if ok else "Apprise 发送失败")
        except Exception as exc:
            return False, None, str(exc)

    def forward(self, db, message: dict):
        """收到短信后，向命中的启用配置推送通知并记录日志。"""
        cfgs = db.rows("SELECT * FROM notify_configs WHERE enabled=1 ORDER BY id")
        hits = [c for c in cfgs if self._match(c, message.get("sender", ""), message.get("content", ""))]
        for cfg in hits:
            m = {**message, "message_id": message.get("id")}
            ok, code, err = self._deliver({**cfg, **m})
            db.execute(
                "INSERT INTO forward_logs "
                "(message_id, sender, content, rule_name, webhook_url, channel, success, status_code, error) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    message.get("id"),
                    message.get("sender"),
                    message.get("content"),
                    cfg["name"],
                    target_display(cfg, self._parse_params(cfg)),
                    cfg["type"],
                    1 if ok else 0,
                    code,
                    err,
                ),
            )
            log.info(
                "通知 短信#%s -> [%s]%s -> %s (code=%s, err=%r)",
                message.get("id"),
                cfg["type"],
                cfg["name"],
                "OK" if ok else "FAIL",
                code,
                err,
            )

    def test(self, db, cfg: dict) -> dict:
        """向配置推送一条测试通知（message_id 为 NULL，不计入短信库）。"""
        m = {
            "message_id": None,
            "sender": "测试",
            "receiver": "",
            "content": "这是一条来自 Air780 短信桥的测试通知",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        ok, code, err = self._deliver({**cfg, **m})
        db.execute(
            "INSERT INTO forward_logs "
            "(message_id, sender, content, rule_name, webhook_url, channel, success, status_code, error) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (None, "测试", m["content"], cfg.get("name"), target_display(cfg, self._parse_params(cfg)), cfg.get("type"), 1 if ok else 0, code, err),
        )
        return {"ok": ok, "status_code": code, "error": err or None}