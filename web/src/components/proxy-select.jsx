import { maskProxyUrl, parseProxyUrl } from "@/lib/proxy";

// 账号原代理不在代理池时（旧数据里无法迁移的地址）的占位值：保存时不提交 proxy_id。
export const KEEP_PROXY = "__keep__";

export function proxyOptionLabel(p) {
  const parsed = parseProxyUrl(p.url);
  // 名称里已带地址（如迁移来的条目）就不再重复
  const address = parsed?.address && !p.name.includes(parsed.address) ? parsed.address : "";
  const extra = [address, p.location].filter(Boolean).join(" · ");
  return extra ? `${p.name}（${extra}）` : p.name;
}

// 账号代理只能从代理池里选；value 为代理池条目 id，空串表示不使用代理。
export function ProxySelect({ id, value, onChange, proxies, disabled, legacyUrl = "" }) {
  const known = !value || value === KEEP_PROXY || proxies.some((p) => p.id === value);
  return (
    <select
      id={id}
      className="h-9 w-full min-w-0 rounded-md border bg-background px-2 text-sm"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">不使用代理（直连）</option>
      {value === KEEP_PROXY && (
        <option value={KEEP_PROXY}>保持原代理（不在代理池：{maskProxyUrl(legacyUrl)}）</option>
      )}
      {!known && <option value={value}>已不在代理池的代理（{value}）</option>}
      {proxies.map((p) => (
        <option key={p.id} value={p.id}>{proxyOptionLabel(p)}</option>
      ))}
    </select>
  );
}
