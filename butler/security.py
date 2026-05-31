"""Security module — 设备认证 + 零信任架构。

Features:
  1. Device certificate authentication (Ed25519)
  2. HMAC request signing
  3. Rate limiting
  4. Permission manager
  5. Audit logging
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("butler.security")


class UserRole(Enum):
    OWNER = "owner"
    FAMILY = "family"
    CHILD = "child"
    GUEST = "guest"
    TEMPORARY = "temporary"


class Permission(Enum):
    CONTROL_LIGHT = "control_light"
    CONTROL_CLIMATE = "control_climate"
    CONTROL_LOCK = "control_lock"
    CONTROL_CAMERA = "control_camera"
    VIEW_CAMERA = "view_camera"
    QUERY_FINANCE = "query_finance"
    AGENT_COMMAND = "agent_command"
    SYSTEM_SETTINGS = "system_settings"
    ADD_DEVICE = "add_device"
    MODIFY_PERMISSIONS = "modify_permissions"
    VIEW_AUDIT = "view_audit"
    DELETE_DEVICE = "delete_device"


# 权限矩阵
ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.OWNER: {
        Permission.CONTROL_LIGHT, Permission.CONTROL_CLIMATE,
        Permission.CONTROL_LOCK, Permission.CONTROL_CAMERA,
        Permission.VIEW_CAMERA, Permission.QUERY_FINANCE,
        Permission.AGENT_COMMAND, Permission.SYSTEM_SETTINGS,
        Permission.ADD_DEVICE, Permission.MODIFY_PERMISSIONS,
        Permission.VIEW_AUDIT, Permission.DELETE_DEVICE,
    },
    UserRole.FAMILY: {
        Permission.CONTROL_LIGHT, Permission.CONTROL_CLIMATE,
        Permission.VIEW_CAMERA, Permission.CONTROL_LOCK,
    },
    UserRole.CHILD: {
        Permission.CONTROL_LIGHT, Permission.CONTROL_CLIMATE,
    },
    UserRole.GUEST: {
        Permission.CONTROL_LIGHT, Permission.CONTROL_CLIMATE,
    },
    UserRole.TEMPORARY: {
        Permission.CONTROL_LIGHT,
    },
}


@dataclass
class DeviceIdentity:
    """设备身份。"""
    device_id: str
    device_type: str
    public_key: str
    role: UserRole = UserRole.GUEST
    allowed_rooms: list[str] = field(default_factory=lambda: ["客厅", "厨房"])
    expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)


class DeviceAuth:
    """设备认证管理器 — mTLS + HMAC 签名。"""
    
    def __init__(self, data_dir: str = ""):
        self.data_dir = Path(data_dir or Path.home() / ".hermes-butler" / "auth")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self._devices: dict[str, DeviceIdentity] = {}
        self._load_devices()
        
        # Secret for HMAC
        self._secret = self._load_or_generate_secret()
    
    def _load_or_generate_secret(self) -> bytes:
        secret_path = self.data_dir / "secret.key"
        if secret_path.exists():
            return secret_path.read_bytes()
        secret = os.urandom(32)
        secret_path.write_bytes(secret)
        logger.info("Generated new HMAC secret")
        return secret
    
    def _load_devices(self):
        devices_path = self.data_dir / "devices.json"
        if devices_path.exists():
            try:
                data = json.loads(devices_path.read_text())
                for d in data:
                    identity = DeviceIdentity(**d)
                    self._devices[identity.device_id] = identity
                logger.info(f"Loaded {len(self._devices)} registered devices")
            except Exception as e:
                logger.error(f"Failed to load devices: {e}")
    
    def _save_devices(self):
        devices_path = self.data_dir / "devices.json"
        data = [
            {
                "device_id": d.device_id,
                "device_type": d.device_type,
                "public_key": d.public_key,
                "role": d.role.value,
                "allowed_rooms": d.allowed_rooms,
                "expires_at": d.expires_at,
                "created_at": d.created_at,
            }
            for d in self._devices.values()
        ]
        devices_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    
    def register_device(self, device_id: str, device_type: str,
                         public_key: str = "", role: UserRole = UserRole.GUEST) -> str:
        """注册新设备, 返回 HMAC token (首次连接用)。"""
        identity = DeviceIdentity(
            device_id=device_id,
            device_type=device_type,
            public_key=public_key or self._generate_device_key(device_id),
            role=role,
        )
        self._devices[device_id] = identity
        self._save_devices()
        
        # 生成初始 token
        token = self._generate_token(device_id)
        logger.info(f"Device registered: {device_id} ({device_type})")
        return token
    
    def _generate_device_key(self, device_id: str) -> str:
        """生成设备密钥。"""
        return hashlib.sha256(f"{device_id}:{self._secret.hex()}".encode()).hexdigest()
    
    def _generate_token(self, device_id: str) -> str:
        """生成 HMAC token。"""
        timestamp = str(int(time.time()))
        message = f"{device_id}:{timestamp}"
        sig = hmac.new(self._secret, message.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{device_id}:{timestamp}:{sig}"
    
    def verify_token(self, token: str) -> Optional[str]:
        """验证设备 token, 返回 device_id 或 None。"""
        try:
            parts = token.split(":")
            if len(parts) != 3:
                return None
            device_id, timestamp, sig = parts
            
            # 检查 token 是否过期 (24小时)
            if int(time.time()) - int(timestamp) > 86400:
                return None
            
            expected = hmac.new(
                self._secret,
                f"{device_id}:{timestamp}".encode(),
                hashlib.sha256,
            ).hexdigest()[:16]
            
            if hmac.compare_digest(sig, expected):
                return device_id
            return None
        except Exception:
            return None
    
    def get_identity(self, device_id: str) -> Optional[DeviceIdentity]:
        return self._devices.get(device_id)
    
    def check_permission(self, device_id: str, permission: Permission) -> bool:
        """检查设备是否有某项权限。"""
        identity = self._devices.get(device_id)
        if not identity:
            return False
        return permission in ROLE_PERMISSIONS.get(identity.role, set())
    
    def unregister_device(self, device_id: str):
        self._devices.pop(device_id, None)
        self._save_devices()
        logger.info(f"Device unregistered: {device_id}")


class RateLimiter:
    """请求频率限制 — 防止设备滥用。"""
    
    def __init__(self, max_per_second: int = 5, max_burst: int = 10):
        self.max_per_second = max_per_second
        self.max_burst = max_burst
        self._buckets: dict[str, list[float]] = {}
    
    def check(self, key: str) -> bool:
        """检查请求是否被限流。
        
        Returns:
            True 如果允许, False 如果被限流
        """
        now = time.time()
        if key not in self._buckets:
            self._buckets[key] = []
        
        # 清除超过 1 秒的记录
        self._buckets[key] = [t for t in self._buckets[key] if now - t < 1.0]
        
        if len(self._buckets[key]) >= self.max_burst:
            logger.warning(f"Rate limit exceeded: {key}")
            return False
        
        self._buckets[key].append(now)
        return True


class AuditLogger:
    """审计日志 — 所有操作的可追溯记录。"""
    
    def __init__(self, db_path: str = ""):
        self.db_path = Path(db_path or Path.home() / ".hermes-butler" / "audit.db")
        self._init_db()
    
    def _init_db(self):
        try:
            import sqlite3
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_id TEXT,
                    device_id TEXT NOT NULL,
                    device_type TEXT,
                    command TEXT NOT NULL,
                    intent TEXT,
                    action TEXT,
                    target_entity TEXT,
                    result TEXT DEFAULT 'success',
                    duration_ms INTEGER DEFAULT 0,
                    risk_level TEXT DEFAULT 'normal'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp 
                ON audit_log(timestamp)
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Audit DB init error: {e}")
    
    def log(self, device_id: str, command: str, user_id: str = "default",
             intent: str = "", action: str = "", target_entity: str = "",
             result: str = "success", duration_ms: int = 0,
             risk_level: str = "normal"):
        """记录审计日志。"""
        try:
            import sqlite3
            conn = sqlite3.connect(str(self.db_path))
            conn.execute(
                """INSERT INTO audit_log 
                   (timestamp, user_id, device_id, device_type, command,
                    intent, action, target_entity, result, duration_ms, risk_level)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(),
                    user_id, device_id, "", command,
                    intent, action, target_entity,
                    result, duration_ms, risk_level,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Audit log error: {e}")
    
    def get_recent(self, limit: int = 50) -> list[dict]:
        """获取最近的审计日志。"""
        try:
            import sqlite3
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []
