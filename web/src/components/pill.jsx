import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { TONE } from "@/lib/tones";

// 圆角状态徽章：tone 取 TONE 的语义色，dot 显示前置圆点（pulse 时闪烁）。
export function Pill({ tone = "muted", dot = false, pulse = false, className, children, ...props }) {
  return (
    <Badge className={cn("border-transparent font-medium", TONE[tone] || TONE.muted, className)} {...props}>
      {dot && (
        <span className={cn("size-1.5 shrink-0 rounded-full bg-current", pulse && "animate-pulse")} />
      )}
      {children}
    </Badge>
  );
}
