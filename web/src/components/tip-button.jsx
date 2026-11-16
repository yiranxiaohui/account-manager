import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

// 带悬浮提示的按钮（多用于纯图标按钮）。禁用的按钮收不到指针事件，外面包一层 span 保证提示仍可显示。
export function TipButton({ tip, side = "top", children, ...props }) {
  if (!tip) return <Button {...props}>{children}</Button>;
  // 纯图标按钮没有可见文字，用提示文案作为无障碍名称
  const iconOnly = String(props.size || "").startsWith("icon") && typeof tip === "string";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">
          <Button aria-label={iconOnly ? tip : undefined} {...props}>
            {children}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent side={side}>{tip}</TooltipContent>
    </Tooltip>
  );
}
