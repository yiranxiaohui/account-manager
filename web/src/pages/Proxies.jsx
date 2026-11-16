import { useEffect, useMemo, useState } from "react";
import { m } from "motion/react";
import { toast } from "sonner";
import {
  CircleCheck,
  CircleX,
  Download,
  FlaskConical,
  Globe,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  SearchX,
  Server,
  Trash2,
  TriangleAlert,
  Users,
  X,
} from "lucide-react";
import { api } from "../api.js";
import { cn } from "@/lib/utils";
import {
  loadTestResults,
  maskProxyUrl,
  parseProxyUrl,
  protocolLabel,
  saveTestResults,
} from "@/lib/proxy";
import { PageHeader } from "@/components/page-header";
import { StatCard, StatGrid } from "@/components/stat-card";
import { Callout } from "@/components/callout";
import { Pill } from "@/components/pill";
import { TipButton } from "@/components/tip-button";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ProxyEditDialog } from "@/components/proxy-edit-dialog";
import { ProbeResult } from "@/components/proxy-probe-result";
import { SegmentedTabs } from "@/components/segmented-tabs";
import { Sub2apiProxyImportDialog } from "@/components/sub2api-proxy-import-dialog";

const TEST_CONCURRENCY = 4;

function normalizeBase(raw) {
  let url = String(raw || "").trim().replace(/\/+$/, "");
  if (url.endsWith("/api/v1")) url = url.slice(0, -"/api/v1".length);
  return url;
}

const isSub2api = (p) => p.source === "sub2api";

// 测试结果对应的地址指纹（不含凭据）；同步或编辑改了协议/主机/端口后旧结果作废
function addrKey(url) {
  const parsed = parseProxyUrl(url);
  return parsed ? `${parsed.scheme}://${parsed.address}` : "";
}

function SourceBadge({ entry, currentBase }) {
  if (!isSub2api(entry)) {
    return <Badge variant="outline" className="font-normal text-muted-foreground">手动</Badge>;
  }
  const foreign = currentBase && entry.source_base_url !== currentBase;
  return (
    <Pill
      tone={foreign ? "muted" : "highlight"}
      title={`来自 ${entry.source_base_url}${foreign ? "（不是当前配置的 sub2api，不参与同步）" : ""}`}
    >
      sub2api #{entry.source_id}
    </Pill>
  );
}

const ROW_GRID = "grid grid-cols-[1rem_minmax(0,1.3fr)_minmax(0,1.3fr)_minmax(0,1.4fr)_6rem] items-center gap-4";

