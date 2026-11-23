import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// 全站统一的下拉选择框：基于 shadcn/ui Select，选项用数组描述。
// options 元素：
//   { value, label, hint?, icon?, disabled?, textValue? }  普通选项（hint 只在弹层里显示）
//   { separator: true }                                      分隔线
//   { group: "标题", options: [...] }                         分组
// prefix 是显示在触发器左侧的图标（如筛选条里的漏斗）。
// Radix Select 不允许空字符串作为选项值，而表单里常用 "" 表示「默认 / 不使用」，这里统一映射。
const EMPTY = "__option-select-empty__";
const toInner = (value) => (value === "" || value == null ? EMPTY : String(value));

function optionValues(options, out = new Set()) {
  for (const o of options) {
    if (o.options) optionValues(o.options, out);
    else if (!o.separator) out.add(toInner(o.value));
  }
  return out;
}

function renderOptions(options, keyPrefix = "") {
  return options.map((o, i) => {
    const key = `${keyPrefix}${i}`;
    if (o.separator) return <SelectSeparator key={`sep-${key}`} />;
    if (o.options) {
      return (
        <SelectGroup key={`group-${key}`}>
          {o.group ? <SelectLabel>{o.group}</SelectLabel> : null}
          {renderOptions(o.options, `${key}-`)}
        </SelectGroup>
      );
    }
    const textValue = o.textValue ?? (typeof o.label === "string" ? o.label : undefined);
    return (
      <SelectItem key={toInner(o.value)} value={toInner(o.value)} disabled={o.disabled}
        textValue={textValue} hint={o.hint}>
        {o.icon ? <span className="flex shrink-0 items-center text-muted-foreground">{o.icon}</span> : null}
        <span className="truncate">{o.label}</span>
      </SelectItem>
    );
  });
}

export function OptionSelect({
  id,
  value,
  onValueChange,
  options,
  placeholder = "请选择",
  disabled,
  size,
  className,
  contentClassName,
  align,
  prefix,
  "aria-label": ariaLabel,
}) {
  // 当前值不在选项里（含空值但没有「空」选项）时传 ""，让触发器显示占位文字而不是一片空白
  const inner = optionValues(options).has(toInner(value)) ? toInner(value) : "";
  return (
    <Select
      value={inner}
      disabled={disabled}
      onValueChange={(v) => onValueChange?.(v === EMPTY ? "" : v)}
    >
      <SelectTrigger id={id} size={size} aria-label={ariaLabel} className={cn("w-full", className)}>
        {prefix ? (
          <span className="flex min-w-0 flex-1 items-center gap-1.5">
            <span className="flex shrink-0 items-center text-muted-foreground">{prefix}</span>
            <SelectValue placeholder={placeholder} />
          </span>
        ) : (
          <SelectValue placeholder={placeholder} />
        )}
      </SelectTrigger>
      <SelectContent align={align} className={contentClassName}>
        {renderOptions(options)}
      </SelectContent>
    </Select>
  );
}
