import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

// 品牌标识：渐变方块 + 图标
export function BrandMark({ className, iconClassName }) {
  return (
    <span
      className={cn(
        "brand-gradient inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-white shadow-md shadow-primary/30 ring-1 ring-white/15 ring-inset",
        className,
      )}
    >
      <Sparkles className={cn("size-4", iconClassName)} />
    </span>
  );
}
