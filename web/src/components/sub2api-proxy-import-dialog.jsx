import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { CircleAlert, Download, KeyRound, RefreshCw, Search, TriangleAlert, Users } from "lucide-react";
import { api } from "../api.js";
import { cn } from "@/lib/utils";
import { flagEmoji, latencyClass, protocolLabel } from "@/lib/proxy";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Spinner } from "@/components/ui/spinner";
import { Callout } from "@/components/callout";
import { Pill } from "@/components/pill";
import { SegmentedTabs } from "@/components/segmented-tabs";
import { TipButton } from "@/components/tip-button";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const FILTERS = [
  ["new", "可导入"],
  ["imported", "已导入"],
  ["all", "全部"],
];

const RESULT = {
  created: ["新增", "text-success"],
  updated: ["已更新", "text-info"],
  unchanged: ["无变化", "text-muted-foreground"],
  skipped: ["跳过", "text-warning"],
  failed: ["失败", "text-destructive"],
  missing: ["不存在", "text-destructive"],
};

// 可勾选：可导入，且（未导入 / 已导入但地址有更新），且不与手动条目重复
function selectable(p) {
  return p.importable && !p.duplicate_of && (!p.local || p.local.outdated);
}

function stateBadge(p) {
  if (p.local) {
    return p.local.outdated
      ? ["有更新", "warning", "sub2api 中的地址或凭据已变化，导入即可更新本地条目"]
      : ["已导入", "success", `本地条目：${p.local.name}`];
  }
  if (!p.importable) return [p.expired ? "已过期" : "不可导入", "danger", p.reason];
  if (p.duplicate_of) return ["地址已存在", "muted", `代理池已有相同地址：${p.duplicate_of}`];
  return ["可导入", "info", ""];
}

function matches(p, filter, q) {
  if (filter === "new" && !selectable(p)) return false;
  if (filter === "imported" && !p.local) return false;
  if (!q) return true;
  const hay = `${p.name} ${p.host}:${p.port} ${p.location} ${p.protocol} #${p.id}`.toLowerCase();
  return hay.includes(q);
}

