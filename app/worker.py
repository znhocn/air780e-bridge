"""Background serial worker: connection management, SMS polling, outbound SMS queue, status collection."""

import logging
import queue
import re
import threading
import time

from .atdevice import ATDevice, CommandError, gsm7_decode, unpack_septets
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
    (Receive polling uses PDU mode via :func:`parse_cmgl_pdu` / :func:`parse_sms_pdu`;
    kept for diagnostics/CLI use.)
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


# ---------------- PDU-mode receive (exact long-SMS reassembly) ----------------
#
# TEXT-mode CMGL strips the UDH, so multi-segment SMS can only be ordered by
# SIM index -- which reflects ARRIVAL order, not the network segment order, and
# produces scrambled text for out-of-order page delivery. Reading the records
# in PDU mode keeps each segment's real `05 00 03 <ref> <total> <seq>` header,
# letting us reassemble exactly. Air780E PDU-mode `AT+CMGL` (no argument)
# reliably lists REC UNREAD entries as full PDUs.

_CONCAT_IEI = 0x00  # IEI for "8-bit reference, concatenated short messages"


def _parse_pdu_udh(header: bytes):
    """Extract (ref, total, seq) from a concatenation User Data Header, if present."""
    if len(header) < 6 or header[0] != 0x05:
        return None
    iei, length = header[1], header[2]
    if iei == _CONCAT_IEI and length == 0x03:
        return header[3], header[4], header[5]
    return None


def _decode_semi_octet(raw: bytes) -> str:
    """Semi-octet BCD: first digit in low nibble, 'F' marks the start of the pad."""
    digits = ""
    for b in raw:
        lo, hi = b & 0x0F, (b >> 4) & 0x0F
        if hi == 0x0F:
            digits += f"{lo:X}"
            break
        digits += f"{lo:X}{hi:X}"
    return digits


def _decode_pdu_address(toa: int, raw: bytes) -> str:
    ton = toa & 0x70
    if ton == 0x50:  # alphanumeric: packed GSM-7bit
        try:
            n = (len(raw) * 8 + 6) // 7
            return gsm7_decode(unpack_septets(raw, n))
        except Exception:
            return raw.hex()
    digits = _decode_semi_octet(raw)
    return "+" + digits if ton == 0x10 else digits


def _decode_pdu_scts(raw: bytes) -> str:
    """TP-SCTS (7 octets, nibble-swapped BCD + timezone in quarter hours)."""
    if len(raw) < 7:
        return ""
    def digits(b: int) -> str:
        return f"{b & 0x0F:X}{(b >> 4) & 0x0F:X}"

    neg = bool(raw[6] & 0x80)
    tz = f"{(raw[6] & 0x0F):X}{(raw[6] >> 4) & 0x07:X}"
    return f"{digits(raw[0])}/{digits(raw[1])}/{digits(raw[2])}," \
           f"{digits(raw[3])}:{digits(raw[4])}:{digits(raw[5])}{'-' if neg else '+'}{tz}"


def parse_sms_pdu(pdu_hex: str) -> dict:
    """Parse one PDU-mode CMGL/CMGR readback (full PDU, SCA included).

    Handles SMS-DELIVER (network MT) and SMS-SUBMIT (CMGW self-tests) shapes.
    Returns sender / scts / content, with ref/total/seq when a concatenation
    UDH is present. Raises ValueError on malformed input.
    """
    b = bytes.fromhex(pdu_hex.strip())
    if len(b) < 5:
        raise ValueError("PDU too short")
    # Air780E strips the SCA octet from CMGL/CMGR output; tolerate an explicit
    # SCA of length 0 when fed the full PDU (e.g. self-tests via CMGW).
    i = 1 if b[0] == 0x00 else 0
    if i >= len(b):
        raise ValueError("PDU ends inside SCA")
    fo = b[i]
    i += 1
    tp = fo & 0x03
    scts_raw = b""
    if tp in (0x00, 0x01):  # SMS-DELIVER / SMS-SUBMIT
        if tp == 0x01:
            i += 1  # TP-MR
        if i >= len(b):
            raise ValueError("PDU ends before address")
        addr_len = b[i]
        i += 1
        addr_octets = 1 + (addr_len + 1) // 2  # type-of-address byte + packed digits
        if i + addr_octets > len(b):
            raise ValueError("PDU ends inside address")
        addr_data = b[i:i + addr_octets]
        i += addr_octets
        toa = addr_data[0] if addr_data else 0x81
        sender = _decode_pdu_address(toa, addr_data[1:])
        if i + 2 > len(b):
            raise ValueError("PDU ends before PID/DCS")
        pid = b[i]
        dcs = b[i + 1]
        i += 2
        if tp == 0x00:
            if i + 7 > len(b):
                raise ValueError("PDU ends inside SCTS")
            scts_raw = b[i:i + 7]
            i += 7
        elif fo & 0x10:  # SUBMIT with relative validity
            i += 1
    else:
        raise ValueError(f"Unsupported TP-MTI {tp}")
    if i >= len(b):
        raise ValueError("PDU ends before UDL")
    udl = b[i]
    i += 1
    ud = b[i:i + udl]  # udl octets for 8-bit/UCS2; for GSM-7bit it is septet count
    alpha = dcs & 0x0F
    udhi = bool(fo & 0x40)

    content = ""
    ref = total = seq = None
    if alpha == 0x08:  # UCS2: udl counts octets
        header = ud[:6] if udhi and len(ud) >= 6 else b""
        if udhi and len(ud) >= 6:
            out = _parse_pdu_udh(ud[:6])
            if out:
                ref, total, seq = out
        body = ud[len(header):]
        content = bytes(body).decode("utf-16-be", errors="replace")
    elif alpha == 0x00:  # GSM-7bit: udl counts septets
        septets = unpack_septets(ud, udl)
        if udhi:
            if len(septets) >= 6:
                out = _parse_pdu_udh(bytes(septets[:6]))
                if out:
                    ref, total, seq = out
            body = septets[6:]
        else:
            body = septets
        content = gsm7_decode(list(body))
    elif alpha == 0x04:  # 8-bit data (rare)
        body = ud[6:] if udhi and len(ud) >= 6 else ud
        content = bytes(body).decode("latin-1", errors="replace")
    return {
        "sender": sender,
        "scts": _decode_pdu_scts(scts_raw),
        "content": content,
        "ref": ref,
        "total": total,
        "seq": seq,
        "udhi": udhi,
    }


