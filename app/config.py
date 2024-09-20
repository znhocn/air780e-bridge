"""环境配置"""
import os
from dataclasses import dataclass, field


def _env_list(key: str, default: str):
    raw = os.environ.get(key, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass
class Settings:
    device_port: str  # "auto" 自动探测
    device_baudrate: int
    device_probe_ports: list
    db_path: str
    admin_password: str
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
            # ADMIN_PASSWORD 可选：设置后启动时播种一个名为 admin 的兼容账号（仅当无任何管理员时）
            admin_password=os.environ.get("ADMIN_PASSWORD", ""),
            # 留空则首次启动自动生成并持久化到数据库 settings 表
            jwt_secret=os.environ.get("JWT_SECRET", ""),
            jwt_expires_hours=int(os.environ.get("JWT_EXPIRES_HOURS", "72")),
            poll_interval=float(os.environ.get("POLL_INTERVAL", "5.0")),
            retry_interval=float(os.environ.get("CONNECT_RETRY_INTERVAL", "3.0")),
            status_interval=float(os.environ.get("STATUS_INTERVAL", "30.0")),
        )


settings = Settings.load()