"""后台串口工作线程：连接管理、短信轮询接收、短信发送队列、状态收集。"""

import logging
import re
import threading
import time

from .atdevice import ATDevice, CommandError, ucs2_decode, looks_ucs2
from .config import settings

log = logging.getLogger("air780.worker")

CMTI_RE = re.compile(r'^\+CMTI:\s*"([^"]*)",\s*(\d+)$')


def _norm_number(n: str) -> str:
    digits = re.sub(r"[^\d]", "", n or "")
    return digits


def _csv_fields(s: str) -> list:
    """按逗号切分，但保留引号内的逗号（如时间戳 "25/11/09,10:30:00+32"）。"""
    fields, cur, in_q = [], [], False
    for ch in s:
        if ch == '"':
            in_q = not in_q
        elif ch == "," and not in_q:
            fields.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    fields.append("".join(cur).strip())
    return fields


def parse_cmgl(lines: list) -> list:
    """解析 AT+CMGL 文本模式输出为消息字典列表。

    支持 +/- 存储、引号可选的地址字段与包含逗号的时间戳。
    """
    messages = []
    cur = None
    for line in lines:
        m = re.match(r'^\+CMGL:\s*(\d+)\s*(?:,\s*(.*))?$', line)
        if m:
            fields = _csv_fields(m.group(2)) if m.group(2) else []
            status = fields[0] if fields else ""
            addr = ""
            for f in fields[1:]:
                if f not in ("", "SM") and not f.startswith("REC") and not f.startswith("STO"):
                    addr = f
                    break
            scts = next(
                (f for f in reversed(fields[1:]) if "/" in f and ":" in f), None
            )
            cur = {
                "index": m.group(1),
                "status": status,
                "address_raw": addr,
                "scts": scts,
                "body": "",
            }
            messages.append(cur)
            continue
        if cur is not None and not line.startswith("+CMGL") and line != "OK":
            cur["body"] += line if not cur["body"] else "\n" + line
    return messages


def parse_cmgr(lines: list) -> dict | None:
    for i, line in enumerate(lines):
        if line.startswith("+CMGR:"):
            m = re.match(r'^\+CMGR:\s*(.*)$', line)
            fields = _csv_fields(m.group(1))
            status = fields[0] if fields else ""
            addr = ""
            for f in fields[1:]:
                if f not in ("", "SM", "READ", "UNREAD") and not f.isdigit() and not f.startswith("REC") and not f.startswith("STO"):
                    addr = f
                    break
            scts = next(
                (f for f in reversed(fields[1:]) if "/" in f and ":" in f), None
            )
            return {
                "index": None,
                "status": status,
                "address_raw": addr,
                "scts": scts,
                "body": "\n".join(lines[i + 1 :]),
            }
    return None


def decode_address(raw: str, ucs2: bool) -> str:
    if ucs2 and looks_ucs2(raw):
        decoded = ucs2_decode(raw)
        if decoded.isdigit() or decoded.startswith("+") or any(c.isdigit() for c in decoded):
            return decoded
    return raw


