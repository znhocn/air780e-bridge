"""Serial AT command layer (3GPP 27.005 / 27.007).

Design notes:
- A single reader thread parses lines; command request/response flows through CommandResult
- A per-command lock serializes commands so responses never interleave
- Supports the CMGS ">" prompt and the GSM/UCS2 charsets
- On reader thread failure, on_fatal callback notifies the upper layer of a disconnect
"""

import logging
import re
import secrets
import threading
import time

import serial

log = logging.getLogger("air780e.at")

FINAL_RE = re.compile(r"^(OK|ERROR|\+CME ERROR:.*|\+CMS ERROR:.*)$")
CLIP_RE = re.compile(r'^\+CLIP:\s*"([^"]*)"')

# GSM 03.38 default alphabet (DCS=0); index = septet code.
_GSM_BASIC = (
    "@\xa3$\xa5\xe8\xe9\xf9\xec\xf2\xc7\n\xd8\xf8\r\xc5\xe5"
    "\u0394_\u03a6\u0393\u039b\u03a9\u03a0\u03a8\u03a3\u0398\u039e\x1b\xc6\xe6\xe9\xc9 "
    "!\"\xa4%&\'()*+,-./0123456789:;<=>?\xa1"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ\xc4\xd6\xd1\xdc\xa7\xbf"
    "abcdefghijklmnopqrstuvwxyz\xe4\xf6\xf1\xfc\xe0"
)
_GSM_BASIC_INV = {c: i for i, c in enumerate(_GSM_BASIC)}
_GSM_EXT = {
    0x14: "^", 0x28: "{", 0x29: "}", 0x2F: "\\",
    0x3C: "[", 0x3D: "~", 0x3E: "]", 0x40: "|", 0x65: "\u20ac",
}
_GSM_EXT_INV = {c: i for i, c in _GSM_EXT.items()}


def gsm7_encodable(text: str) -> bool:
    return all(ch in _GSM_BASIC_INV or ch in _GSM_EXT_INV for ch in text)


def ascii_text_eligible(content: str) -> bool:
    """Whether content can safely go through TEXT-mode CMGS as raw bytes.

    ASCII bytes are safe as payload (they never collide with the 0x1A
    ctrl-Z terminator), except a literal ESC (0x1B): that byte is the
    GSM7 extension-alphabet escape, so the receiver would mis-decode
    everything after it. ESC content must go through the UCS2/PDU path.
    """
    return (
        gsm7_encodable(content)
        and all(ord(c) < 128 for c in content)
        and "\x1b" not in content
    )


def gsm7_septets(text: str) -> list:
    """Map text to GSM-7bit septet values (0x1B escape sequences unfold to 2 septets)."""
    out = []
    for ch in text:
        if ch in _GSM_BASIC_INV:
            out.append(_GSM_BASIC_INV[ch])
        else:
            out.append(0x1B)
            out.append(_GSM_EXT_INV[ch])
    return out


def gsm7_decode(septets) -> str:
    out = []
    i = 0
    while i < len(septets):
        s = septets[i]
        if s == 0x1B:
            if i + 1 < len(septets) and septets[i + 1] in _GSM_EXT:
                out.append(_GSM_EXT[septets[i + 1]])
            i += 2
        else:
            out.append(_GSM_BASIC[s] if s < len(_GSM_BASIC) else "?")
            i += 1
    return "".join(out)


def pack_septets(septets) -> bytes:
    """Pack GSM-7bit septet values into a byte stream (LSB-first, 3GPP style)."""
    bits = 0
    acc = 0
    out = bytearray()
    for s in septets:
        acc |= s << bits
        bits += 7
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    if bits:
        out.append(acc & 0xFF)
    return bytes(out)


def pack_septets_with_header(header: bytes, septets) -> bytes:
    """Pack GSM-7bit `septets` after an octet-aligned UDH, padded to a septet boundary.

    3GPP TS 23.038: with a UDH present in GSM 7-bit user data the header stays
    octet-aligned at the start of the field, then fill bits are inserted so the
    packed message begins at the next septet boundary (ceil(octets*8/7) septets
    from bit 0). The caller must set UDL to header_septets + len(septets).
    """
    bits = 0
    acc = 0
    total = 0
    out = bytearray()
    for b in header:
        acc |= b << bits
        bits += 8
        total += 8
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    # Fill bits so the packed text starts on the next septet boundary. `total`
    # keeps the absolute bit position (`bits` is only the byte remainder).
    pad = (-total) % 7
    total += pad
    bits += pad
    while bits >= 8:
        out.append(acc & 0xFF)
        acc >>= 8
        bits -= 8
    for s in septets:
        acc |= s << bits
        bits += 7
        total += 7
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    if bits:
        out.append(acc & 0xFF)
    return bytes(out)


