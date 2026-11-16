import { cn } from "@/lib/utils";
import { TONE_TEXT } from "@/lib/tones";

const BOX = {
  info: "border-info/25 bg-info/8",
  success: "border-success/25 bg-success/8",
  warning: "border-warning/30 bg-warning/8",
  danger: "border-destructive/30 bg-destructive/8",
  primary: "border-primary/25 bg-primary/8",
  muted: "border-border bg-muted/40",
};

// 提示条：语义色边框/底色 + 图标 + 标题/正文 + 右侧操作
export function Callout({ tone = "info", icon: Icon, title, children, actions, className, role }) {
  return (
    <div
      role={role}
      className={cn("flex flex-wrap items-center gap-x-3 gap-y-2.5 rounded-xl border px-4 py-3 text-sm", BOX[tone], className)}
    >
      <div className="flex min-w-0 flex-1 basis-64 items-start gap-3">
        {Icon && <Icon className={cn("mt-0.5 size-4 shrink-0", TONE_TEXT[tone])} />}
        <div className="min-w-0 flex-1 space-y-0.5">
          {title && <div className="font-medium text-foreground">{title}</div>}
          {children && <div className="text-pretty text-muted-foreground">{children}</div>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
