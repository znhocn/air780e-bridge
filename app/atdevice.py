"""Serial AT command layer (3GPP 27.005 / 27.007).

Design notes:
- A single reader thread parses lines; command request/response flows through CommandResult
- A per-command lock serializes commands so responses never interleave
- Supports the CMGS ">" prompt and the GSM/UCS2 charsets
- On reader thread failure, on_fatal callback notifies the upper layer of a disconnect
"""

import logging
import re
import threading
import time

import serial

log = logging.getLogger("air780e.at")

FINAL_RE = re.compile(r"^(OK|ERROR|\+CME ERROR:.*|\+CMS ERROR:.*)$")
CLIP_RE = re.compile(r'^\+CLIP:\s*"([^"]*)"')


def ucs2_hex(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


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
        """Send an SMS; supports Chinese. Auto-selects GSM/UCS2 encoding per content.

        Returns (charset, results); results holds one CommandResult per segment.
        """
        number = number.strip()
        if not number:
            raise ValueError("Number is empty")
        pure_ascii = all(ord(c) < 128 for c in number + content)
        charset = "GSM" if pure_ascii else "UCS2"
        seg_len = 160 if pure_ascii else 67
        pieces = [content[i : i + seg_len] for i in range(0, len(content), seg_len)]
        if not pieces:
            pieces = [""]

        with self._lock:
            if not self.connected:
                raise CommandError("Device not connected")
            if charset != self._charset:
                res = self._submit(f'AT+CSCS="{charset}"', 5)
                if not res.ok:
                    raise CommandError(f"Failed to set charset: {res.error_text()}")
                self._charset = charset

            addr = number if pure_ascii else ucs2_hex(number)
            results = []
            try:
                for body in pieces:
                    cur = CommandResult(wait)
                    cmd = f'AT+CMGS="{addr}"'
                    cur.echo = cmd
                    self._current = cur
                    self._write((cmd + "\r").encode())
                    if not self._wait_prompt(cur, 25):
                        if not cur.done.is_set():
                            cur.timed_out = True
                            self._write(b"\x1b")  # ESC to abort
                        cur.done.set()
                        results.append(cur)
                        break
                    payload = body if pure_ascii else ucs2_hex(body)
                    self._write(payload.encode())
                    self._write(b"\x1a")  # ctrl-Z to submit
                    cur.wait()
                    results.append(cur)
                    if not cur.ok:
                        break
            finally:
                self._current = None
            return charset, results