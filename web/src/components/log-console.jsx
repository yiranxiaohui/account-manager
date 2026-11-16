import { forwardRef } from "react";
import { cn } from "@/lib/utils";

// 终端风格日志面板：顶栏（窗口圆点 + 标题 + 右侧插槽）+ 等宽滚动区域。
export const LogConsole = forwardRef(function LogConsole(
  { title, right, className, bodyClassName, children },
  ref,
) {
  return (
    <div
      className={cn(
        "flex min-h-0 flex-col overflow-hidden rounded-xl bg-terminal text-terminal-foreground ring-1 ring-black/5 dark:ring-white/8",
        className,
      )}
    >
      <div className="flex h-9 shrink-0 items-center gap-3 border-b border-white/8 px-3.5">
        <span className="flex gap-1.5" aria-hidden="true">
          <span className="size-2.5 rounded-full bg-[#ff5f57]/80" />
          <span className="size-2.5 rounded-full bg-[#febc2e]/80" />
          <span className="size-2.5 rounded-full bg-[#28c840]/80" />
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-white/55">{title}</span>
        {right}
      </div>
      <div ref={ref} className={cn("log-panel min-h-0 flex-1 overflow-auto p-3.5", bodyClassName)}>
        {children}
      </div>
    </div>
  );
});
