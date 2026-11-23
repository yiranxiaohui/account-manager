import { History, TriangleAlert, Unplug } from "lucide-react";
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

// 代理池下拉框：value 为代理池条目 id，空串表示「不使用代理」（或 noneLabel 指定的默认行为）。
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
  const pool = proxies.map((p) => ({ value: p.id, label: p.name, hint: proxyOptionHint(p) }));
  const options = pool.length
    ? [...special, { separator: true }, { group: `代理池（${pool.length}）`, options: pool }]
    : special;
  return (
    <OptionSelect
      id={id}
      aria-label={ariaLabel}
      value={value}
      disabled={disabled}
      options={options}
      onValueChange={onChange}
    />
  );
}