export default function Proxies({ navigate }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sub2apiBase, setSub2apiBase] = useState("");
  const [tests, setTests] = useState(loadTestResults);
  const [testing, setTesting] = useState(() => new Set());
  const [batch, setBatch] = useState(null); // {done,total}
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState(() => new Set());
  const [edit, setEdit] = useState({ open: false, entry: null });
  const [importOpen, setImportOpen] = useState(false);
  const [confirm, setConfirm] = useState(null); // {title, description, onConfirm}
  const [missing, setMissing] = useState(() => new Set());
  const [syncing, setSyncing] = useState(false);
  const [usage, setUsage] = useState({}); // {条目 id: 绑定的账号数}

  async function reload() {
    try {
      const [cfg, used] = await Promise.all([api.getConfig(), api.proxyUsage()]);
      setItems(cfg.saved_proxies || []);
      setSub2apiBase(normalizeBase(cfg.sub2api_base_url));
      setUsage(used.usage || {});
    } catch (e) {
      if (e?.status !== 401) toast.error("代理池加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); }, []);

  // 条目变化后清理已不存在条目的测试结果、选择与失效标记
  useEffect(() => {
    if (loading) return; // 未加载完时 items 为空，不能据此清掉本地保存的测试结果
    const ids = new Set(items.map((p) => p.id));
    saveTestResults(tests, ids);
    setSelected((prev) => new Set([...prev].filter((id) => ids.has(id))));
    setMissing((prev) => new Set([...prev].filter((id) => ids.has(id))));
  }, [items, tests, loading]);

  async function persist(next, okMsg) {
    try {
      const res = await api.putConfig({ saved_proxies: next });
      setItems(res.saved_proxies || []);
      if (okMsg) toast.success(okMsg);
      return true;
    } catch (e) {
      if (e?.status !== 401) toast.error(e.body?.detail || "保存失败，请重试");
      return false;
    }
  }

  // 只认地址未变的测试结果
  const valid = useMemo(() => {
    const out = {};
    for (const p of items) {
      const r = tests[p.id];
      if (r && (!r.addr || r.addr === addrKey(p.url))) out[p.id] = r;
    }
    return out;
  }, [items, tests]);

  const counts = useMemo(() => ({
    all: items.length,
    manual: items.filter((p) => !isSub2api(p)).length,
    sub2api: items.filter(isSub2api).length,
    ok: items.filter((p) => valid[p.id]?.ok).length,
    failed: items.filter((p) => valid[p.id] && !valid[p.id].ok).length,
  }), [items, valid]);

  const q = query.trim().toLowerCase();
  const visible = useMemo(() => items.filter((p) => {
    if (filter === "manual" && isSub2api(p)) return false;
    if (filter === "sub2api" && !isSub2api(p)) return false;
    if (filter === "failed" && !(valid[p.id] && !valid[p.id].ok)) return false;
    if (!q) return true;
    const parsed = parseProxyUrl(p.url);
    const hay = `${p.name} ${parsed?.host || ""}:${parsed?.port || ""} ${p.location || ""} ${valid[p.id]?.ip || ""}`;
    return hay.toLowerCase().includes(q);
  }), [items, filter, q, valid]);

  const selectedVisible = visible.filter((p) => selected.has(p.id));
  const allSelected = visible.length > 0 && visible.every((p) => selected.has(p.id));
  const toggle = (id) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const toggleAll = () => setSelected((prev) => {
    const next = new Set(prev);
    visible.forEach((p) => (allSelected ? next.delete(p.id) : next.add(p.id)));
    return next;
  });

  const setTestingFlag = (id, on) => setTesting((prev) => {
    const next = new Set(prev);
    if (on) next.add(id);
    else next.delete(id);
    return next;
  });

  async function testOne(p, { quiet = false } = {}) {
    setTestingFlag(p.id, true);
    try {
      const res = { ...(await api.proxyTest({ id: p.id })), addr: addrKey(p.url) };
      setTests((prev) => ({ ...prev, [p.id]: res }));
      if (!quiet) {
        if (res.ok) toast.success(`「${p.name}」可用：${res.ip} · ${res.latency_ms} ms`);
        else toast.error(`「${p.name}」不可用：${res.error}`);
      }
      return res.ok;
    } catch (e) {
      if (e?.status !== 401 && !quiet) toast.error(e.body?.detail || "测试失败");
      return false;
    } finally {
      setTestingFlag(p.id, false);
    }
  }

  async function testMany(list) {
    if (!list.length) return;
    const queue = [...list];
    const stat = { done: 0, total: list.length, ok: 0 };
    setBatch({ ...stat });
    const worker = async () => {
      while (queue.length) {
        const p = queue.shift();
        if (await testOne(p, { quiet: true })) stat.ok += 1;
        stat.done += 1;
        setBatch({ ...stat });
      }
    };
    await Promise.all(Array.from({ length: Math.min(TEST_CONCURRENCY, list.length) }, worker));
    setBatch(null);
    const bad = stat.total - stat.ok;
    if (bad) toast.error(`测试完成：可用 ${stat.ok} 个，不可用 ${bad} 个`);
    else toast.success(`测试完成：${stat.ok} 个代理全部可用`);
  }

  async function saveEntry(entry) {
    const exists = items.some((p) => p.id === entry.id);
    const next = exists ? items.map((p) => (p.id === entry.id ? entry : p)) : [...items, entry];
    const ok = await persist(next, exists ? "已更新" : "已添加");
    if (ok) setTests((prev) => { const n = { ...prev }; delete n[entry.id]; return n; });
    return ok;
  }

  function askDelete(list) {
    if (!list.length) return;
    // 仍被账号使用的代理不能删：账号代理只能来自代理池
    const used = list.filter((p) => usage[p.id]);
    const deletable = list.filter((p) => !usage[p.id]);
    const usedText = used.slice(0, 3).map((p) => `「${p.name}」（${usage[p.id]} 个账号）`).join("、")
      + (used.length > 3 ? ` 等 ${used.length} 个代理` : "");
    if (!deletable.length) {
      toast.error(`${usedText}仍被账号使用，请先在账号管理中为这些账号更换代理再删除`, {
        duration: 8000,
      });
      return;
    }
    const ids = new Set(deletable.map((p) => p.id));
    setConfirm({
      title: deletable.length === 1 ? `删除代理「${deletable[0].name}」？` : `删除 ${deletable.length} 个代理？`,
      description: (used.length ? `${usedText}仍被账号使用，将保留。` : "")
        + "只从本地代理池移除，sub2api 中的代理不受影响。",
      onConfirm: () => persist(items.filter((p) => !ids.has(p.id)), `已删除 ${deletable.length} 个代理`),
    });
  }

  async function sync() {
    setSyncing(true);
    try {
      const res = await api.sub2apiProxiesSync();
      const s = res.summary;
      const gone = res.results.filter((r) => r.result === "missing" && r.local_id).map((r) => r.local_id);
      setMissing(new Set(gone));
      await reload();
      const parts = [
        `更新 ${s.updated}`,
        `无变化 ${s.unchanged}`,
        s.failed && `失败 ${s.failed}`,
        s.missing && `上游已移除 ${s.missing}`,
      ].filter(Boolean).join("，");
      (s.failed || s.missing ? toast.warning : toast.success)(`同步完成：${parts}`);
      const failed = res.results.find((r) => r.result === "failed");
      if (failed) toast.error(`「${failed.name}」${failed.detail}`, { duration: 8000 });
    } catch (e) {
      if (e?.status !== 401) toast.error(e.body?.detail || "同步失败");
    } finally {
      setSyncing(false);
    }
  }

  const busy = !!batch || syncing;
  const missingItems = items.filter((p) => missing.has(p.id));
  const tabs = [
    ["all", "全部", counts.all],
    ["manual", "手动", counts.manual],
    ["sub2api", "sub2api", counts.sub2api],
    ...(counts.failed ? [["failed", "不可用", counts.failed]] : []),
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Globe}
        title="代理池"
        description="保存常用代理，供注册任务、sessionKey 导入、手动登录与账号绑定选择；也可以直接导入 sub2api 里的代理。账号只能使用这里的代理：修改地址后已绑定的账号会跟着使用新地址，仍被账号使用的代理不能删除。"
        actions={
          <>
            {counts.sub2api > 0 && (
              <TipButton variant="outline" onClick={sync} disabled={busy}
                tip="按 sub2api 当前数据更新已导入代理的地址与凭据">
                <RefreshCw className={cn(syncing && "animate-spin")} /> 同步 sub2api
              </TipButton>
            )}
            <Button variant="outline" onClick={() => setImportOpen(true)} disabled={busy}>
              <Download /> 从 sub2api 导入
            </Button>
            <Button onClick={() => setEdit({ open: true, entry: null })} disabled={busy}>
              <Plus /> 添加代理
            </Button>
          </>
        }
      />

      <StatGrid>
        <StatCard index={0} label="代理总数" value={counts.all} icon={Server} tone="primary" />
        <StatCard index={1} label="来自 sub2api" value={counts.sub2api} icon={Download} tone="highlight" />
        <StatCard index={2} label="测试可用" value={counts.ok} icon={CircleCheck} tone="success" />
        <StatCard index={3} label="测试不可用" value={counts.failed} icon={CircleX}
          tone={counts.failed ? "danger" : "muted"}
          onClick={counts.failed ? () => setFilter("failed") : undefined} active={filter === "failed"} />
      </StatGrid>

      {missingItems.length > 0 && (
        <Callout
          tone="warning"
          icon={TriangleAlert}
          title={`${missingItems.length} 个代理在 sub2api 中已删除或停用`}
          actions={
            <>
              <Button variant="outline" size="sm" onClick={() => setMissing(new Set())}>忽略</Button>
              <Button variant="destructive" size="sm" onClick={() => askDelete(missingItems)}>
                <Trash2 /> 删除这些
              </Button>
            </>
          }
        >
          {missingItems.slice(0, 3).map((p) => p.name).join("、")}
          {missingItems.length > 3 ? " 等" : ""}
        </Callout>
      )}

      <Card className="gap-0 py-0">
        {items.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b p-3">
            <InputGroup className="min-w-56 flex-1">
              <InputGroupAddon><Search /></InputGroupAddon>
              <InputGroupInput placeholder="搜索名称、主机、地区、出口 IP…" value={query}
                onChange={(e) => setQuery(e.target.value)} />
              {query && (
                <InputGroupAddon align="inline-end">
                  <InputGroupButton size="icon-xs" aria-label="清除搜索" onClick={() => setQuery("")}>
                    <X />
                  </InputGroupButton>
                </InputGroupAddon>
              )}
            </InputGroup>
            <SegmentedTabs value={filter} onChange={setFilter} items={tabs} />
            {selectedVisible.length > 0 ? (
              <>
                <Button variant="outline" disabled={busy} onClick={() => testMany(selectedVisible)}>
                  {batch ? <Spinner /> : <FlaskConical />}
                  {batch ? `测试中 ${batch.done}/${batch.total}` : `测试所选（${selectedVisible.length}）`}
                </Button>
                <Button variant="destructive" disabled={busy} onClick={() => askDelete(selectedVisible)}>
                  <Trash2 /> 删除所选
                </Button>
              </>
            ) : (
              <Button variant="outline" disabled={busy || !visible.length} onClick={() => testMany(visible)}>
                {batch ? <Spinner /> : <FlaskConical />}
                {batch ? `测试中 ${batch.done}/${batch.total}` : "全部测试"}
              </Button>
            )}
          </div>
        )}

        {loading ? (
          <div className="divide-y">
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex items-center gap-4 px-4 py-3.5">
                <Skeleton className="size-4" />
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-4 w-48" />
                <Skeleton className="h-4 flex-1" />
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <Empty className="py-14">
            <EmptyHeader>
              <EmptyMedia variant="icon"><Globe /></EmptyMedia>
              <EmptyTitle>代理池还是空的</EmptyTitle>
              <EmptyDescription>手动添加代理，或一键导入 sub2api 中已有的代理。</EmptyDescription>
            </EmptyHeader>
            <EmptyContent className="flex-row justify-center">
              <Button variant="outline" onClick={() => setImportOpen(true)}>
                <Download /> 从 sub2api 导入
              </Button>
              <Button onClick={() => setEdit({ open: true, entry: null })}>
                <Plus /> 添加代理
              </Button>
            </EmptyContent>
          </Empty>
        ) : visible.length === 0 ? (
          <Empty className="py-14">
            <EmptyHeader>
              <EmptyMedia variant="icon"><SearchX /></EmptyMedia>
              <EmptyTitle>没有符合条件的代理</EmptyTitle>
            </EmptyHeader>
            <EmptyContent>
              <Button variant="outline" onClick={() => { setQuery(""); setFilter("all"); }}>清除筛选</Button>
            </EmptyContent>
          </Empty>
        ) : (
          <>
            <div className={cn(ROW_GRID, "h-10 border-b bg-muted/30 px-4 text-xs font-medium text-muted-foreground max-md:hidden")}>
              <Checkbox aria-label="全选"
                checked={allSelected ? true : selectedVisible.length > 0 ? "indeterminate" : false}
                disabled={busy} onCheckedChange={toggleAll} />
              <span>名称</span>
              <span>地址</span>
              <span>出口</span>
              <span className="text-right">操作</span>
            </div>
            <ul className="divide-y">
              {visible.map((p, i) => {
                const parsed = parseProxyUrl(p.url);
                const result = valid[p.id];
                const isTesting = testing.has(p.id);
                return (
                  <m.li
                    key={p.id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.2, delay: Math.min(i, 12) * 0.015 }}
                    className={cn(
                      ROW_GRID,
                      "px-4 py-3 text-sm transition-colors hover:bg-muted/40",
                      "max-md:grid-cols-[1rem_minmax(0,1fr)_auto] max-md:gap-x-3 max-md:gap-y-1.5",
                      selected.has(p.id) && "bg-primary/5",
                      missing.has(p.id) && "bg-warning/6",
                    )}>
                    <Checkbox className="max-md:row-span-3 max-md:mt-0.5 max-md:self-start"
                      aria-label={`选择 ${p.name}`} checked={selected.has(p.id)} disabled={busy}
                      onCheckedChange={() => toggle(p.id)} />
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate font-medium" title={p.name}>{p.name}</span>
                        <SourceBadge entry={p} currentBase={sub2apiBase} />
                        {usage[p.id] ? (
                          <Badge variant="outline" className="shrink-0 font-normal text-muted-foreground"
                            title="绑定该代理的账号数；仍被使用时不能删除">
                            <Users /> {usage[p.id]}
                          </Badge>
                        ) : null}
                        {missing.has(p.id) && <Pill tone="warning">上游已移除</Pill>}
                      </span>
                      {p.location ? (
                        <span className="mt-0.5 block truncate text-xs text-muted-foreground">{p.location}</span>
                      ) : null}
                    </span>
                    <span className="flex min-w-0 items-center gap-1.5 max-md:col-start-2 max-md:row-start-2">
                      <Badge variant="secondary" className="shrink-0 rounded-md font-mono text-[10px]">
                        {protocolLabel(parsed?.scheme)}
                      </Badge>
                      <code className="truncate text-xs text-muted-foreground" title={maskProxyUrl(p.url)}>
                        {parsed ? parsed.address : maskProxyUrl(p.url)}
                      </code>
                      {parsed?.hasAuth && (
                        <span className="shrink-0 text-muted-foreground" title="带认证">
                          <KeyRound className="size-3" />
                        </span>
                      )}
                    </span>
                    <span className="min-w-0 text-xs max-md:col-start-2 max-md:row-start-3">
                      {isTesting ? (
                        <span className="flex items-center gap-1.5 text-muted-foreground">
                          <Spinner className="size-3.5" /> 测试中…
                        </span>
                      ) : result ? (
                        <ProbeResult result={result} compact />
                      ) : (
                        <span className="text-muted-foreground/70">未测试</span>
                      )}
                    </span>
                    <span className="flex justify-end gap-0.5 max-md:col-start-3 max-md:row-start-1">
                      <TipButton variant="ghost" size="icon-sm" tip="测试出口"
                        disabled={isTesting || busy} onClick={() => testOne(p)}>
                        <FlaskConical />
                      </TipButton>
                      <TipButton variant="ghost" size="icon-sm" tip="编辑"
                        disabled={busy} onClick={() => setEdit({ open: true, entry: p })}>
                        <Pencil />
                      </TipButton>
                      <TipButton variant="ghost" size="icon-sm" tip="删除"
                        className="text-destructive hover:text-destructive" disabled={busy}
                        onClick={() => askDelete([p])}>
                        <Trash2 />
                      </TipButton>
                    </span>
                  </m.li>
                );
              })}
            </ul>
          </>
        )}
      </Card>

      <ProxyEditDialog
        open={edit.open}
        entry={edit.entry}
        onOpenChange={(open) => setEdit((s) => ({ ...s, open }))}
        onSave={saveEntry}
      />
      <Sub2apiProxyImportDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        onImported={reload}
        navigate={navigate}
      />
      <AlertDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogMedia className="bg-destructive/10 text-destructive">
              <Trash2 />
            </AlertDialogMedia>
            <AlertDialogTitle>{confirm?.title}</AlertDialogTitle>
            <AlertDialogDescription>{confirm?.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <Button variant="destructive"
              onClick={async () => { const fn = confirm?.onConfirm; setConfirm(null); await fn?.(); }}>
              删除
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
