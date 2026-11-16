// 代理地址与测试结果的展示辅助：只做展示，不做校验（校验以服务端为准）。

export function parseProxyUrl(url) {
  try {
    const u = new URL(String(url || "").trim());
    const scheme = u.protocol.replace(/:$/, "").toLowerCase();
    // URL 会省略协议默认端口（http 80 / https 443），展示时补回来
    const port = u.port || { http: "80", https: "443" }[scheme] || "";
    return {
      scheme,
      host: u.hostname,
      port,
      address: port ? `${u.hostname}:${port}` : u.hostname,
      hasAuth: Boolean(u.username || u.password),
    };
  } catch {
    return null;
  }
}

// 展示用：隐藏用户名之外的凭据，形如 socks5://user:***@host:port
export function maskProxyUrl(url) {
  try {
    const u = new URL(String(url || "").trim());
    const auth = u.username || u.password
      ? `${decodeURIComponent(u.username)}${u.password ? ":***" : ""}@`
      : "";
    return `${u.protocol}//${auth}${u.host}`;
  } catch {
    return String(url || "").replace(/:([^:@/]+)@/, ":***@");
  }
}

export function protocolLabel(scheme) {
  if (!scheme) return "?";
  return scheme === "socks5h" ? "SOCKS5H" : scheme.toUpperCase();
}

// 两位国家代码 → 国旗 emoji；无代码返回空串
export function flagEmoji(code) {
  const cc = String(code || "").toUpperCase();
  if (!/^[A-Z]{2}$/.test(cc)) return "";
  return String.fromCodePoint(...[...cc].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

export function latencyClass(ms) {
  if (ms == null) return "text-muted-foreground";
  if (ms < 400) return "text-success";
  if (ms < 1000) return "text-warning";
  return "text-destructive";
}

export function relTime(iso) {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

// 测试结果只存浏览器本地（按代理条目 ID），刷新页面后仍能看到上次结果。
const TEST_KEY = "cr:proxy-tests";

export function loadTestResults() {
  try {
    const raw = JSON.parse(localStorage.getItem(TEST_KEY) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch {
    return {};
  }
}

export function saveTestResults(results, ids) {
  const keep = {};
  for (const id of ids) if (results[id]) keep[id] = results[id];
  try {
    localStorage.setItem(TEST_KEY, JSON.stringify(keep));
  } catch {
    /* 存不下就算了 */
  }
}
