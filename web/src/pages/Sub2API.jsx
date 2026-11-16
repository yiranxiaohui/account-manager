import { useEffect, useMemo, useState } from "react";
import { m } from "motion/react";
import { toast } from "sonner";
import {
  CircleAlert,
  CircleCheck,
  KeyRound,
  Link2,
  RefreshCw,
  SearchX,
  Settings as SettingsIcon,
  Upload,
  UserX,
  Users,
  Wrench,
} from "lucide-react";
import { api } from "../api.js";
import { cn } from "@/lib/utils";
import { fmtDateTime } from "@/lib/time";
import { PageHeader } from "@/components/page-header";
import { StatCard, StatGrid } from "@/components/stat-card";
import { Callout } from "@/components/callout";
import { Pill } from "@/components/pill";
import { SegmentedTabs } from "@/components/segmented-tabs";
import { TipButton } from "@/components/tip-button";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { useOrgPicker } from "@/components/org-select-dialog";
import { OAuthImportDialog } from "@/components/oauth-import-dialog";

const STATUS = {
  active: ["正常", "success"],
  error: ["异常", "danger"],
  disabled: ["已停用", "muted"],
};

const FILTERS = [
  ["error", "需重新授权"],
  ["fixable", "异常且可修复"],
  ["all", "全部"],
  ["unmatched", "本地无对应账号"],
];

const LIVE = { alive: "有效", dead: "失效", blocked: "被拦截", error: "检测失败" };

function canFix(a) {
  return !!a.local?.has_session_key;
}

function matchFilter(a, filter) {
  switch (filter) {
    case "error":
      return a.status === "error";
    case "fixable":
      return a.status === "error" && canFix(a);
    case "unmatched":
      return !a.local;
    default:
      return true;
  }
}

function TokenExpiry({ iso }) {
  if (!iso) return null;
  const expired = new Date(iso).getTime() <= Date.now();
  return (
    <span className={expired ? "text-destructive" : ""} title={iso}>
      令牌{expired ? "已过期" : "过期"} {fmtDateTime(iso)}
    </span>
  );
}

