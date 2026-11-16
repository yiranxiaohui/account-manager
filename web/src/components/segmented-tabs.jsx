import { cn } from "@/lib/utils";

// 分段筛选：items 为 [key, 标签, 计数?]
export function SegmentedTabs({ value, onChange, items, className, label = "筛选" }) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("inline-flex h-8 max-w-full items-center overflow-x-auto rounded-lg bg-muted p-[3px] no-scrollbar", className)}
    >
      {items.map(([key, text, count]) => {
        const active = value === key;
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(key)}
            className={cn(
              "inline-flex h-full shrink-0 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium whitespace-nowrap transition-all outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
              active
                ? "bg-background text-foreground shadow-sm dark:bg-input/40 dark:ring-1 dark:ring-input"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {text}
            {count != null && (
              <span className={cn("tabular-nums", active ? "text-muted-foreground" : "opacity-60")}>{count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
