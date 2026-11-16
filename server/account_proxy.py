"""账号代理只能来自代理池。

accounts.proxy_id 指向代理池条目（config.yaml 的 register.proxies[].id）；accounts.proxy
是该条目地址的副本，检测、接管、OAuth、导出等仍直接读它。

- 面板 / API 写账号代理时只接受 proxy_id，由服务端换成地址后成对写入。
- 注册、接管等流程拿到的是代理地址：先在代理池里找同一个代理（地址完全相同，
  或协议族、主机、端口、用户名、密码都相同，socks5 与 socks5h 视为同一协议族），
  找不到时自动加进代理池，再绑定。
- reconcile 在启动和代理池变化后运行：把旧版手填的代理迁移成代理池条目并绑定，
  并把代理池里改过的地址同步给已绑定的账号。
"""
from __future__ import annotations

import uuid
from collections import Counter
from urllib.parse import unquote, urlsplit

from account_manager.console import log
from server.config_store import CONFIG_LOCK, load_config, save_config

# 同一协议族的不同写法：本项目对 SOCKS 一律交给上游解析域名，两种写法行为一致。
_SCHEME_FAMILY = {"socks5h": "socks5", "socks4a": "socks4"}
_DEFAULT_PORTS = {"http": 80, "https": 443}

LABEL_MIGRATED = "账号迁移"
LABEL_REGISTER = "注册"
LABEL_TAKEOVER = "接管"


def endpoint_key(url: str) -> tuple | None:
    """代理地址 → 用于判断「是不是同一个代理」的键；解析失败返回 None。"""
    try:
        parts = urlsplit(str(url or "").strip())
        scheme = parts.scheme.lower()
        scheme = _SCHEME_FAMILY.get(scheme, scheme)
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    if not scheme or not host:
        return None
    return (scheme, host, port, unquote(parts.username or ""), unquote(parts.password or ""))


def find_entry(entries, url: str) -> dict | None:
    """在代理池里找与 url 相同的代理：先比完整地址，再比协议族 + 主机 + 端口 + 凭据。"""
    url = str(url or "").strip()
    if not url:
        return None
    exact = next((e for e in entries if e.get("url") == url), None)
    if exact is not None:
        return exact
    key = endpoint_key(url)
    if key is None:
        return None
    return next((e for e in entries if endpoint_key(e.get("url", "")) == key), None)


def _address(url: str) -> str:
    key = endpoint_key(url)
    if key is None:
        return "未知地址"
    host = f"[{key[1]}]" if ":" in key[1] else key[1]
    return f"{host}:{key[2]}" if key[2] else host


def _new_entry(url: str, *, label: str, names: set) -> dict:
    base = f"{label} · {_address(url)}"
    name, n = base, 2
    while name in names:
        name, n = f"{base} #{n}", n + 1
    names.add(name)
    return {"id": str(uuid.uuid4()), "name": name, "url": url}


def _valid(url: str) -> bool:
    from account_manager.browser import validate_proxy

    try:
        validate_proxy(url)
    except ValueError:
        return False
    return True


def ensure_entry(config_path, url: str, *, label: str) -> dict:
    """返回代理池里与 url 相同的条目；没有就新建一个（地址无效抛 ValueError）。"""
    url = str(url or "").strip()
    if not url:
        raise ValueError("代理地址为空")
    with CONFIG_LOCK:
        entries = [dict(e) for e in load_config(config_path).saved_proxies]
        found = find_entry(entries, url)
        if found is not None:
            return found
        entry = _new_entry(url, label=label, names={e.get("name") for e in entries})
        save_config(config_path, {"saved_proxies": [*entries, entry]})
        log(f"代理池新增条目「{entry['name']}」（来自{label}）。")
        return entry


def bind_url(config_path, url: str, *, label: str) -> tuple[str, str]:
    """代理地址 → （代理池条目 id，条目地址）。url 为空返回 ("", "")。"""
    if not str(url or "").strip():
        return "", ""
    entry = ensure_entry(config_path, url, label=label)
    return entry["id"], entry["url"]


def reconcile(conn, config_path) -> dict:
    """让每个账号的代理都对应代理池条目，返回各类处理的计数。

    - 已绑定且条目还在：地址与条目不同时改成条目地址（代理池里编辑 / 同步过）；
    - 未绑定（旧版手填）或绑定的条目已不在：按地址找同一个代理，找不到就新建条目；
    - 地址本身无效、无法放进代理池的保持原样并计入 invalid，等用户在面板里改选。
    """
    stats: Counter = Counter()
    with CONFIG_LOCK:
        entries = [dict(e) for e in load_config(config_path).saved_proxies]
        by_id = {e["id"]: e for e in entries}
        names = {e.get("name") for e in entries}
        created: list[dict] = []
        updates: list[tuple[str, str, str]] = []  # (proxy_id, proxy, email)
        rows = conn.execute("SELECT email, proxy, proxy_id FROM accounts").fetchall()
        for row in rows:
            email, url, pid = row[0], (row[1] or "").strip(), (row[2] or "").strip()
            if pid and pid in by_id:
                if row[1] != by_id[pid]["url"]:
                    updates.append((pid, by_id[pid]["url"], email))
                    stats["synced"] += 1
                continue
            if not url:
                if pid or row[1]:
                    updates.append(("", "", email))
                    stats["cleared"] += 1
                continue
            entry = find_entry(entries, url)
            if entry is None:
                if not _valid(url):
                    stats["invalid"] += 1
                    continue
                entry = _new_entry(url, label=LABEL_MIGRATED, names=names)
                entries.append(entry)
                by_id[entry["id"]] = entry
                created.append(entry)
                stats["created"] += 1
            updates.append((entry["id"], entry["url"], email))
            stats["bound"] += 1
        if created:
            # 先落代理池再改账号：配置写失败时账号保持原样，下次启动重试。
            save_config(config_path, {"saved_proxies": entries})
        if updates:
            conn.executemany("UPDATE accounts SET proxy_id=?, proxy=? WHERE email=?", updates)
            conn.commit()
    if stats:
        parts = {
            "bound": "绑定", "created": "新建代理池条目", "synced": "同步地址",
            "cleared": "清理空绑定", "invalid": "地址无效未迁移",
        }
        log("账号代理整理：" + "，".join(f"{parts[k]} {stats[k]}" for k in parts if stats[k]))
    return dict(stats)
