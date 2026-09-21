"""Environment configuration"""
import os
from dataclasses import dataclass


def _env_list(key: str, default: str):
    raw = os.environ.get(key, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass
class Settings:
    device_port: str  # "auto" auto-probes
    device_baudrate: int
    device_probe_ports: list
    db_path: str
    jwt_secret: str
    jwt_expires_hours: int
    poll_interval: float
    retry_interval: float
    status_interval: float

    @classmethod
    def load(cls):
        return cls(
            device_port=os.environ.get("DEVICE_PORT", "auto"),
            device_baudrate=int(os.environ.get("DEVICE_BAUDRATE", "115200")),
            device_probe_ports=_env_list(
                "DEVICE_PROBE_PORTS", "/dev/ttyACM0,/dev/ttyACM1,/dev/ttyACM2"
            ),
            db_path=os.environ.get("DB_PATH", "/data/bridge.db"),
            # empty: auto-generated on first start and persisted to the settings table
            jwt_secret=os.environ.get("JWT_SECRET", ""),
            jwt_expires_hours=int(os.environ.get("JWT_EXPIRES_HOURS", "72")),
            poll_interval=float(os.environ.get("POLL_INTERVAL", "5.0")),
            retry_interval=float(os.environ.get("CONNECT_RETRY_INTERVAL", "3.0")),
            status_interval=float(os.environ.get("STATUS_INTERVAL", "30.0")),
        )


settings = Settings.load()