"""sub2api 代理池对接：把 sub2api 里的代理导入本地代理池，之后可一键同步。

- 只读 sub2api：GET /api/v1/admin/proxies/all?with_count=true（只含启用中的代理）。
- 代理密码只在服务端用于组装代理地址并写入 config.yaml；列表接口回给前端的摘要
  不含用户名和密码。受限的管理员 Key 读不到凭据，此时一律不可导入，避免把需要
  认证的代理当成无认证代理写进来。
- 导入的条目记下来源实例（source_base_url）与 sub2api 代理 ID（source_id）。再次导入
  或「同步」时按这两者对应：只更新地址与地区，保留本地名称和条目 ID（注册任务、
  导入对话框都按条目 ID 选代理）。
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request

from account_manager.browser import validate_proxy
from account_manager.console import log
from server.config_store import CONFIG_LOCK, PROXY_SOURCE_SUB2API, load_config, save_config
from server.sub2api import Sub2APIClient, Sub2APIError, normalize_base_url

PROTOCOLS = ("http", "https", "socks5", "socks5h")
MAX_IMPORT = 500
CREDENTIALS_HIDDEN = "当前 sub2api Key 无权读取代理凭据，请在系统设置中改用完整的管理员 API Key"
_HOST_RE = re.compile(r"^(?:[A-Za-z0-9_.-]+|\[[0-9A-Fa-f:.]+\])$")
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def build_url(item: dict) -> str:
    """sub2api 代理 → 浏览器可用的代理地址；不可用时抛 ValueError（消息不含凭据）。"""
    protocol = str(item.get("protocol") or "").strip().lower()
    if protocol not in PROTOCOLS:
        raise ValueError(f"不支持的代理协议：{protocol or '空'}")
    host = str(item.get("host") or "").strip()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6
    port = item.get("port")
    if not _HOST_RE.fullmatch(host) or type(port) is not int or not 0 < port < 65536:
        raise ValueError("代理主机或端口无效")
    user = str(item.get("username") or "")
    password = str(item.get("password") or "")
    auth = ""
    if user or password:
        auth = quote(user, safe="") + (f":{quote(password, safe='')}" if password else "") + "@"
    url = f"{protocol}://{auth}{host}:{port}"
    try:
        validate_proxy(url)
    except ValueError:
        raise ValueError("浏览器无法使用该代理（协议不受支持或凭据过长）") from None
    return url


def parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = _FRACTION_RE.sub(r"\1", value.strip())  # Go 的纳秒精度超出 Python 解析范围
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def location_label(item: dict) -> str:
    """「国家 · 城市」，没有城市时退回地区。"""
    country = str(item.get("country") or "").strip()
    place = str(item.get("city") or "").strip() or str(item.get("region") or "").strip()
    return " · ".join(dict.fromkeys(p for p in (country, place) if p))


def find_linked(entries, base_url: str, source_id: int) -> dict | None:
    return next((e for e in entries
                 if e.get("source") == PROXY_SOURCE_SUB2API
                 and e.get("source_id") == source_id
                 and e.get("source_base_url") == base_url), None)


def proxy_view(item: dict, *, base_url: str, entries, now: datetime) -> tuple[dict, str]:
    """返回（给前端的摘要，组装好的地址；不可导入时为空串）。摘要不含用户名和密码。"""
    pid = item["id"]
    expires = parse_time(item.get("expires_at"))
    expired = bool(expires and expires <= now)
    visible = "username" in item
    reason, url = "", ""
    if not visible:
        reason = CREDENTIALS_HIDDEN
    else:
        try:
            url = build_url(item)
        except ValueError as exc:
            reason = str(exc)
    if expired and not reason:
        reason = "代理已过期"
    if reason:
        url = ""
    latency = item.get("latency_ms")
    linked = find_linked(entries, base_url, pid)
    duplicate = None
    if url and linked is None:
        duplicate = next((e for e in entries if e.get("url") == url), None)
    view = {
        "id": pid,
        "name": str(item.get("name") or "").strip() or f"sub2api #{pid}",
        "protocol": str(item.get("protocol") or ""),
        "host": str(item.get("host") or ""),
        "port": item.get("port"),
        "has_auth": bool(item.get("username") or item.get("password")) if visible else None,
        "status": str(item.get("status") or ""),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ") if expires else "",
        "expired": expired,
        "account_count": item.get("account_count") if type(item.get("account_count")) is int else None,
        "latency_ms": latency if type(latency) is int else None,
        "latency_status": str(item.get("latency_status") or ""),
        "country_code": str(item.get("country_code") or "").upper(),
        "location": location_label(item),
        "quality_grade": str(item.get("quality_grade") or ""),
        "importable": not reason,
        "reason": reason,
        "local": None if linked is None else {
            "id": linked["id"],
            "name": linked["name"],
            "outdated": bool(url) and linked.get("url") != url,
        },
        "duplicate_of": duplicate["name"] if duplicate else "",
    }
    return view, url


def merge(entries, items: dict[int, dict], ids, *, base_url: str, now: datetime):
    """把所选 sub2api 代理并入本地代理池。返回（新的条目列表，逐条结果）。"""
    entries = [dict(e) for e in entries]
    names = {e.get("name") for e in entries}
    results: list[dict] = []
    for pid in ids:
        linked = find_linked(entries, base_url, pid)
        item = items.get(pid)
        if item is None:
            results.append({"id": pid, "name": linked["name"] if linked else f"#{pid}",
                            "result": "missing", "detail": "sub2api 中已不存在或已停用",
                            "local_id": linked["id"] if linked else None})
            continue
        view, url = proxy_view(item, base_url=base_url, entries=entries, now=now)
        base = {"id": pid, "name": view["name"]}
        if not url:
            results.append({**base, "result": "failed", "detail": view["reason"],
                            "local_id": linked["id"] if linked else None})
            continue
        location = view["location"]
        if linked is not None:
            changed = linked["url"] != url or linked.get("location", "") != location
            linked["url"] = url
            if location:
                linked["location"] = location
            else:
                linked.pop("location", None)
            results.append({**base, "name": linked["name"],
                            "result": "updated" if changed else "unchanged",
                            "detail": "", "local_id": linked["id"]})
            continue
        duplicate = next((e for e in entries if e.get("url") == url), None)
        if duplicate is not None:
            results.append({**base, "result": "skipped",
                            "detail": f"代理池已有相同地址：{duplicate['name']}",
                            "local_id": duplicate["id"]})
            continue
        name = view["name"]
        if name in names:
            name = f"{name} #{pid}"
        entry = {"id": f"s2a-{uuid.uuid4().hex[:12]}", "name": name, "url": url,
                 "source": PROXY_SOURCE_SUB2API, "source_id": pid,
                 "source_base_url": base_url}
        if location:
            entry["location"] = location
        entries.append(entry)
        names.add(name)
        results.append({**base, "name": name, "result": "created", "detail": "",
                        "local_id": entry["id"]})
    return entries, results


def summarize(results) -> dict:
    keys = ("created", "updated", "unchanged", "skipped", "failed", "missing")
    return {k: sum(1 for r in results if r["result"] == k) for k in keys}


def register_sub2api_proxy_routes(app: FastAPI, state, *, require_auth) -> None:
    def now() -> datetime:
        return parse_time(state.now_fn()) or datetime.now(timezone.utc)

    def fetch() -> tuple[str, list[dict]]:
        cfg = state.config()
        try:
            client = Sub2APIClient(cfg.sub2api_base_url, cfg.sub2api_admin_key)
        except Sub2APIError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        try:
            with client:
                return client.base_url, client.list_proxies()
        except Sub2APIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    def apply(base_url: str, items: list[dict], ids_for) -> dict:
        """在配置锁内重新读取本地代理池再合并，避免覆盖并发的面板保存。"""
        by_id = {i["id"]: i for i in items}
        with CONFIG_LOCK:
            current = list(load_config(state.config_path).saved_proxies)
            ids = ids_for(current)
            merged, results = merge(current, by_id, ids, base_url=base_url, now=now())
            if merged != current:
                try:
                    save_config(state.config_path, {"saved_proxies": merged})
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from None
                # 同步改了地址时，已绑定这些条目的账号跟着用新地址
                state.reconcile_account_proxies()
        summary = summarize(results)
        log("sub2api 代理同步：" + "，".join(f"{k} {v}" for k, v in summary.items() if v))
        return {"summary": summary, "results": results}

    async def read_body(request: Request) -> dict:
        try:
            body = await request.json() if await request.body() else {}
        except Exception:  # noqa: BLE001 — 非 JSON 属于客户端错误
            raise HTTPException(status_code=400, detail="请求体必须是 JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="请求体必须是对象")
        return body

    @app.get("/api/sub2api/proxies")
    async def sub2api_proxies(_=Depends(require_auth)):
        base_url, items = await asyncio.to_thread(fetch)
        entries = list(state.config().saved_proxies)
        at = now()
        views = [proxy_view(i, base_url=base_url, entries=entries, now=at)[0] for i in items]
        return {
            "base_url": base_url,
            "credentials_visible": all("username" in i for i in items),
            "proxies": views,
        }

    @app.post("/api/sub2api/proxies/import")
    async def sub2api_proxies_import(request: Request, _=Depends(require_auth)):
        body = await read_body(request)
        raw = body.get("ids")
        if not isinstance(raw, list) or not raw or any(type(i) is not int or i <= 0 for i in raw):
            raise HTTPException(status_code=400, detail="ids 必须是非空的 sub2api 代理 ID 列表")
        ids = list(dict.fromkeys(raw))
        if len(ids) > MAX_IMPORT:
            raise HTTPException(status_code=400, detail=f"一次最多导入 {MAX_IMPORT} 个代理")
        base_url, items = await asyncio.to_thread(fetch)
        return await asyncio.to_thread(apply, base_url, items, lambda _current: ids)

    @app.post("/api/sub2api/proxies/sync")
    async def sub2api_proxies_sync(_=Depends(require_auth)):
        """按 sub2api 当前数据刷新所有已导入条目的地址；上游已删除/停用的只报告，不删除。"""
        base_url = normalize_base_url(state.config().sub2api_base_url)
        if not any(e.get("source") == PROXY_SOURCE_SUB2API and e.get("source_base_url") == base_url
                   for e in state.config().saved_proxies):
            return {"summary": summarize([]), "results": []}
        base_url, items = await asyncio.to_thread(fetch)

        def linked_ids(current):
            return [e["source_id"] for e in current
                    if e.get("source") == PROXY_SOURCE_SUB2API
                    and e.get("source_base_url") == base_url]

        return await asyncio.to_thread(apply, base_url, items, linked_ids)