export function Sub2apiProxyImportDialog({ open, onOpenChange, onImported, navigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("new");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);

  async function load({ keepReport = false } = {}) {
    setLoading(true);
    setError("");
    if (!keepReport) setReport(null);
    try {
      const res = await api.sub2apiProxies();
      setData(res);
      setSelected((prev) => new Set([...prev].filter((id) =>
        res.proxies.some((p) => p.id === id && selectable(p)))));
    } catch (e) {
      if (e?.status === 401) return;
      setError(e.body?.detail || `加载失败（${e.status || "?"}）`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    setSelected(new Set());
    setQuery("");
    setFilter("new");
    load();
  }, [open]);

  const proxies = data?.proxies || [];
  const q = query.trim().toLowerCase();
  const visible = useMemo(() => proxies.filter((p) => matches(p, filter, q)), [proxies, filter, q]);
  const pickable = visible.filter(selectable);
  const allPicked = pickable.length > 0 && pickable.every((p) => selected.has(p.id));
  const counts = useMemo(() => ({
    total: proxies.length,
    fresh: proxies.filter(selectable).length,
    imported: proxies.filter((p) => p.local).length,
  }), [proxies]);

  const toggle = (id) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const toggleAll = () => setSelected((prev) => {
    const next = new Set(prev);
    pickable.forEach((p) => (allPicked ? next.delete(p.id) : next.add(p.id)));
    return next;
  });

  async function doImport() {
    const ids = proxies.filter((p) => selected.has(p.id) && selectable(p)).map((p) => p.id);
    if (!ids.length) return toast.error("请先勾选要导入的代理");
    setBusy(true);
    try {
      const res = await api.sub2apiProxiesImport(ids);
      setReport(res);
      setSelected(new Set());
      const s = res.summary;
      const parts = [s.created && `新增 ${s.created}`, s.updated && `更新 ${s.updated}`,
        s.skipped && `跳过 ${s.skipped}`, (s.failed + s.missing) && `失败 ${s.failed + s.missing}`]
        .filter(Boolean).join("，");
      if (s.created + s.updated > 0) toast.success(`导入完成：${parts}`);
      else toast.error(`没有导入任何代理${parts ? `（${parts}）` : ""}`);
      onImported?.();
      await load({ keepReport: true });
    } catch (e) {
      if (e?.status !== 401) toast.error(e.body?.detail || `导入失败（${e.status || "?"}）`);
    } finally {
      setBusy(false);
    }
  }

  const picked = proxies.filter((p) => selected.has(p.id) && selectable(p)).length;
  return (
    <AlertDialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <AlertDialogContent className="flex max-h-[min(90vh,52rem)] flex-col sm:max-w-4xl">
        <AlertDialogHeader className="place-items-start text-left">
          <AlertDialogTitle className="flex items-center gap-2">
            <Download className="size-4 text-primary" /> 从 sub2api 导入代理
          </AlertDialogTitle>
          <AlertDialogDescription>
            读取 sub2api 中启用的代理，导入后可在注册任务、sessionKey 导入和手动登录里选择。
            代理密码只在服务端组装地址，不会经过浏览器。
            {data?.base_url ? (
              <code className="ml-1 rounded bg-muted px-1 py-0.5 font-mono text-xs">{data.base_url}</code>
            ) : null}
          </AlertDialogDescription>
        </AlertDialogHeader>

        {error && (
          <Callout
            tone="danger"
            icon={CircleAlert}
            actions={/未配置/.test(error) && navigate && (
              <Button variant="outline" size="sm" onClick={() => { onOpenChange(false); navigate("settings"); }}>
                去设置
              </Button>
            )}
          >
            <span className="text-destructive">{error}</span>
          </Callout>
        )}
        {data && !data.credentials_visible && (
          <Callout tone="warning" icon={TriangleAlert}>
            当前配置的 sub2api Key 读不到代理的用户名和密码，无法导入。请在「系统设置 → sub2api 对接」
            改用完整的管理员 API Key。
          </Callout>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <InputGroup className="min-w-48 flex-1">
            <InputGroupAddon><Search /></InputGroupAddon>
            <InputGroupInput placeholder="搜索名称、主机、地区…" value={query}
              onChange={(e) => setQuery(e.target.value)} />
          </InputGroup>
          <SegmentedTabs
            value={filter}
            onChange={setFilter}
            items={FILTERS.map(([key, label]) => [
              key,
              label,
              key === "new" ? counts.fresh : key === "imported" ? counts.imported : counts.total,
            ])}
          />
          <TipButton variant="outline" size="icon" onClick={() => load()} disabled={loading || busy} tip="刷新">
            <RefreshCw className={cn(loading && "animate-spin")} />
          </TipButton>
        </div>

        <div className="min-h-40 flex-1 overflow-auto rounded-lg ring-1 ring-foreground/10">
          <div className="sticky top-0 z-10 grid h-9 grid-cols-[1.5rem_minmax(0,1.6fr)_minmax(0,1.4fr)_minmax(0,1.1fr)_5.5rem] items-center gap-3 border-b bg-muted/80 px-3 text-xs font-medium text-muted-foreground backdrop-blur max-sm:grid-cols-[1.5rem_minmax(0,1fr)_5.5rem]">
            <Checkbox aria-label="全选可导入"
              checked={allPicked ? true : pickable.some((p) => selected.has(p.id)) ? "indeterminate" : false}
              disabled={!pickable.length || busy} onCheckedChange={toggleAll} />
            <span>名称</span>
            <span className="max-sm:hidden">地址</span>
            <span className="max-sm:hidden">地区 / 延迟</span>
            <span className="text-right">状态</span>
          </div>
          {loading && !data ? (
            <div className="flex items-center justify-center gap-2 p-10 text-sm text-muted-foreground">
              <Spinner /> 正在读取 sub2api 代理…
            </div>
          ) : data && visible.length === 0 ? (
            <div className="p-10 text-center text-sm text-muted-foreground">
              {proxies.length === 0 ? "sub2api 中没有启用的代理"
                : filter === "new" ? "没有可导入的新代理，可切到「全部」查看" : "没有符合条件的代理"}
            </div>
          ) : (
            <ul className="divide-y">
              {visible.map((p) => {
                const [label, tone, tip] = stateBadge(p);
                const can = selectable(p);
                return (
                  <li key={p.id}>
                    <label className={cn(
                      "grid grid-cols-[1.5rem_minmax(0,1.6fr)_minmax(0,1.4fr)_minmax(0,1.1fr)_5.5rem] items-center gap-3 px-3 py-2.5 text-sm max-sm:grid-cols-[1.5rem_minmax(0,1fr)_5.5rem]",
                      can ? "cursor-pointer hover:bg-muted/50" : "opacity-60",
                      selected.has(p.id) && "bg-primary/5",
                    )}>
                      <Checkbox aria-label={`选择 ${p.name}`}
                        checked={selected.has(p.id)} disabled={!can || busy} onCheckedChange={() => toggle(p.id)} />
                      <span className="min-w-0">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate font-medium" title={p.name}>{p.name}</span>
                          <span className="shrink-0 text-xs text-muted-foreground">#{p.id}</span>
                        </span>
                        <span className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                          {p.account_count != null && (
                            <span className="flex items-center gap-1" title="sub2api 中使用此代理的账号数">
                              <Users className="size-3" /> {p.account_count}
                            </span>
                          )}
                          <span className="truncate font-mono sm:hidden">{p.host}:{p.port}</span>
                        </span>
                      </span>
                      <span className="flex min-w-0 items-center gap-1.5 max-sm:hidden">
                        <Badge variant="secondary" className="shrink-0 rounded-md font-mono text-[10px]">
                          {protocolLabel(p.protocol)}
                        </Badge>
                        <span className="truncate font-mono text-xs" title={`${p.host}:${p.port}`}>
                          {p.host}:{p.port}
                        </span>
                        {p.has_auth && (
                          <span className="shrink-0 text-muted-foreground" title="需要认证">
                            <KeyRound className="size-3" />
                          </span>
                        )}
                      </span>
                      <span className="flex min-w-0 items-center gap-1.5 text-xs max-sm:hidden">
                        {flagEmoji(p.country_code) && <span>{flagEmoji(p.country_code)}</span>}
                        <span className="truncate">{p.location || "—"}</span>
                        {p.latency_ms != null ? (
                          <span className={cn("shrink-0 tabular-nums", latencyClass(p.latency_ms))}>
                            {p.latency_ms} ms
                          </span>
                        ) : p.latency_status === "failed" ? (
                          <span className="shrink-0 text-destructive">检测失败</span>
                        ) : null}
                      </span>
                      <span className="flex justify-end">
                        <Pill tone={tone} title={tip}>{label}</Pill>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {report && report.results.length > 0 && (
          <div className="max-h-32 overflow-auto rounded-lg bg-muted/40 p-2.5 text-xs ring-1 ring-foreground/10">
            {report.results.map((r) => {
              const [label, cls] = RESULT[r.result] || [r.result, ""];
              return (
                <div key={`${r.id}-${r.result}`} className="flex gap-2 py-0.5">
                  <span className="w-12 shrink-0 text-muted-foreground">#{r.id}</span>
                  <span className={cn("w-12 shrink-0", cls)}>{label}</span>
                  <span className="min-w-0 truncate" title={r.detail || ""}>
                    {r.name}
                    {r.detail ? <span className="text-muted-foreground"> · {r.detail}</span> : null}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        <AlertDialogFooter className="items-center sm:justify-between">
          <span className="text-xs text-muted-foreground">
            {data ? `共 ${counts.total} 个，已导入 ${counts.imported} 个，已选 ${picked} 个` : ""}
          </span>
          <span className="flex flex-col-reverse gap-2 sm:flex-row">
            <AlertDialogCancel disabled={busy}>关闭</AlertDialogCancel>
            <Button onClick={doImport} disabled={busy || !picked}>
              {busy ? <Spinner /> : <Download />}
              {busy ? "导入中…" : `导入所选（${picked}）`}
            </Button>
          </span>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