class SerialWorker(threading.Thread):
    def __init__(self, db, forwarder, on_message=None):
        super().__init__(daemon=True, name="serial-worker")
        self.db = db
        self.forwarder = forwarder
        self.on_message = on_message  # callable(message)
        self.dev: ATDevice | None = None
        self.ucs2 = False
        self._stop_event = threading.Event()
        self._send_queue: list = []  # (msg_id, number, content)
        self._state = {"connected": False, "port": "", "config_err": ""}
        self._info_lock = threading.Lock()
        self._last_status_at = 0.0

    # ---------- 状态 ----------

    def _set(self, **kw):
        with self._info_lock:
            self._state.update(kw)

    def status(self) -> dict:
        with self._info_lock:
            return dict(self._state)

    def stop(self):
        self._stop_event.set()
        if self.dev:
            self.dev.close()

    # ---------- 主循环 ----------

    def run(self):
        while not self._stop_event.is_set():
            self._set(
                connected=False,
                port="",
                config_err=self._state.get("config_err") and "dev" or "",
            )
            try:
                self.connect_and_init()
            except Exception as exc:
                log.warning("连接失败: %s", exc)
                self._set(config_err=str(exc))
                self._stop_event.wait(settings.retry_interval)
                continue
            self._set(connected=True, config_err="")
            try:
                self._poll_forever()
            except Exception as exc:
                log.warning("轮询中断: %s", exc)
                self._set(config_err=str(exc), connected=False)
                if self.dev:
                    self.dev.close()
            self._stop_event.wait(settings.retry_interval)

    def connect_and_init(self):
        port = self._pick_port()
        if not port:
            raise CommandError(f"未找到可用串口（探测 {settings.device_probe_ports}）")
        dev = ATDevice(port, settings.device_baudrate)
        dev.on_fatal = self._on_fatal
        dev.open()
        self.dev = dev
        self._set(port=port)

        res = dev.command("AT", 5)
        if not res.ok:
            raise CommandError(f"{port} 无响应")
        dev.command("ATE0", 3)
        cmgf = dev.command("AT+CMGF=1", 3)
        if not cmgf.ok:
            raise CommandError(f"{port} 不支持文本模式短信")
        self.ucs2 = dev.command('AT+CSCS="UCS2"', 3).ok
        dev.command("AT+CNMI=2,1,0,0,0", 3)  # 缓存模式，轮询兜底
        self._refresh_status(force=True)
        log.info("设备就绪 %s (ucs2=%s) IMEI=%s", port, self.ucs2, self._state.get("imei"))

    def _pick_port(self) -> str | None:
        if settings.device_port != "auto":
            return settings.device_port
        for port in settings.device_probe_ports:
            try:
                dev = ATDevice(port, settings.device_baudrate)
                dev.open()
                ok = dev.command("AT", 4).ok
                dev.close()
                if ok:
                    return port
            except Exception:
                continue
        return None

    def _on_fatal(self, exc):
        log.info("串口断线事件: %s", exc)

    # ---------- 轮询 ----------

    def _poll_forever(self):
        while not self._stop_event.is_set() and self.dev and self.dev.connected:
            try:
                if time.monotonic() - self._last_status_at >= settings.status_interval:
                    self._refresh_status()
                self._poll_incoming()
                self._process_send_queue()
            except Exception:
                log.exception("轮询异常")
            self._stop_event.wait(settings.poll_interval)

    def _refresh_status(self, force: bool = False):
        if not force and time.monotonic() - self._last_status_at < settings.status_interval:
            return
        dev = self.dev
        if not dev:
            return
        info = {}
        r = dev.command("AT+CSQ", 3)
        m = re.search(r"\+CSQ:\s*(\d+)\s*,\s*(\d+)", "\n".join(r.lines))
        info["csq_rssi"], info["csq_ber"] = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        r = dev.command("AT+COPS?", 3)
        m = re.search(r"\+COPS:\s*\d+\s*,\s*\d+\s*,\s*\"?([^\"]+)\"?", "\n".join(r.lines))
        info["operator"] = m.group(1) if m else ""
        r = dev.command("AT+CREG?", 3)
        m = re.search(r"\+CREG:\s*\d+\s*,\s*(\d+)", "\n".join(r.lines))
        info["reg_state"] = m.group(1) if m else ""
        r = dev.command("AT+CGMM", 3)
        model = "".join(r.lines[:-1]) if r.lines else ""
        model = re.sub(r"^\+CGMM:\s*", "", model).strip(' "\n').strip()
        info["model"] = model
        r = dev.command("AT+CGSN", 3)
        imei = "".join(r.lines[:-1]) if r.lines else ""
        imei = re.sub(r"^\+CGSN:\s*", "", imei).strip(' "\n').strip()
        info["imei"] = imei
        info["rssi_dbm"] = (-113 + 2 * info["csq_rssi"]) if info["csq_rssi"] is not None and info["csq_rssi"] < 99 else None
        info["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._last_status_at = time.monotonic()
        self._set(**info)

    # ---------- 收短信 ----------

    def _poll_incoming(self):
        res = self.dev.command("AT+CMGL=4", 8)
        if not res.ok or not any("+CMGL:" in ln for ln in res.lines):
            return
        for msg in parse_cmgl(res.lines):
            sender = _norm_number(decode_address(msg["address_raw"], self.ucs2))
            content = msg["body"]
            if self.ucs2 and looks_ucs2(content):
                content = ucs2_decode(content)
            elif not self.ucs2 and looks_ucs2(content):
                content = ucs2_decode(content)
            scts = msg["scts"] or ""
            if not self._already_stored(sender, content):
                mid = self.db.execute(
                    "INSERT INTO messages (direction, sender, receiver, content, status, raw) "
                    "VALUES ('in', ?, '', ?, 'stored', ?)",
                    (sender, content, f"idx={msg['index']} scts={scts}".strip()),
                )
                msg_row = self.db.row("SELECT * FROM messages WHERE id=?", (mid,))
                log.info("收到短信 #%s 来自 %s: %s", mid, sender, content[:40])
                if self.forwarder:
                    self.forwarder.forward(self.db, msg_row)
                if self.on_message:
                    try:
                        self.on_message(msg_row)
                    except Exception:
                        log.exception("on_message 回调失败")
            self._delete_msg(msg["index"])
        self._purge_device_messages()

    def _purge_device_messages(self):
        """短信均已入库并逐条删除，这里再清掉已读+已发送的残留，保证 SIM 存储不被占满。"""
        try:
            self.dev.command("AT+CMGD=1,2", 3)
        except Exception:
            pass

    def _already_stored(self, sender: str, content: str) -> bool:
        row = self.db.row(
            "SELECT id FROM messages WHERE direction='in' AND sender=? AND content=? "
            "AND datetime(created_at) >= datetime('now','-10 minutes') LIMIT 1",
            (sender, content),
        )
        return row is not None

    def _delete_msg(self, index: str):
        try:
            self.dev.command(f"AT+CMGD={index}", 5)
        except Exception:
            log.warning("删除短信 %s 失败", index)

    # ---------- 发短信 ----------

    def send(self, message_id: int, number: str, content: str):
        self._send_queue.append((message_id, number, content))

    def _process_send_queue(self):
        while self._send_queue and self.dev and self.dev.connected:
            mid, number, content = self._send_queue.pop(0)
            self.db.execute(
                "UPDATE messages SET status='sending' WHERE id=?", (mid,)
            )
            try:
                charset, results = self.dev.send_sms(number, content)
                ok = bool(results) and all(r.ok for r in results)
                err = ""
                if not ok:
                    err = "; ".join(r.error_text() for r in results if not r.ok)
                raw = "; ".join(
                    "|".join(r.lines[-2:]) for r in results
                )
                self.db.execute(
                    "UPDATE messages SET status=?, raw=? WHERE id=?",
                    ("sent" if ok else "failed", f"[{charset}] {raw}".strip()[:1000], mid),
                )
                log.info("发送短信 #%s 到 %s: %s", mid, number, "OK" if ok else f"FAIL {err}")
            except Exception as exc:
                self.db.execute(
                    "UPDATE messages SET status='failed', raw=? WHERE id=?",
                    (str(exc)[:1000], mid),
                )
                log.warning("发送短信 #%s 异常: %s", mid, exc)