"""串口 AT 命令层（3GPP 27.005 / 27.007）

设计要点：
- 单读线程解析行，命令请求/响应通过 CommandResult 传递
- 一条命令锁串行化，避免与其他命令交错
- 支持 CMGS 的 ">" 提示符与 GMT/UCS2 双字符集
- 读线程异常时通过 on_fatal 回调通知上层断线
"""

import logging
import re
import threading

import serial

log = logging.getLogger("air780.at")

FINAL_RE = re.compile(r"^(OK|ERROR|\+CME ERROR:.*|\+CMS ERROR:.*)$")


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
    """一次命令交换的收集结果。"""

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
        self._running = False
        self._thread: threading.Thread | None = None
        self.on_fatal = None  # callable(exc)

    def open(self):
        self.close()
        self.ser = serial.Serial(self.port, self.baudrate, timeout=self.read_timeout)
        self._running = True
        self._thread = threading.Thread(
            target=self._read_loop, daemon=True, name="at-reader"
        )
        self._thread.start()
        log.info("串口已打开 %s @%d", self.port, self.baudrate)
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
                log.warning("串口读失败: %s", exc)
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
            # 空闲期的异步上报（如 +CMTI），由上层轮询兜底
            log.debug("unsolicited: %s", text)

    def _submit(self, cmd: str, timeout: float) -> CommandResult:
        cur = CommandResult(timeout)
        cur.echo = cmd
        self._current = cur
        self._write((cmd + "\r").encode())
        cur.wait()
        self._current = None
        return cur

    def command(self, cmd: str, timeout: float = 5.0) -> CommandResult:
        """发送一条简单命令并返回结果。"""
        with self._lock:
            if not self.connected:
                raise CommandError("设备未连接")
            return self._submit(cmd, timeout)

    def send_sms(self, number: str, content: str, wait: float = 70.0):
        """发送短信，支持中文。按字符自动选择 GSM/UCS2 编码，返回结果列表。

        返回 (charset, results)，results 为每段的 CommandResult。
        """
        number = number.strip()
        if not number:
            raise ValueError("号码为空")
        pure_ascii = all(ord(c) < 128 for c in number + content)
        charset = "GSM" if pure_ascii else "UCS2"
        seg_len = 160 if pure_ascii else 67
        pieces = [content[i : i + seg_len] for i in range(0, len(content), seg_len)]
        if not pieces:
            pieces = [""]

        with self._lock:
            if not self.connected:
                raise CommandError("设备未连接")
            res = self._submit(f'AT+CSCS="{charset}"', 5)
            if not res.ok:
                raise CommandError(f"设置字符集失败: {res.error_text()}")

            addr = number if pure_ascii else ucs2_hex(number)
            results = []
            for body in pieces:
                cur = CommandResult(wait)
                cmd = f'AT+CMGS="{addr}"'
                cur.echo = cmd
                self._current = cur
                self._write((cmd + "\r").encode())
                if not cur.prompt.wait(25):
                    cur.timed_out = True
                    self._write(b"\x1b")  # ESC 退出
                    cur.done.set()
                    results.append(cur)
                    break
                payload = body if pure_ascii else ucs2_hex(body)
                self._write(payload.encode())
                self._write(b"\x1a")  # ctrl-Z 提交
                cur.wait()
                results.append(cur)
                self._current = None
                if not cur.ok:
                    break
            return charset, results