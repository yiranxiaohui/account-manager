import { CircleAlert, CircleCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { flagEmoji, latencyClass, relTime } from "@/lib/proxy";

// 一次出口探测的结果：成功显示「国旗 地区 · IP · 延迟」，失败显示原因。
export function ProbeResult({ result, compact = false, className }) {
  if (!result) return null;
  if (!result.ok) {
    return (
      <span className={cn("flex min-w-0 items-center gap-1.5 text-red-400", className)}
        title={result.error || "连接失败"}>
        <CircleAlert className="size-3.5 shrink-0" />
        <span className="truncate">{compact ? "不可用" : `不可用：${result.error || "连接失败"}`}</span>
      </span>
    );
  }
  // ipinfo 的 country 只是两位代码（已用国旗表示），此时只显示城市
  const country = result.country && result.country !== result.country_code ? result.country : "";
  const place = [country, result.city || result.region].filter(Boolean)
    .filter((v, i, a) => a.indexOf(v) === i).join(" · ");
  const tip = [
    result.ip && `出口 IP：${result.ip}`,
    place && `地区：${place}`,
    result.timezone && `时区：${result.timezone}`,
    result.org && `网络：${result.org}`,
    result.tested_at && `测试于 ${new Date(result.tested_at).toLocaleString()}（${relTime(result.tested_at)}）`,
  ].filter(Boolean).join("\n");
  return (
    <span className={cn("flex min-w-0 items-center gap-1.5", className)} title={tip}>
      <CircleCheck className="size-3.5 shrink-0 text-emerald-400" />
      {flagEmoji(result.country_code) && <span className="shrink-0">{flagEmoji(result.country_code)}</span>}
      <span className="truncate">
        {place || result.country_code || "未知地区"}
        <span className="text-muted-foreground"> · </span>
        <span className="font-mono">{result.ip}</span>
      </span>
      <span className={cn("shrink-0 tabular-nums", latencyClass(result.latency_ms))}>
        {result.latency_ms} ms
      </span>
    </span>
  );
}