def parse_cmgl_pdu(lines: list) -> list:
    """Parse PDU-mode AT+CMGL output into [{index, pdu_hex}, ...]."""
    records = []
    cur = None
    for line in lines:
        line = line.strip()
        m = re.match(r'^\+CMGL:\s*(\d+)\s*,', line)
        if m:
            cur = {"index": m.group(1), "pdu": ""}
            records.append(cur)
            continue
        if cur is not None and len(line) > 10 and re.fullmatch(r"[0-9A-Fa-f]+", line):
            cur["pdu"] = line
            cur = None
    return [r for r in records if r["pdu"]]


def group_pdu_parts(parts: list) -> list:
    """Assemble PDU-mode records into messages using the exact segment order.

    Concatenation parts are grouped by (sender, ref, total) and sorted by the
    real TP-UDH sequence number, so out-of-order delivery reassembles exactly.
    Non-concatenated records pass through unchanged.
    """
    singles = []
    buckets: dict[tuple, dict] = {}
    order: list[tuple] = []
    for p in parts:
        if p["ref"] is None:
            singles.append(p)
            continue
        key = (p["sender"], p["ref"], p["total"])
        if key not in buckets:
            buckets[key] = {"parts": []}
            order.append(key)
        buckets[key]["parts"].append(p)
    out = []
    for p in singles:
        out.append({
            "sender": p["sender"],
            "scts": p["scts"],
            "indices": [p.get("index", "")],
            "content": p["content"],
            "raw": "",
        })
    for key in order:
        parts_list = buckets[key]["parts"]
        ps = sorted(parts_list, key=lambda p: p["seq"])
        idxs = [p.get("index", "") for p in parts_list]
        seq_list = ",".join(str(p["seq"]) for p in ps)
        sender, _ref, _total = key
        out.append({
            "sender": sender,
            "scts": ps[0]["scts"],
            "indices": idxs,
            "content": "".join(p["content"] for p in ps),
            "raw": f"ref={_ref} total={len(ps)} seq={seq_list}",
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
        # PDU-mode read so each segment keeps its real concatenation UDH and we
        # can reassemble long SMS in exact network order; TEXT mode afterwards.
        pdu_ok = False
        try:
            res = self.dev.command("AT+CMGF=0", 5)
            pdu_ok = True
            res = self.dev.command("AT+CMGL", 8)
            pdu_ok = res.ok
        except Exception:
            log.exception("PDU-mode receive poll failed")
            pdu_ok = False
        try:
            if pdu_ok:
                parts = []
                for rec in parse_cmgl_pdu(res.lines):
                    try:
                        parsed = parse_sms_pdu(rec["pdu"])
                        parsed["index"] = rec["index"]
                        parts.append(parsed)
                    except Exception as exc:
                        log.warning("Skipping unparseable SMS PDU at index %s: %s",
                                    rec["index"], exc)
                for assembled in group_pdu_parts(parts):
                    self._store_incoming(assembled)
        finally:
            try:
                self.dev.command("AT+CMGF=1", 5)
            except Exception:
                log.exception("Failed to restore TEXT mode after receive poll")
        self._purge_device_messages()

    def _store_incoming(self, assembled: dict):
        """Store (and notify) one assembled incoming message, then drop its SIM records."""
        sender = assembled["sender"]
        content = assembled["content"]
        scts = assembled["scts"]
        indices = assembled["indices"]
        meta = assembled["raw"]
        raw = " ".join(x for x in (f"idx={','.join(indices)}", meta, f"scts={scts}") if x).strip()
        if not self._already_stored(sender, content):
            mid = self.db.execute(
                "INSERT INTO messages (direction, sender, receiver, content, status, raw) "
                "VALUES ('in', ?, '', ?, 'stored', ?)",
                (sender, content, raw),
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