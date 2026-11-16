import { m } from "motion/react";
import { cn } from "@/lib/utils";
import { TONE } from "@/lib/tones";

// 指标卡片：图标 + 数值 + 标签；传 onClick 时可点击（如作为筛选快捷入口），active 表示当前选中。
export function StatCard({ label, value, icon: Icon, tone = "primary", hint, onClick, active, index = 0 }) {
  const Comp = onClick ? m.button : m.div;
  return (
    <Comp
      type={onClick ? "button" : undefined}
      onClick={onClick}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, delay: index * 0.04, ease: "easeOut" }}
      className={cn(
        "group/stat relative flex min-w-0 items-center gap-3 overflow-hidden rounded-xl bg-card p-3.5 text-left ring-1 ring-foreground/10 transition-[box-shadow,background-color]",
        onClick && "cursor-pointer outline-none hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring",
        active && "ring-2 ring-primary/60",
      )}
    >
      {Icon && (
        <span className={cn("inline-flex size-9 shrink-0 items-center justify-center rounded-lg", TONE[tone])}>
          <Icon className="size-4.5" />
        </span>
      )}
      <span className="min-w-0">
        <span className="block truncate text-xs text-muted-foreground">{label}</span>
        <span className="mt-0.5 flex items-baseline gap-1.5">
          <span className="text-xl leading-none font-semibold tabular-nums">{value}</span>
          {hint && <span className="truncate text-xs text-muted-foreground">{hint}</span>}
        </span>
      </span>
    </Comp>
  );
}

export function StatGrid({ className, children }) {
  return <div className={cn("grid grid-cols-2 gap-3 lg:grid-cols-4", className)}>{children}</div>;
}
