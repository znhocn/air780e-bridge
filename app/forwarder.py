"""SMS notification forwarding

- DingTalk / WeCom / Feishu / Telegram / Email: separate configs, all sent via Apprise;
- Webhook: separate config, sent via httpx HTTP POST (JSON).
"""

import json
import logging
import threading
import time
import urllib.parse

import apprise
import httpx

log = logging.getLogger("air780e.forward")

TITLE = "Air780E SMS Notification"

# Channel display names (bilingual; the frontend renders them per locale)
CHANNEL_LABELS = {
    "dingtalk": {"zh": "钉钉", "en": "DingTalk"},
    "wecom": {"zh": "企业微信（群机器人）", "en": "WeCom (Group Bot)"},
    "feishu": {"zh": "飞书", "en": "Feishu"},
    "telegram": {"zh": "Telegram", "en": "Telegram"},
    "email": {"zh": "Email", "en": "Email"},
    "webhook": {"zh": "Webhook", "en": "Webhook"},
    "apprise": {"zh": "Apprise（自定义 URL）", "en": "Apprise (custom URL)"},
}


def _zf(zh_: str, en_: str):
    return {"zh": zh_, "en": en_}


# Channel -> config fields (bilingual labels/placeholders; the frontend renders the form from these)
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
    "telegram": [
        {"key": "bot_token", "type": "password", "required": True,
         "labels": _zf("Telegram Bot Token", "Telegram Bot Token"),
         "placeholders": _zf("123456789:AAHdqTcvCH1v...", "123456789:AAHdqTcvCH1v...")},
        {"key": "chat_id", "type": "text", "required": True,
         "labels": _zf("Telegram Chat ID（群/用户，可含 @）", "Telegram Chat ID (group/user, may include @)"),
         "placeholders": _zf("-1001200000000 或 @username", "-1001200000000 or @username")},
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
    "apprise": [
        {"key": "url", "type": "text", "required": True,
         "labels": _zf("Apprise 完整地址（任意受支持服务）", "Apprise URL (any supported service)"),
         "placeholders": _zf("如 tgram://<bot_token>/<chat_id> 或 slack://…", "e.g. tgram://<bot_token>/<chat_id> or slack://…")},
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
    """Build the Apprise URL from channel + params; raises ValueError when required args are missing."""
    p = {k: (str(v) if v is not None else "") for k, v in (params or {}).items()}
    if cfg_type == "dingtalk":
        token, secret, phone = p.get("token", "").strip(), p.get("secret", "").strip(), p.get("phone", "").strip()
        if not token:
            raise ValueError("Missing DingTalk access_token")
        cred = f"{urllib.parse.quote(secret, safe='')}@{urllib.parse.quote(token, safe='')}" if secret else urllib.parse.quote(token, safe='')
        url = f"dingtalk://{cred}"
        return f"{url}/{urllib.parse.quote(phone, safe='+-() ')}" if phone else url
    if cfg_type == "wecom":
        if not p.get("botkey", "").strip():
            raise ValueError("Missing WeCom Group Bot key")
        return _wecom_url(p["botkey"])
    if cfg_type == "feishu":
        if not p.get("token", "").strip():
            raise ValueError("Missing Feishu bot token")
        token = _feishu_token(p["token"])
        return f"feishu://{token}"
    if cfg_type == "telegram":
        token = p.get("bot_token", "").strip()
        chat_id = p.get("chat_id", "").strip()
        if not token:
            raise ValueError("Missing Telegram Bot Token")
        if not chat_id:
            raise ValueError("Missing Telegram Chat ID")
        return f"tgram://{token}/{chat_id}"
    if cfg_type == "email":
        host = p.get("smtp_host", "").strip()
        if not host:
            raise ValueError("Missing SMTP server")
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
    if cfg_type == "apprise":
        url = p.get("url", "").strip()
        if not url:
            raise ValueError("Missing Apprise URL")
        a = apprise.Apprise()
        if not a.add(url):
            raise ValueError(f"Cannot parse Apprise URL: {url}")
        return url
    raise ValueError(f"Unsupported channel: {cfg_type}")


def target_display(cfg: dict, params: dict) -> str:
    """Masked target display for the logs."""
    t = cfg.get("type", ""); p = params
    if t == "webhook":
        return str(p.get("url", ""))
    if t == "dingtalk":
        return f"dingtalk://{_mask(p.get('token', ''))}"
    if t == "wecom":
        return f"wecom://{_mask(p.get('botkey', ''))}"
    if t == "feishu":
        return f"feishu://{_mask(p.get('token', ''))}"
    if t == "telegram":
        return f"tgram://{_mask(p.get('bot_token', ''))}/{p.get('chat_id', '')}"
    if t == "email":
        return f"mailtos://{p.get('user', '')}@{p.get('smtp_host', '')}"
    if t == "apprise":
        u = str(p.get("url", ""))
        scheme, _, rest = u.partition("://")
        return f"{scheme or 'apprise'}://{_mask(rest)}"
    return t


class Forwarder:
    def __init__(self, timeout: float = 10.0, cache_size: int = 32):
        self.timeout = timeout
        self._apprise_cache: dict = {}
        self._cache_lock = threading.Lock()
        self._cache_size = cache_size

    def _match(self, cfg: dict, sender: str, content: str, ignore_content: bool = False) -> bool:
        if cfg.get("match_from") and cfg["match_from"] not in (sender or ""):
            return False
        if not ignore_content and cfg.get("match_contains") and cfg["match_contains"] not in (content or ""):
            return False
        return True

    def _parse_params(self, cfg: dict) -> dict:
        try:
            return json.loads(cfg.get("params") or "{}") or {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def _apprise(self, url: str) -> apprise.Apprise:
        """Reuse a cached Apprise instance per target URL (params rarely change)."""
        with self._cache_lock:
            a = self._apprise_cache.get(url)
            if a is not None:
                return a
            a = apprise.Apprise()
            if not a.add(url):
                raise ValueError(f"Cannot parse Apprise URL: {url}")
            if len(self._apprise_cache) >= self._cache_size:
                self._apprise_cache.pop(next(iter(self._apprise_cache)))
            self._apprise_cache[url] = a
            return a

    def _body(self, message: dict) -> str:
        if message.get("event") == "call":
            return (
                f"Event: Incoming Call\n"
                f"From: {message.get('sender') or '-'}\n"
                f"Time: {message.get('created_at') or '-'}"
            )
        return (
            f"From: {message.get('sender') or '-'}\n"
            f"Time: {message.get('created_at') or '-'}\n"
            f"Content:\n{message.get('content') or '-'}"
        )

    def _deliver(self, cfg: dict, params: dict | None = None) -> tuple:
        """Returns (ok, status_code, error)."""
        params = params if params is not None else self._parse_params(cfg)
        t = cfg.get("type", "")
        if t == "webhook":
            payload = {
                "event": cfg.get("event") or "sms",
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
        a = self._apprise(url)
        try:
            ok = a.notify(title=TITLE, body=self._body(cfg))
            return bool(ok), None, ("" if ok else "Apprise send failed")
        except Exception as exc:
            return False, None, str(exc)

    def forward(self, db, message: dict):
        """After receiving an SMS, push notifications to matching enabled configs and log the results."""
        cfgs = db.rows("SELECT * FROM notify_configs WHERE enabled=1 ORDER BY id")
        is_call = message.get("event") == "call"
        hits = [
            c for c in cfgs
            if self._match(c, message.get("sender", ""), message.get("content", ""), ignore_content=is_call)
        ]
        for cfg in hits:
            m = {**message, "message_id": message.get("id")}
            params = self._parse_params(cfg)
            ok, code, err = self._deliver({**cfg, **m}, params)
            db.execute(
                "INSERT INTO forward_logs "
                "(message_id, sender, content, rule_name, webhook_url, channel, success, status_code, error) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    message.get("id"),
                    message.get("sender"),
                    message.get("content"),
                    cfg["name"],
                    target_display(cfg, params),
                    cfg["type"],
                    1 if ok else 0,
                    code,
                    err,
                ),
            )
            log.info(
                "notify sms#%s -> [%s]%s -> %s (code=%s, err=%r)",
                message.get("id"),
                cfg["type"],
                cfg["name"],
                "OK" if ok else "FAIL",
                code,
                err,
            )

    def test(self, db, cfg: dict) -> dict:
        """Push a test notification to a config (message_id is NULL; nothing is stored in the SMS table)."""
        m = {
            "message_id": None,
            "sender": "Test",
            "receiver": "",
            "content": "This is a test notification from the Air780E SMS Bridge",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        params = self._parse_params(cfg)
        ok, code, err = self._deliver({**cfg, **m}, params)
        db.execute(
            "INSERT INTO forward_logs "
            "(message_id, sender, content, rule_name, webhook_url, channel, success, status_code, error) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (None, "Test", m["content"], cfg.get("name"), target_display(cfg, params), cfg.get("type"), 1 if ok else 0, code, err),
        )
        return {"ok": ok, "status_code": code, "error": err or None}