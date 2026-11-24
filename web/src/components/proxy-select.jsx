import { useEffect, useState } from "react";
import { History, TriangleAlert, Unplug, Users } from "lucide-react";
import { api } from "@/api.js";
import { cn } from "@/lib/utils";
import { maskProxyUrl, parseProxyUrl } from "@/lib/proxy";
import { OptionSelect } from "@/components/option-select";

// 账号原代理不在代理池时（旧数据里无法迁移的地址）的占位值：保存时不提交 proxy_id。
export const KEEP_PROXY = "__keep__";

// 下拉项右侧的说明：地址 + 位置（名称里已带地址的条目，如迁移来的，就不再重复地址）
export function proxyOptionHint(p) {
  const parsed = parseProxyUrl(p.url);
  const address = parsed?.address && !p.name.includes(parsed.address) ? parsed.address : "";
  return [address, p.location].filter(Boolean).join(" · ");
}

// 各代理池条目绑定的账号数 {条目 id: n}：所有下拉框共用一份，挂载和每次展开时刷新，
// 同一时刻只发一个请求；加载失败时保留上次结果（首次失败则不显示账号数）。
let usageCache = null;
let usageInflight = null;
const usageListeners = new Set();

function refreshProxyUsage() {
  if (!usageInflight) {
    usageInflight = api
      .proxyUsage()
      .then((r) => {
        usageCache = r?.usage || {};
        for (const listener of usageListeners) listener(usageCache);
      })
      .catch(() => {})
      .finally(() => {
        usageInflight = null;
      });
  }
  return usageInflight;
}

export function useProxyUsage() {
  const [usage, setUsage] = useState(usageCache);
  useEffect(() => {
    usageListeners.add(setUsage);
    refreshProxyUsage();
    return () => {
      usageListeners.delete(setUsage);
    };
  }, []);
  return [usage, refreshProxyUsage];
}

function UsageCount({ count }) {
  const label = count ? `${count} 个账号绑定该代理` : "暂无账号绑定该代理";
  return (
    <span className={cn("flex items-center gap-1", !count && "opacity-50")} title={label}>
      <Users className="size-3.5" aria-hidden="true" />
      <span aria-hidden="true">{count}</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}

// 代理池下拉框：value 为代理池条目 id，空串表示「不使用代理」（或 noneLabel 指定的默认行为）。
// 弹层里每个代理池条目右侧显示当前绑定它的账号数。
export function ProxySelect({
  id,
  value,
  onChange,
  proxies,
  disabled,
  legacyUrl = "",
  noneLabel = "不使用代理",
  noneHint = "直连",
  "aria-label": ariaLabel,
}) {
  const [usage, refreshUsage] = useProxyUsage();
  const known = !value || value === KEEP_PROXY || proxies.some((p) => p.id === value);
  const special = [
    { value: "", label: noneLabel, hint: noneHint, icon: <Unplug /> },
    value === KEEP_PROXY && {
      value: KEEP_PROXY,
      label: `保持原代理（${maskProxyUrl(legacyUrl)}）`,
      hint: "不在代理池",
      icon: <History />,
    },
    !known && { value, label: `已不在代理池的代理（${value}）`, icon: <TriangleAlert /> },
  ].filter(Boolean);
  const pool = proxies.map((p) => ({
    value: p.id,
    label: p.name,
    hint: proxyOptionHint(p),
    meta: usage ? <UsageCount count={usage[p.id] || 0} /> : undefined,
  }));
  // 分组标题右侧给账号数一列加表头（pr-6 + 标题自身 px-2 与选项的 pr-8 对齐）
  const group = usage ? (
    <span className="flex items-center justify-between gap-4 pr-6">
      <span>代理池（{pool.length}）</span>
      <span>绑定账号</span>
    </span>
  ) : `代理池（${pool.length}）`;
  const options = pool.length
    ? [...special, { separator: true }, { group, options: pool }]
    : special;
  return (
    <OptionSelect
      id={id}
      aria-label={ariaLabel}
      value={value}
      disabled={disabled}
      options={options}
      onValueChange={onChange}
      onOpenChange={(open) => {
        if (open) refreshUsage();
      }}
    />
  );
}
