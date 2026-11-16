import { Pill } from "@/components/pill";

const STATUS = {
  running: { label: "运行中", tone: "info" },
  success: { label: "成功", tone: "success" },
  failed: { label: "失败", tone: "danger" },
  needs_manual: { label: "需人工介入", tone: "warning" },
};

export function statusTone(status) {
  return STATUS[status]?.tone || "muted";
}

export function StatusBadge({ status, className }) {
  const s = STATUS[status] || { label: status || "未知", tone: "muted" };
  return (
    <Pill tone={s.tone} dot pulse={status === "running"} className={className}>
      {s.label}
    </Pill>
  );
}