export default function Sub2API({ navigate }) {
  const [data, setData] = useState(null); // {base_url, accounts}
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("error");
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(() => new Set());
  const [batch, setBatch] = useState(null); // {done,total}
  const [importOpen, setImportOpen] = useState(false);
  const orgPicker = useOrgPicker();

  async function load() {
    setLoading(true);
    setError("");
    try {
      setData(await api.sub2apiAccounts());
    } catch (e) {
      if (e?.status === 401) return;
      setError(e.body?.detail || `加载失败（${e.status || "?"}）`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const accounts = data?.accounts || [];
  const visible = useMemo(
    () => accounts.filter((a) => matchFilter(a, filter)),
    [accounts, filter],
  );
  const selectedFixable = visible.filter((a) => selected.has(a.id) && canFix(a));
  const counts = useMemo(() => ({
    all: accounts.length,
    error: accounts.filter((a) => a.status === "error").length,
    fixable: accounts.filter((a) => a.status === "error" && canFix(a)).length,
    unmatched: accounts.filter((a) => !a.local).length,
  }), [accounts]);

  const setBusyFlag = (id, on) =>
    setBusy((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  // 重新授权并写回 sub2api；成功返回 true。quiet（批量）时不弹成功提示，失败始终提示原因。
  // pickOrg 时账号属于多个组织就弹窗选择工作空间（默认选中该 sub2api 账号原来的组织）；
  // 批量时不弹窗，后端沿用原来的组织。
  async function reauth(a, { quiet = false, pickOrg = false } = {}) {
    setBusyFlag(a.id, true);
    try {
      let orgUuid;
      if (pickOrg) {
        orgUuid = await orgPicker.pick(a.local.email, { preferred: a.org_uuid });
        if (orgUuid === undefined) return false; // 取消选择
      }
      const res = await api.sub2apiReauth(a.id, orgUuid);
      setData((d) =>
        d && {
          ...d,
          accounts: d.accounts.map((x) => (x.id === a.id ? res.account : x)),
        },
      );
      if (!quiet) toast.success(`「${a.name}」已重新授权并写回 sub2api`);
      return true;
    } catch (e) {
      if (e?.status === 401) return false;
      const msg = e.body?.detail || `失败（${e.status || "?"}）`;
      toast.error(`「${a.name}」${msg}`, { duration: 8000 });
      return false;
    } finally {
      setBusyFlag(a.id, false);
    }
  }

  async function reauthBatch(list) {
    const queue = [...list];
    const stat = { done: 0, total: list.length, ok: 0, failed: 0 };
    setBatch({ ...stat });
    const worker = async () => {
      while (queue.length) {
        const a = queue.shift();
        const ok = await reauth(a, { quiet: true });
        stat.done += 1;
        if (ok) stat.ok += 1;
        else stat.failed += 1;
        setBatch({ ...stat });
      }
    };
    await Promise.all([worker(), worker()]);
    setBatch(null);
    setSelected(new Set());
    if (stat.failed) toast.error(`完成：成功 ${stat.ok} 个，失败 ${stat.failed} 个`);
    else toast.success(`完成：${stat.ok} 个账号已重新授权并写回 sub2api`);
  }

  const toggle = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const fixableVisible = visible.filter(canFix);
  const allSelected =
    fixableVisible.length > 0 && fixableVisible.every((a) => selected.has(a.id));
  const someSelected = fixableVisible.some((a) => selected.has(a.id));
  const toggleAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) fixableVisible.forEach((a) => next.delete(a.id));
      else fixableVisible.forEach((a) => next.add(a.id));
      return next;
    });

  return (
    <div className="space-y-6">
      <PageHeader
        icon={KeyRound}
        title="sub2api 同步"
        description="列出 sub2api 里的 Claude OAuth 账号，按邮箱对应到本地账号。「重新授权」会用本地 sessionKey（经账号绑定的代理）获取新的 OAuth 令牌并写回 sub2api 的同一个账号：只替换令牌，保留并发、分组、模型映射等配置，同时清除异常状态。"
        actions={
          <>
            <Button variant="outline" onClick={load} disabled={loading || !!batch}>
              <RefreshCw className={cn(loading && "animate-spin")} /> 刷新
            </Button>
            <Button onClick={() => setImportOpen(true)} disabled={!!batch}>
              <Upload /> 上传 OAuth JSON
            </Button>
          </>
        }
      />

      {data?.base_url && (
        <div className="-mt-2 flex items-center gap-2 text-xs text-muted-foreground">
          <Link2 className="size-3.5" />
          已连接
          <code className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-foreground/80">{data.base_url}</code>
        </div>
      )}

      {error && (
        <Callout
          tone="danger"
          icon={CircleAlert}
          title="无法读取 sub2api 账号"
          actions={/未配置/.test(error) && (
            <Button variant="outline" size="sm" onClick={() => navigate("settings")}>
              <SettingsIcon /> 去设置
            </Button>
          )}
        >
          {error}
        </Callout>
      )}

      {data && (
        <StatGrid>
          <StatCard index={0} label="账号总数" value={counts.all} icon={Users} tone="primary"
            onClick={() => setFilter("all")} active={filter === "all"} />
          <StatCard index={1} label="异常" value={counts.error} icon={CircleAlert}
            tone={counts.error ? "danger" : "muted"}
            onClick={() => setFilter("error")} active={filter === "error"} />
          <StatCard index={2} label="异常且可修复" value={counts.fixable} icon={Wrench} tone="warning"
            onClick={() => setFilter("fixable")} active={filter === "fixable"} />
          <StatCard index={3} label="本地无对应账号" value={counts.unmatched} icon={UserX} tone="muted"
            onClick={() => setFilter("unmatched")} active={filter === "unmatched"} />
        </StatGrid>
      )}

      {(data || loading) && (
        <Card className="gap-0 py-0">
          {data && (
            <div className="flex flex-wrap items-center gap-3 border-b p-3">
              <SegmentedTabs
                value={filter}
                onChange={setFilter}
                items={FILTERS.map(([k, label]) => [k, label, counts[k]])}
              />
              <label className="flex cursor-pointer items-center gap-2.5 px-1 text-sm text-muted-foreground select-none">
                <Checkbox
                  checked={allSelected ? true : someSelected ? "indeterminate" : false}
                  disabled={fixableVisible.length === 0 || !!batch}
                  onCheckedChange={toggleAll}
                />
                全选可修复
              </label>
              <span className="flex-1" />
              <span className="text-xs text-muted-foreground tabular-nums">显示 {visible.length} 个</span>
              {selectedFixable.length > 0 && (
                <Button disabled={!!batch} onClick={() => reauthBatch(selectedFixable)}>
                  {batch ? <Spinner /> : <KeyRound />}
                  {batch
                    ? `处理中 ${batch.done}/${batch.total}…`
                    : `重新授权所选（${selectedFixable.length}）`}
                </Button>
              )}
            </div>
          )}
          {loading && !data ? (
            <div className="divide-y">
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-3.5">
                  <Skeleton className="size-4" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-3.5 w-56" />
                    <Skeleton className="h-3 w-96 max-w-full" />
                  </div>
                  <Skeleton className="h-7 w-20" />
                </div>
              ))}
            </div>
          ) : visible.length === 0 ? (
            <Empty className="py-14">
              <EmptyHeader>
                <EmptyMedia variant="icon">{filter === "error" ? <CircleCheck /> : <SearchX />}</EmptyMedia>
                <EmptyTitle>
                  {filter === "error" ? "sub2api 里没有异常的 Claude OAuth 账号" : "没有符合条件的账号"}
                </EmptyTitle>
                {filter === "error" && <EmptyDescription>所有账号状态正常，无需重新授权。</EmptyDescription>}
              </EmptyHeader>
            </Empty>
          ) : (
            <ul className="divide-y">
              {visible.map((a, i) => {
                const [label, tone] = STATUS[a.status] || [a.status || "未知", "muted"];
                return (
                  <m.li
                    key={a.id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.2, delay: Math.min(i, 12) * 0.015 }}
                    className={cn(
                      "flex items-center gap-3 px-4 py-3 text-sm transition-colors",
                      selected.has(a.id) && "bg-primary/5",
                    )}
                  >
                    <Checkbox
                      aria-label={`选择 ${a.name}`}
                      checked={selected.has(a.id)}
                      disabled={!canFix(a) || !!batch}
                      onCheckedChange={() => toggle(a.id)}
                    />
                    <span className="flex min-w-0 flex-1 flex-col gap-1.5">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate font-medium" title={a.name}>{a.name}</span>
                        <span className="shrink-0 font-mono text-xs text-muted-foreground">#{a.id}</span>
                      </span>
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                        <Pill tone={tone} dot>{label}</Pill>
                        <TokenExpiry iso={a.token_expires_at} />
                        {a.local ? (
                          <span>
                            本地：{a.local.email === a.name ? "已对应" : a.local.email}
                            {a.local.check_status
                              ? ` · ${LIVE[a.local.check_status] || a.local.check_status}`
                              : ""}
                            {a.local.has_session_key ? "" : " · 无 sessionKey"}
                          </span>
                        ) : (
                          <span className="text-warning">
                            {a.email ? `本地没有 ${a.email}` : "无法识别邮箱"}
                          </span>
                        )}
                      </span>
                      {a.error_message ? (
                        <span
                          className="w-fit max-w-full truncate rounded-md bg-destructive/8 px-2 py-1 font-mono text-[11px] text-destructive"
                          title={a.error_message}
                        >
                          {a.error_message}
                        </span>
                      ) : null}
                    </span>
                    <TipButton
                      variant="outline"
                      className="shrink-0"
                      disabled={!canFix(a) || busy.has(a.id) || !!batch}
                      tip={canFix(a) ? "用本地 sessionKey 重新授权并写回 sub2api" : "本地没有可用的 sessionKey"}
                      onClick={() => reauth(a, { pickOrg: true })}
                    >
                      {busy.has(a.id) ? <Spinner /> : <KeyRound />}
                      {busy.has(a.id) ? "授权中…" : "重新授权"}
                    </TipButton>
                  </m.li>
                );
              })}
            </ul>
          )}
        </Card>
      )}
      <OAuthImportDialog open={importOpen} onOpenChange={setImportOpen}
        onImported={() => { setFilter("all"); load(); }} />
      {orgPicker.dialog}
    </div>
  );
}
