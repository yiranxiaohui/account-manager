import { useEffect, useMemo, useRef, useState } from "react";
import { m } from "motion/react";
import { toast } from "sonner";
import {
  ChevronRight,
  CircleCheck,
  CircleX,
  Hand,
  History,
  ImageIcon,
  Loader2,
  Play,
  SquareTerminal,
} from "lucide-react";
import { api } from "../api.js";
import { cn } from "@/lib/utils";
import { TONE } from "@/lib/tones";
import { fmtDateTime, fmtDuration, relTime } from "@/lib/time";
import { PageHeader } from "@/components/page-header";
import { StatCard, StatGrid } from "@/components/stat-card";
import { StatusBadge, statusTone } from "@/components/status-badge";
import { LogConsole } from "@/components/log-console";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

const STATUS_ICON = {
  success: CircleCheck,
  failed: CircleX,
  needs_manual: Hand,
  running: Loader2,
};

function RunIcon({ status }) {
  const Icon = STATUS_ICON[status] || History;
  return (
    <span className={cn("inline-flex size-8 shrink-0 items-center justify-center rounded-lg", TONE[statusTone(status)])}>
      <Icon className={cn("size-4", status === "running" && "animate-spin")} />
    </span>
  );
}

export default function Register({ runStream }) {
  const { activeRunId, activeStatus, logLines, attach, streamEpoch } = runStream;
  const [proxies, setProxies] = useState([]);
  const [proxyId, setProxyId] = useState("");
  useEffect(() => {
    api.getConfig().then((cfg) => setProxies(cfg.saved_proxies || []))
      .catch(() => toast.error("代理列表加载失败，请刷新页面"));
  }, []);
  const [domain, setDomain] = useState("");
  const [email, setEmail] = useState("");
  const [starting, setStarting] = useState(false);
  const [runs, setRuns] = useState([]);
  const [runsLoaded, setRunsLoaded] = useState(false);
  const [selectedRun, setSelectedRun] = useState(null);
  const logPanelRef = useRef(null);

  useEffect(() => {
    api.listRuns().then(setRuns).catch(() => {}).finally(() => setRunsLoaded(true));
  }, [streamEpoch]);

  useEffect(() => {
    const el = logPanelRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logLines]);

  const stats = useMemo(() => {
    const count = (s) => runs.filter((r) => r.status === s).length;
    const success = count("success");
    const finished = runs.filter((r) => r.status !== "running").length;
    return {
      total: runs.length,
      success,
      failed: count("failed"),
      manual: count("needs_manual"),
      rate: finished ? `${Math.round((success / finished) * 100)}%` : "",
    };
  }, [runs]);

  async function startRun() {
    setStarting(true);
    try {
      const res = await api.startRun(email || undefined, domain || undefined, proxyId || undefined);
      attach(res.run_id);
    } catch (err) {
      toast.error(err.status === 409 ? "已有任务在运行" : err.body?.detail || "启动失败，请重试");
    } finally {
      setStarting(false);
    }
  }

  async function openRun(id) {
    try {
      setSelectedRun(await api.runDetail(id));
    } catch {
      setSelectedRun(null);
    }
  }

  const running = activeStatus === "running";

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Play}
        title="注册任务"
        description="创建自动化注册任务，实时查看运行日志；点击历史记录可查看完整日志与截图。"
      />

      <StatGrid>
        <StatCard index={0} label="最近运行" value={stats.total} icon={History} tone="primary" hint="最多 50 次" />
        <StatCard index={1} label="成功" value={stats.success} icon={CircleCheck} tone="success"
          hint={stats.rate && `成功率 ${stats.rate}`} />
        <StatCard index={2} label="失败" value={stats.failed} icon={CircleX} tone="danger" />
        <StatCard index={3} label="需人工介入" value={stats.manual} icon={Hand} tone="warning" />
      </StatGrid>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] lg:items-stretch">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>新建注册</CardTitle>
            <CardDescription>参数均可留空，自动创建邮箱并完成注册。</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="registration-domain">邮箱后缀 / domain</Label>
              <Input
                id="registration-domain"
                placeholder="可选，如 example.com"
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="registration-email">已有邮箱</Label>
              <Input
                id="registration-email"
                placeholder="可选，用于重跑该账号"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="registration-proxy">注册代理</Label>
              <NativeSelect
                id="registration-proxy"
                className="w-full"
                value={proxyId}
                onChange={(e) => setProxyId(e.target.value)}
              >
                <NativeSelectOption value="">使用默认配置（3x-ui / 固定代理 / 直连）</NativeSelectOption>
                {proxies.map((p) => (
                  <NativeSelectOption key={p.id} value={p.id}>{p.name}</NativeSelectOption>
                ))}
              </NativeSelect>
              <p className="text-xs text-muted-foreground">
                在<a className="text-primary underline-offset-4 hover:underline" href="#/proxies">代理池</a>
                保存代理后，可在此选择用于本次注册。
              </p>
            </div>
            <Button size="lg" className="w-full" onClick={startRun} disabled={starting || running}>
              {starting || running ? <Spinner /> : <Play />}
              {starting ? "启动中…" : running ? "任务运行中…" : "开始注册"}
            </Button>
          </CardContent>
        </Card>

        {/* 日志区在宽屏下绝对定位填满整行：高度跟随左侧表单，日志再长也不会撑高这一行 */}
        <div className="relative h-[26rem] min-w-0 lg:h-auto lg:min-h-[22rem]">
        <LogConsole
          ref={logPanelRef}
          className="absolute inset-0"
          title={activeRunId ? `run #${activeRunId} · 实时日志` : "实时日志"}
          right={activeRunId ? <StatusBadge status={activeStatus} /> : null}
        >
          {!activeRunId ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center font-sans">
              <span className="inline-flex size-10 items-center justify-center rounded-xl bg-white/6 text-white/60">
                <SquareTerminal className="size-5" />
              </span>
              <div>
                <p className="text-sm font-medium text-white/80">暂无运行中的任务</p>
                <p className="mt-1 text-xs text-white/45">开始注册后，日志会在这里实时滚动显示。</p>
              </div>
            </div>
          ) : logLines.length === 0 ? (
            <div className="flex items-center gap-2 text-white/45">
              <Loader2 className="size-3.5 animate-spin" /> 等待日志输出…
            </div>
          ) : (
            logLines.map((line, i) => (
              <div key={i} className="flex gap-3">
                <span className="w-7 shrink-0 text-right text-white/25 select-none">{i + 1}</span>
                <span className="min-w-0 flex-1">{line}</span>
              </div>
            ))
          )}
        </LogConsole>
        </div>
      </div>

      <Card className="gap-0 pb-0">
        <CardHeader className="border-b">
          <CardTitle>运行历史</CardTitle>
          <CardDescription>最近 50 次注册运行，点击查看详情。</CardDescription>
        </CardHeader>
        {!runsLoaded ? (
          <div className="space-y-3 p-4">
            {[0, 1, 2].map((i) => <Skeleton key={i} className="h-11 w-full" />)}
          </div>
        ) : runs.length === 0 ? (
          <Empty className="py-12">
            <EmptyHeader>
              <EmptyMedia variant="icon"><History /></EmptyMedia>
              <EmptyTitle>暂无运行记录</EmptyTitle>
              <EmptyDescription>开始第一次注册后，运行记录会出现在这里。</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <ul className="max-h-[30rem] divide-y overflow-auto">
            {runs.map((r, i) => (
              <m.li
                key={r.id}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.2, delay: Math.min(i, 12) * 0.02 }}
              >
                <button
                  type="button"
                  onClick={() => openRun(r.id)}
                  className="group flex w-full items-center gap-3 px-4 py-3 text-left transition-colors outline-none hover:bg-muted/50 focus-visible:bg-muted/60"
                >
                  <RunIcon status={r.status} />
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="shrink-0 font-mono text-xs text-muted-foreground">#{r.id}</span>
                      <span className="truncate text-sm font-medium">
                        {r.email || "（未指定邮箱）"}
                        {r.domain ? <span className="font-normal text-muted-foreground"> @{r.domain}</span> : null}
                      </span>
                    </span>
                    <span className="truncate text-xs text-muted-foreground" title={fmtDateTime(r.started_at)}>
                      {r.started_at ? `开始于 ${relTime(r.started_at)}` : ""}
                      {r.finished_at ? ` · 耗时 ${fmtDuration(r.started_at, r.finished_at)}` : ""}
                    </span>
                  </span>
                  <StatusBadge status={r.status} />
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground/60 transition-transform group-hover:translate-x-0.5" />
                </button>
              </m.li>
            ))}
          </ul>
        )}
      </Card>

      <Sheet open={!!selectedRun} onOpenChange={(o) => !o && setSelectedRun(null)}>
        <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-2xl">
          {selectedRun && (
            <>
              <SheetHeader className="border-b pr-12">
                <SheetTitle className="flex items-center gap-2">
                  运行 <span className="font-mono">#{selectedRun.id}</span>
                  <StatusBadge status={selectedRun.status} />
                </SheetTitle>
                <SheetDescription className="truncate">
                  {selectedRun.email || "（未指定邮箱）"}
                  {selectedRun.domain ? ` @${selectedRun.domain}` : ""}
                </SheetDescription>
                <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1 text-xs text-muted-foreground">
                  {selectedRun.started_at && <span>开始：{fmtDateTime(selectedRun.started_at)}</span>}
                  {selectedRun.finished_at && <span>结束：{fmtDateTime(selectedRun.finished_at)}</span>}
                  {selectedRun.finished_at && (
                    <span>耗时：{fmtDuration(selectedRun.started_at, selectedRun.finished_at)}</span>
                  )}
                </div>
              </SheetHeader>
              <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
                <LogConsole className="min-h-48 flex-1" title="log.txt">
                  <pre className="whitespace-pre-wrap break-all">{selectedRun.log || "（无日志）"}</pre>
                </LogConsole>
                {selectedRun.screenshots && selectedRun.screenshots.length > 0 && (
                  <div className="space-y-2">
                    <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                      <ImageIcon className="size-3.5" /> 截图（{selectedRun.screenshots.length}）
                    </div>
                    <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                      {selectedRun.screenshots.map((name) => (
                        <a
                          key={name}
                          href={`/runs/${selectedRun.id}/${name}`}
                          target="_blank"
                          rel="noreferrer"
                          className="group overflow-hidden rounded-lg ring-1 ring-foreground/10"
                          title={name}
                        >
                          <img
                            className="block aspect-video w-full object-cover transition-transform group-hover:scale-105"
                            src={`/runs/${selectedRun.id}/${name}`}
                            alt={name}
                          />
                        </a>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
