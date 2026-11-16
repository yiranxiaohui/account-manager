import { Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

// 吸底保存栏：长表单滚到哪都能直接保存。
export function SaveBar({ saving, label = "保存设置", hint = "修改后点击保存生效", type = "submit", onClick, className }) {
  return (
    <div className={cn("sticky bottom-4 z-20 mt-6 flex justify-end", className)}>
      <div className="flex items-center gap-3 rounded-xl bg-popover/85 py-2 pr-2 pl-4 shadow-lg ring-1 ring-foreground/10 backdrop-blur-md">
        <span className="hidden text-xs text-muted-foreground sm:inline">{hint}</span>
        <Button type={type} onClick={onClick} disabled={saving}>
          {saving ? <Spinner /> : <Save />}
          {saving ? "保存中…" : label}
        </Button>
      </div>
    </div>
  );
}
