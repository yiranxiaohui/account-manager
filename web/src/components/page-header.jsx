import { cn } from "@/lib/utils";

// 页面标题区：大标题 + 说明，右侧放页面级操作。
export function PageHeader({ title, description, icon: Icon, actions, className }) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-x-6 gap-y-4", className)}>
      <div className="flex min-w-0 flex-1 basis-80 items-start gap-3.5">
        {Icon && (
          <span className="mt-0.5 hidden size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary ring-1 ring-primary/15 sm:inline-flex">
            <Icon className="size-5" />
          </span>
        )}
        <div className="min-w-0 space-y-1">
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
          {description && (
            <p className="max-w-3xl text-sm text-pretty text-muted-foreground">{description}</p>
          )}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

// 小节标题：卡片外的分组标题
export function SectionTitle({ title, description, actions, className }) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-3", className)}>
      <div className="space-y-0.5">
        <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      {actions}
    </div>
  );
}
