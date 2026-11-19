"""应用级单例装配：DB 连接、Config 路径、Runner。"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from server import account_proxy, db
from server.config_store import Config, load_config
from server.legacy_xray import migrate_register_proxy
from server.runner import Runner
from server.takeover import TakeoverManager
from server.android import AndroidManager


DB_FILENAME = "account-manager.db"
# 项目改名前（claude-register）使用的数据库文件名；旧部署未迁移时继续沿用。
LEGACY_DB_FILENAME = "claude-register.db"


def resolve_db_path(data_dir: Path) -> Path:
    """优先使用新文件名；只有旧文件存在时沿用旧文件，避免改名后丢数据。"""
    current = Path(data_dir) / DB_FILENAME
    legacy = Path(data_dir) / LEGACY_DB_FILENAME
    if not current.exists() and legacy.exists():
        return legacy
    return current


def default_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AppState:
    def __init__(self, data_dir: Path, config_path: Path, now_fn=default_now):
        self.data_dir = Path(data_dir)
        self.config_path = Path(config_path)
        self.now_fn = now_fn
        migrate_register_proxy(self.config_path, self.data_dir)
        self.conn = db.init_db(resolve_db_path(self.data_dir))
        db.mark_stale_running_as_failed(self.conn)  # 重启清理残留 running
        # 账号代理只能来自代理池：把旧版手填的代理迁移成代理池条目并绑定（幂等）。
        account_proxy.reconcile(self.conn, self.config_path)
        self.runner = Runner(
            self.conn, self.data_dir, now_fn, proxy_binder=self.bind_proxy_url,
        )
        # 首次启动生成随机会话密钥并持久化（data_dir 已 gitignore，不会被提交）。
        secret_path = self.data_dir / "secret.key"
        if secret_path.exists():
            self.secret = secret_path.read_text(encoding="utf-8").strip()
        else:
            self.secret = secrets.token_hex(32)
            secret_path.write_text(self.secret, encoding="utf-8")
        self.takeover = TakeoverManager(now_fn=now_fn)
        self.android = AndroidManager(self.data_dir, now_fn=now_fn)

    def config(self) -> Config:
        return load_config(self.config_path)

    def bind_proxy_url(self, url: str, *, label: str = account_proxy.LABEL_REGISTER):
        """代理地址 → （代理池条目 id，条目地址）；代理池里没有时自动加入。"""
        return account_proxy.bind_url(self.config_path, url, label=label)

    def reconcile_account_proxies(self) -> dict:
        return account_proxy.reconcile(self.conn, self.config_path)