def unpack_septets(data: bytes, n: int) -> list:
    out = []
    bit = 0
    for _ in range(n):
        val = 0
        shift = 0
        need = 7
        while need:
            b = data[bit >> 3] if (bit >> 3) < len(data) else 0
            off = bit & 7
            take = min(need, 8 - off)
            val |= ((b >> off) & ((1 << take) - 1)) << shift
            shift += take
            need -= take
            bit += take
        out.append(val)
    return out


def ucs2_hex(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


def pdu_address(number: str) -> tuple[int, str]:
    """Pack a phone number into a TP-DA (address length, type+digits bytes).

    Semi-octet packing puts the first digit in the low nibble, matching the
    GSM SMSC format this module uses (same convention as SMS-center numbers).
    """
    digits = "".join(c for c in number if c.isdigit())
    packed = []
    for i in range(0, len(digits), 2):
        hi = digits[i + 1] if i + 1 < len(digits) else "F"
        packed.append(f"{int(hi, 16):X}{int(digits[i], 16):X}")
    toa = "91" if number.startswith("+") else "81"
    return len(digits), toa + "".join(packed).upper()


def ucs2_pdu(number: str, text: str, udh: bytes = None) -> str:
    """Build an SMS-SUBMIT PDU (DCS=8 UCS2) as a hex string.

    The leading '00' SCA field tells the module to use the SIM's message
    center, mirroring Air780E's own text/PDU examples. Returns the full PDU
    hex; the CMGS <length> parameter must exclude that 1-byte SCA octet.

    When `udh` is given the TP-UDHI bit is set (fo=0x51); UDL counts the
    header bytes + the UCS2 payload in octets.
    """
    body = text.encode("utf-16-be")
    if udh:
        body = udh + body
    alen, addr = pdu_address(number)
    fo = "51" if udh else "11"
    return (
        "00" +                       # SCA: use SMSC from the SIM
        fo +                         # fo: SMS-SUBMIT, validity relative (+UDHI if concat)
        "00" +                       # mr
        f"{alen:02X}" + addr +       # destination address
        "00" +                       # pid
        "08" +                       # dcs: UCS2
        "A7" +                       # vp: relative, 24h
        f"{len(body):02X}" + body.hex().upper()
    )


def gsm7_pdu(number: str, text: str, udh: bytes = None) -> str:
    """Build an SMS-SUBMIT PDU (DCS=0 GSM 7-bit) as a hex string.

    UDL counts septets. With a UDH the header is octet-aligned (3GPP
    TS 23.038): the UDHL+UDH bytes are placed verbatim, fill bits pad to the
    next septet boundary, then the packed text follows. Any receiver reading
    the header as raw octets (real phones, SMSCs, this project's parser) gets
    the correct segment metadata and unshifted body.
    """
    text_septets = gsm7_septets(text)
    alen, addr = pdu_address(number)
    fo = "51" if udh else "11"
    if udh:
        header_septets = (len(udh) * 8 + 6) // 7
        packed = pack_septets_with_header(udh, text_septets)
        udl = header_septets + len(text_septets)
    else:
        packed = pack_septets(text_septets)
        udl = len(text_septets)
    return (
        "00" +                       # SCA: use SMSC from the SIM
        fo +                         # fo: SMS-SUBMIT, validity relative (+UDHI if concat)
        "00" +                       # mr
        f"{alen:02X}" + addr +       # destination address
        "00" +                       # pid
        "00" +                       # dcs: GSM 7-bit default alphabet
        "A7" +                       # vp: relative, 24h
        f"{udl:02X}" + packed.hex().upper()
    )


def concat_udh(ref: int, total: int, seq: int) -> bytes:
    """Concatenated-SMS user data header (IEI 0x00, 8-bit ref). 6 bytes incl. UHL.

    ref/total/seq are single octets (GSM 03.40), so a message can span at most
    255 segments; longer payloads are rejected at send time.
    """
    return bytes([0x05, 0x00, 0x03, ref & 0xFF, total, seq])


def split_ucs2(text: str, cap_units: int) -> list:
    """Split text into UCS2-sized segments honoring UTF-16 code units (astral chars=2)."""
    pieces, cur, units = [], [], 0
    for ch in text:
        u = 1 if ord(ch) < 0x10000 else 2
        if units and units + u > cap_units:
            pieces.append("".join(cur))
            cur, units = [ch], u
        else:
            cur.append(ch)
            units += u
    if cur or not pieces:
        pieces.append("".join(cur))
    return pieces or [""]


def split_gsm7(text: str, cap_septets: int) -> list:
    """Split text into 7-bit segments by septet budget (escape chars take 2)."""
    pieces, cur, used = [], [], 0
    for ch in text:
        s = 1 if ch in _GSM_BASIC_INV else 2
        if used and used + s > cap_septets:
            pieces.append("".join(cur))
            cur, used = [ch], s
        else:
            cur.append(ch)
            used += s
    if cur or not pieces:
        pieces.append("".join(cur))
    return pieces or [""]


def ucs2_decode(hexstr: str) -> str:
    try:
        return bytes.fromhex(hexstr).decode("utf-16-be", errors="replace")
    except ValueError:
        return hexstr


def looks_ucs2(s: str) -> bool:
    return len(s) >= 4 and len(s) % 4 == 0 and bool(re.fullmatch(r"[0-9A-Fa-f]+", s))


class CommandError(RuntimeError):
    pass


class CommandResult:
    """Result collector for a single command exchange."""

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.lines: list = []
        self.done = threading.Event()
        self.prompt = threading.Event()
        self.echo: str | None = None
        self.timed_out = False

    def wait(self):
        self.done.wait(self.timeout)

    @property
    def ok(self) -> bool:
        return bool(self.lines) and self.lines[-1] == "OK"

    @property
    def last(self) -> str:
        return self.lines[-1] if self.lines else ""

    def error_text(self) -> str:
        for ln in self.lines:
            if FINAL_RE.match(ln):
                return ln
        return "timeout" if self.timed_out else "no response"


class ATDevice:
    def __init__(self, port: str, baudrate: int = 115200, read_timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.read_timeout = read_timeout
        self.ser: serial.Serial | None = None
        self._lock = threading.Lock()
        self._current: CommandResult | None = None
        self._charset: str | None = None  # last applied CSCS, to avoid redundant switches
        self._ref_counter = secrets.randbits(16)  # concat SMS reference source
        self._running = False
        self._thread: threading.Thread | None = None
        self.on_fatal = None  # callable(exc)
        self.on_call = None  # callable(number) - incoming call (from +CLIP)

    def open(self):
        self.close()
        self.ser = serial.Serial(self.port, self.baudrate, timeout=self.read_timeout)
        self._running = True
        self._thread = threading.Thread(
            target=self._read_loop, daemon=True, name="at-reader"
        )
        self._thread.start()
        log.info("Serial opened %s @%d", self.port, self.baudrate)
        return self

    def close(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self._current = None

    @property
    def connected(self) -> bool:
        return self.ser is not None and self._running

    def _write(self, data: bytes):
        self.ser.flushInput()
        self.ser.write(data)
        self.ser.flush()

    def _read_loop(self):
        while self._running and self.ser is not None:
            try:
                raw = self.ser.readline()
            except Exception as exc:
                log.warning("Serial read failed: %s", exc)
                cb = self.on_fatal
                self._running = False
                if cb:
                    try:
                        cb(exc)
                    except Exception:
                        pass
                break
            if not raw:
                continue
            text = raw.decode(errors="replace").rstrip("\r\n")
            if not text:
                continue
            self._handle(text)

    def _handle(self, text: str):
        # Incoming-call events may arrive at any time (also mid-command); handle
        # them right away and keep them out of command results.
        m = CLIP_RE.match(text.strip())
        if m:
            self._emit_call(m.group(1))
            return
        cur = self._current
        if cur is not None:
            if text.strip() == ">" and not cur.prompt.is_set():
                cur.prompt.set()
                cur.lines.append(">")
                return
            if cur.echo and text.strip() == cur.echo:
                cur.echo = None
                return
            cur.lines.append(text)
            if FINAL_RE.match(text):
                cur.done.set()
        else:
            # Unsolicited notifications during idle (e.g. +CMTI); upper layer polls as a fallback
            log.debug("unsolicited: %s", text)

    def _emit_call(self, number: str):
        try:
            if self.on_call:
                self.on_call(number.strip())
        except Exception:
            log.exception("on_call callback failed")

    def _wait_prompt(self, cur: CommandResult, timeout: float) -> bool:
        """Wait for the CMGS '>' prompt; fail fast if a final line (OK/ERROR) arrives first."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cur.prompt.is_set():
                return True
            if cur.done.is_set():
                return False
            cur.done.wait(0.2)
        return False

    def _submit(self, cmd: str, timeout: float) -> CommandResult:
        cur = CommandResult(timeout)
        cur.echo = cmd
        self._current = cur
        try:
            self._write((cmd + "\r").encode())
            cur.wait()
        finally:
            self._current = None
        return cur

    def command(self, cmd: str, timeout: float = 5.0) -> CommandResult:
        """Send a simple command and return the result."""
        with self._lock:
            if not self.connected:
                raise CommandError("Device not connected")
            return self._submit(cmd, timeout)

    def send_sms(self, number: str, content: str, wait: float = 70.0):
        """Send an SMS; supports Chinese and long (multi-segment) messages.

        - Pure GSM-7bit ASCII of one segment: TEXT mode (160 chars, no PDU).
        - One-segment non-ASCII: PDU mode DCS=8 (UCS2) - on Air780E the
          TEXT-mode CMGS payload is taken verbatim (hex never decoded), so
          Chinese must go through a self-built PDU.
        - Multi-segment content is sent as ONE concatenated (UDHI) PDU stream:
          ASCII uses DCS=0 GSM-7bit (153 chars/seg, UDH prefix), non-ASCII uses
          DCS=8 UCS2 (67 chars/seg). The recipient reassembles a single long SMS.

        Returns (charset, results); results holds one CommandResult per segment.
        """
        number = number.strip()
        # Only GSM dial characters are allowed; anything else (e.g. quotes,
        # CR/LF) is stripped so a peer-supplied number can never inject extra
        # AT commands into the TEXT-mode AT+CMGS line.
        number = re.sub(r"[^\d+*#]", "", number)
        if not number:
            raise ValueError("Number is empty")
        use_gsm7 = ascii_text_eligible(content)

        with self._lock:
            if not self.connected:
                raise CommandError("Device not connected")
            if use_gsm7 and len(split_gsm7(content, 160)) == 1:
                return self._send_text_ascii(number, content, wait)
            return self._send_pdu(number, content, "gsm7" if use_gsm7 else "ucs2", wait)

    def _next_ref(self, bits: int) -> int:
        self._ref_counter = (self._ref_counter + 1) & ((1 << bits) - 1)
        return self._ref_counter

    def _send_pdu(self, number: str, content: str, mode: str, wait: float) -> tuple:
        """Send messages in PDU mode; multi-segment content becomes one concatenated SMS."""
        res = self._submit("AT+CMGF=0", 5)
        if not res.ok:
            raise CommandError(f"Failed to switch to PDU mode: {res.error_text()}")
        try:
            if mode == "gsm7":
                pieces = split_gsm7(content, 153)
            else:
                pieces = split_ucs2(content, 67)
            if len(pieces) > 255:
                raise CommandError(
                    f"Message too long for SMS: {len(pieces)} segments (max 255)"
                )
            ref = self._next_ref(8)
            results = []
            for seq, body in enumerate(pieces, 1):
                udh = concat_udh(ref, len(pieces), seq) if len(pieces) > 1 else None
                pdu = gsm7_pdu(number, body, udh) if mode == "gsm7" else ucs2_pdu(number, body, udh)
                length = (len(pdu) // 2) - 1  # CMGS length excludes the SCA octet
                res = self._cmgs_exchange(f"AT+CMGS={length}", pdu.encode(), wait)
                results.append(res)
                if not res.ok:
                    break
            return ("GSM" if mode == "gsm7" else "UCS2"), results
        finally:
            self._current = None
            try:
                res = self._submit("AT+CMGF=1", 5)  # restore TEXT mode for CMGL polling
                if not res.ok:
                    log.error("Failed to restore TEXT mode after PDU send: %s", res.error_text())
            except Exception as exc:
                log.error("Failed to restore TEXT mode after PDU send: %s", exc)

    def _send_text_ascii(self, number: str, content: str, wait: float) -> tuple:
        """Send an ASCII-only message in TEXT mode (GSM 7-bit, 160 chars/seg).

        Single-segment path only: the module takes the payload verbatim, so
        ASCII (whose byte values never collide with 0x1A) is safe as-is.
        Callers must not route content containing 0x1B here (see
        ascii_text_eligible).
        """
        charset = "GSM"
        if charset != self._charset:
            res = self._submit('AT+CSCS="GSM"', 5)
            if not res.ok:
                raise CommandError(f"Failed to set charset: {res.error_text()}")
            res = self._submit("AT+CSMP=17,167,0,0", 5)
            if not res.ok:
                raise CommandError(f"Failed to set text-mode params: {res.error_text()}")
            self._charset = charset
        res = self._cmgs_exchange(f'AT+CMGS="{number}"', content.encode(), wait)
        return charset, [res]

    def _cmgs_exchange(self, cmd: str, payload: bytes, wait: float) -> CommandResult:
        """Issue AT+CMGS, wait for '>', submit payload + ctrl-Z, return result."""
        cur = CommandResult(wait)
        cur.echo = cmd
        self._current = cur
        self._write((cmd + "\r").encode())
        try:
            if not self._wait_prompt(cur, 25):
                if not cur.done.is_set():
                    cur.timed_out = True
                    self._write(b"\x1b")  # ESC to abort
                cur.done.set()
                return cur
            self._write(payload)
            self._write(b"\x1a")  # ctrl-Z to submit
            cur.wait()
            return cur
        finally:
            self._current = None