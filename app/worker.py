"""Background serial worker: connection management, SMS polling, outbound SMS queue, status collection."""

import logging
import queue
import re
import threading
import time

from .atdevice import ATDevice, CommandError, ucs2_decode, looks_ucs2
from .config import settings

log = logging.getLogger("air780e.worker")

CALL_DEDUP_SECONDS = 120  # ignore repeated +CLIP reports for the same caller


def _norm_number(n: str) -> str:
    digits = re.sub(r"[^\d]", "", n or "")
    return digits


def _csv_fields(s: str) -> list:
    """Split on commas but keep commas inside quotes (e.g. timestamp "25/11/09,10:30:00+32")."""
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
    """Parse AT+CMGL text-mode output into a list of message dicts.

    Handles +/- storage, addresses with optional quotes, and timestamps containing commas.
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
            body = line.strip()
            if len(body) >= 2 and body.startswith('"') and body.endswith('"'):
                body = body[1:-1]
            cur["body"] += body if not cur["body"] else "\n" + body
    return messages


def decode_address(raw: str, ucs2: bool) -> str:
    if ucs2 and looks_ucs2(raw):
        decoded = ucs2_decode(raw)
        if decoded.isdigit() or decoded.startswith("+") or any(c.isdigit() for c in decoded):
            return decoded
    return raw


def group_incoming(parts: list) -> list:
    """Assemble one poll's SMS parts into messages.

    A long (concatenated) SMS is delivered by the network as several
    segments; Air780E strips the UDH in TEXT mode so segments come out as
    clean text. Segments of the same multipart share the sender and the
    exact SMSC timestamp, so parts with the same (sender, scts) are joined in
    arrival order into a single message. Singletons pass through unchanged.
    """
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for p in parts:
        key = (p["sender"], p["scts"])
        if key not in groups:
            groups[key] = {"sender": p["sender"], "scts": p["scts"], "parts": []}
            order.append(key)
        groups[key]["parts"].append(p)
    out = []
    for key in order:
        g = groups[key]
        parts = sorted(g["parts"], key=lambda p: int(p["index"]))
        out.append({
            "sender": g["sender"],
            "scts": g["scts"],
            "indices": [p["index"] for p in parts],
            "content": "".join(p["content"] for p in parts),
        })
    return out


class SerialWorker(threading.Thread):
    def __init__(self, db, forwarder, on_message=None):
        super().__init__(daemon=True, name="serial-worker")
        self.db = db
        self.forwarder = forwarder
        self.on_message = on_message  # callable(message)
        self.dev: ATDevice | None = None
        self.ucs2 = False
        self._stop_event = threading.Event()
        self._send_queue: queue.Queue = queue.Queue()  # (msg_id, number, content)
        self._state = {"connected": False, "port": "", "config_err": ""}
        self._info_lock = threading.Lock()
        self._last_status_at = 0.0
        self._last_call = (None, 0.0)  # (number, monotonic ts) for +CLIP dedup
        self.on_send_result = None  # callable(message_id, ok) - outbound completion

    # ---------- status ----------

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

    # ---------- main loop ----------

    def run(self):
        while not self._stop_event.is_set():
            self._set(
                connected=False,
                port="",
                config_err="",
            )
            try:
                self.connect_and_init()
            except Exception as exc:
                log.warning("Connection failed: %s", exc)
                self._set(config_err=str(exc))
                self._stop_event.wait(settings.retry_interval)
                continue
            self._set(connected=True, config_err="")
            try:
                self._poll_forever()
            except Exception as exc:
                log.warning("Poll interrupted: %s", exc)
                self._set(config_err=str(exc), connected=False)
                if self.dev:
                    self.dev.close()
            self._stop_event.wait(settings.retry_interval)

    def connect_and_init(self):
        port = self._pick_port()
        if not port:
            raise CommandError(f"No usable serial port found (probed {settings.device_probe_ports})")
        dev = ATDevice(port, settings.device_baudrate)
        dev.on_fatal = self._on_fatal
        dev.on_call = self._on_incoming_call
        dev.open()
        self.dev = dev
        self._set(port=port)

        res = dev.command("AT", 5)
        if not res.ok:
            raise CommandError(f"{port} no response")
        dev.command("ATE0", 3)
        cmgf = dev.command("AT+CMGF=1", 3)
        if not cmgf.ok:
            raise CommandError(f"{port} does not support text-mode SMS")
        self.ucs2 = dev.command('AT+CSCS="UCS2"', 3).ok
        dev.command("AT+CNMI=2,1,0,0,0", 3)  # cached mode; polling as a fallback
        try:
            dev.command("AT+CLIP=1", 3)  # caller line ID -> +CLIP unsolicited events
        except Exception:
            log.warning("AT+CLIP not enabled (incoming-call notification unavailable)")
        self._refresh_status(force=True)
        log.info("Device ready %s (ucs2=%s) IMEI=%s", port, self.ucs2, self._state.get("imei"))

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
        log.info("Serial disconnect event: %s", exc)

    def _on_incoming_call(self, number: str):
        """Incoming call (+CLIP): de-duplicate and push a notification via the forwarder."""
        num = _norm_number(number)
        now = time.monotonic()
        if not num:
            log.info("Incoming call with no caller number (private/unknown)")
            return
        if num == self._last_call[0] and now - self._last_call[1] < CALL_DEDUP_SECONDS:
            log.debug("Ignoring duplicate incoming-call report from %s", num)
            return
        self._last_call = (num, now)
        log.info("Incoming call from %s", num)
        if not self.forwarder:
            return
        try:
            msg = {
                "id": None,
                "direction": "in",
                "sender": num,
                "receiver": "",
                "content": "Incoming call",
                "event": "call",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.forwarder.forward(self.db, msg)
        except Exception:
            log.exception("Incoming-call notification failed")

    # ---------- polling ----------

    def _poll_forever(self):
        while not self._stop_event.is_set() and self.dev and self.dev.connected:
            try:
                if time.monotonic() - self._last_status_at >= settings.status_interval:
                    self._refresh_status()
                self._poll_incoming()
                self._process_send_queue()
            except Exception:
                log.exception("Polling exception")
            self._stop_event.wait(settings.poll_interval)

    def _refresh_status(self, force: bool = False):
        if not force and time.monotonic() - self._last_status_at < settings.status_interval:
            return
        dev = self.dev
        if not dev:
            return
        info = {}
        if force:
            self._refresh_static_info(dev, info)
        r = dev.command("AT+CSQ", 3)
        m = re.search(r"\+CSQ:\s*(\d+)\s*,\s*(\d+)", "\n".join(r.lines))
        info["csq_rssi"], info["csq_ber"] = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        r = dev.command("AT+CREG?", 3)
        m = re.search(r"\+CREG:\s*\d+\s*,\s*(\d+)", "\n".join(r.lines))
        info["reg_state"] = m.group(1) if m else ""
        dev.command("AT+COPS=3,0", 3)
        r = dev.command("AT+COPS?", 3)
        m = re.search(r'\+COPS:\s*\d+\s*,\s*\d+\s*,\s*"?([^",]+)"?\s*(?:,\s*(\d+))?', "\n".join(r.lines))
        info["operator"] = m.group(1).strip() if m else ""
        info["network_type"] = {
            "0": "GSM",
            "2": "UTRAN",
            "3": "GSM/EDGE",
            "4": "HSDPA",
            "5": "HSUPA",
            "6": "HSPA",
            "7": "LTE",
            "8": "EC-GSM",
            "9": "LTE-M",
            "10": "NB-IoT",
            "11": "NR",
        }.get(m.group(2), "") if m else ""
        r = dev.command("AT+CPIN?", 3)
        m = re.search(r"\+CPIN:\s*(.+)", "\n".join(r.lines))
        if m:
            sim_state = m.group(1).strip().upper()
            info["sim_state"] = {
                "READY": "ready",
                "SIM PIN": "locked",
                "SIM PUK": "locked",
                "SIM PIN2": "locked",
                "SIM PUK2": "locked",
                "PH-NET PIN": "locked",
                "NOT INSERTED": "absent",
                "ABSENT": "absent",
                "FAIL": "fail",
            }.get(sim_state, sim_state)
        else:
            err = r.error_text()
            info["sim_state"] = "absent" if "+CME ERROR: 10" in err else ("fail" if "+CME ERROR: 13" in err else "")
        info["rssi_dbm"] = (-113 + 2 * info["csq_rssi"]) if info["csq_rssi"] is not None and info["csq_rssi"] < 99 else None
        info["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._last_status_at = time.monotonic()
        self._set(**info)

    def _refresh_static_info(self, dev, info: dict):
        """Device/SIM identifiers barely change: refresh only once per connect."""
        r = dev.command("AT+CGMM", 3)
        model = "".join(r.lines[:-1]) if r.lines else ""
        model = re.sub(r"^\+CGMM:\s*", "", model).strip(' "\n').strip()
        info["model"] = model
        r = dev.command("AT+CGMR", 3)
        fw = "".join(r.lines[:-1]) if r.lines else ""
        fw = re.sub(r"^\+CGMR:\s*", "", fw).strip(' "\n').strip()
        info["fw_version"] = fw
        r = dev.command("AT+CGSN", 3)
        imei = "".join(r.lines[:-1]) if r.lines else ""
        imei = re.sub(r"^\+CGSN:\s*", "", imei).strip(' "\n').strip()
        info["imei"] = imei
        r = dev.command("AT+CCID", 3)
        ccid = "".join(r.lines[:-1]) if r.lines else ""
        ccid = re.sub(r"^\+CCID:\s*", "", ccid).strip(' "\n').strip()
        info["ccid"] = ccid

    # ---------- receive SMS ----------

    def _poll_incoming(self):
        res = self.dev.command("AT+CMGL", 8)
        if not res.ok or not any("+CMGL:" in ln for ln in res.lines):
            return
        parts = []
        for msg in parse_cmgl(res.lines):
            sender = _norm_number(decode_address(msg["address_raw"], self.ucs2))
            content = msg["body"]
            if looks_ucs2(content):
                content = ucs2_decode(content)
            parts.append({
                "index": msg["index"],
                "sender": sender,
                "content": content,
                "scts": msg["scts"] or "",
            })
        for assembled in group_incoming(parts):
            self._store_incoming(assembled)
        self._purge_device_messages()

    def _store_incoming(self, assembled: dict):
        """Store (and notify) one assembled incoming message, then drop its SIM records."""
        sender = assembled["sender"]
        content = assembled["content"]
        scts = assembled["scts"]
        indices = assembled["indices"]
        if not self._already_stored(sender, content):
            mid = self.db.execute(
                "INSERT INTO messages (direction, sender, receiver, content, status, raw) "
                "VALUES ('in', ?, '', ?, 'stored', ?)",
                (sender, content, f"idx={','.join(indices)} scts={scts}".strip()),
            )
            msg_row = self.db.row("SELECT * FROM messages WHERE id=?", (mid,))
            log.info("Received SMS #%s from %s: %s%s", mid, sender, content[:40],
                     f" ({len(indices)} parts)" if len(indices) > 1 else "")
            if self.forwarder:
                self.forwarder.forward(self.db, msg_row)
            if self.on_message:
                try:
                    self.on_message(msg_row)
                except Exception:
                    log.exception("on_message callback failed")
        for index in indices:
            self._delete_msg(index)

    def _purge_device_messages(self):
        """SMS are inserted and deleted one by one; here we purge leftover read+sent entries so SIM storage does not fill up."""
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
            log.warning("Failed to delete SMS at %s", index)

    # ---------- send SMS ----------

    def send(self, message_id: int, number: str, content: str):
        self._send_queue.put((message_id, number, content))

    def _process_send_queue(self):
        while self.dev and self.dev.connected:
            try:
                mid, number, content = self._send_queue.get_nowait()
            except queue.Empty:
                return
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
                log.info("Sent SMS #%s to %s: %s", mid, number, "OK" if ok else f"FAIL {err}")
                self._emit_send_result(mid, ok)
            except Exception as exc:
                self.db.execute(
                    "UPDATE messages SET status='failed', raw=? WHERE id=?",
                    (str(exc)[:1000], mid),
                )
                log.warning("Send SMS #%s exception: %s", mid, exc)
                self._emit_send_result(mid, False)

    def _emit_send_result(self, message_id: int, ok: bool):
        try:
            if self.on_send_result:
                self.on_send_result(message_id, ok)
        except Exception:
            log.exception("on_send_result callback failed")