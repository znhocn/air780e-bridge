"""命令行工具：不经过 Web 服务，直接通过串口调试网关。

用法：
    python -m app.cli status
    python -m app.cli send 10086 "hello"        # 测试发给 10086 请用实际 SIM
    python -m app.cli probe                      # 探测所有候选串口
"""

import logging
import sys

from .atdevice import ATDevice
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _open():
    dev = ATDevice(settings.device_port if settings.device_port != "auto" else "/dev/ttyACM0", settings.device_baudrate)
    dev.open()
    return dev


def cmd_probe():
    from .atdevice import CommandError

    for port in settings.device_probe_ports:
        try:
            d = ATDevice(port, settings.device_baudrate)
            d.open()
            r = d.command("AT", 4)
            d.close()
            print(f"{port}: {'OK' if r.ok else '无响应'}")
        except Exception as e:
            print(f"{port}: {e}")


def cmd_status():
    dev = _open()
    d = dev
    d.command("ATE0", 3)
    for c in ["AT+CSQ", "AT+CREG?", "AT+COPS?", "AT+CGMM", "AT+CGSN", "AT+CPIN?"]:
        r = d.command(c, 5)
        body = " | ".join(r.lines)
        print(f"{c}: {body}")
    dev.close()


def cmd_send(number, content):
    dev = _open()
    try:
        charset, results = dev.send_sms(number, content)
        for r in results:
            print("  ->", " | ".join(r.lines), "OK" if r.ok else "FAIL")
        print(f"编码: {charset}")
    finally:
        dev.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "probe":
        cmd_probe()
    elif args[0] == "status":
        cmd_status()
    elif args[0] == "send" and len(args) >= 3:
        cmd_send(args[1], " ".join(args[2:]))
    else:
        print(__doc__)