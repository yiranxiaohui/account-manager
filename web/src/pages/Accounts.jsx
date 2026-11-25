import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { toast } from "sonner";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Building2,
  Check,
  Copy,
  Crown,
  Download,
  ExternalLink,
  Gauge,
  Globe,
  HeartPulse,
  KeyRound,
  ListFilter,
  LogIn,
  MonitorPlay,
  MoreHorizontal,
  PackageCheck,
  Pencil,
  Plus,
  RotateCcw,
  ScanSearch,
  Search,
  SearchX,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestionMark,
  ShieldX,
  Smartphone,
  Sparkles,
  Trash2,
  Upload,
  Users,
  X,
} from "lucide-react";
import { api } from "../api.js";
import { cn } from "@/lib/utils";
import { maskProxyUrl, parseProxyUrl } from "@/lib/proxy";
import { relTime } from "@/lib/time";
import {
  matchPlanFilter,
  otherPlanCount,
  planFilterOptions,
  planLabel,
  planRank,
  planTone,
  planTooltip,
} from "@/lib/plan";
import {
  displayWindows,
  parseUsage,
  resetIn,
  spendText,
  usageSortValue,
  usageSummary,
  usageTone,
  usageTooltip,
  windowExpired,
  windowLabel,
  windowPercent,
  windowShort,
} from "@/lib/usage";
import { TONE_TEXT } from "@/lib/tones";
import { StatusBadge } from "@/components/status-badge";
import { Pill } from "@/components/pill";
import { PageHeader } from "@/components/page-header";
import { StatCard, StatGrid } from "@/components/stat-card";
import { Callout } from "@/components/callout";
import { TipButton } from "@/components/tip-button";
import { ExportDialog } from "@/components/export-dialog";
import { ImportDialog } from "@/components/import-dialog";
import { ManualLoginDialog } from "@/components/manual-login-dialog";
import { useOrgPicker } from "@/components/org-select-dialog";
import {
  ANDROID_VIEWER,
  AndroidCallout,
  AndroidInstallDialog,
  useAndroid,
} from "@/components/android";
import { KEEP_PROXY, ProxySelect } from "@/components/proxy-select";
import {
  AccountCreateDialog,
  AccountFields,
  accountPayload,
  emptyAccountForm,
} from "@/components/account-create-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { OptionSelect } from "@/components/option-select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

// 状态筛选（订阅单独一个下拉框，见 PLAN_FILTERS）；null 是下拉框里的分隔线
const FILTERS = [
  ["all", "全部状态"],
  null,
  ["alive", "有效"],
  ["dead", "失效"],
  ["blocked", "被拦截"],
  ["unchecked", "未检测"],
  null,
  ["claimed", "已获取"],
  ["unclaimed", "未获取"],
  ["no_sk", "无 sessionKey"],
  ["oauth", "已授权 OAuth"],
  ["no_oauth", "未授权 OAuth"],
];

// 订阅下拉框里这些选项前加分隔线：Max 系列 / Pro / Team 系列 / Free 与未识别
const PLAN_SEPARATE_BEFORE = new Set(["group:max", "pro", "group:team", "free"]);

function matchFilter(a, filter) {
  switch (filter) {
    case "alive":
    case "dead":
    case "blocked":
      return a.check_status === filter;
    case "unchecked":
      return !a.check_status;
    case "claimed":
      return !!a.claimed_at;
    case "unclaimed":
      return !a.claimed_at;
    case "no_sk":
      return !a.session_key;
    case "oauth":
      return !!a.access_token;
    case "no_oauth":
      return !a.access_token;
    default:
      return true;
  }
}

function matchQuery(a, q, proxyById) {
  if (!q) return true;
  return [a.email, a.display_name, proxyById[a.proxy_id]?.name, parseProxyUrl(a.proxy)?.address,
    a.session_key, planLabel(a.plan)]
    .some((v) => String(v || "").toLowerCase().includes(q));
}

// 可排序的列：dir 是首次点击的方向；value 返回 null 表示缺失，不论升降序都排在最后
function timeValue(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
}

const SORTS = {
  email: { dir: "asc", value: (a) => String(a.email || "").toLowerCase() },
  plan: { dir: "desc", value: (a) => planRank(a.plan) || null },
  // 额度首次点击按升序：剩余额度多的在前
  usage: { dir: "asc", value: (a) => usageSortValue(a) },
  check: { dir: "desc", value: (a) => (a.check_status ? timeValue(a.checked_at) ?? 0 : null) },
};

function sortAccounts(list, sort) {
  const spec = SORTS[sort.key];
  if (!spec) return list;
  const sign = sort.dir === "asc" ? 1 : -1;
  return list
    .map((a) => [a, spec.value(a)])
    .sort(([, x], [, y]) => {
      if (x === y) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return (x < y ? -1 : 1) * sign;
    })
    .map(([a]) => a);
}

// 可点击排序的表头：首次点击按默认方向，再点反向，第三次恢复默认顺序（最新添加在前）
function SortHead({ label, sortKey, sort, onSort, className }) {
  const active = sort.key === sortKey;
  const Icon = !active ? ArrowUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <TableHead
      className={className}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "-mx-1.5 inline-flex h-7 items-center gap-1 rounded-md px-1.5 outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50",
          active && "text-foreground",
        )}
      >
        {label}
        <Icon className={cn("size-3.5", !active && "opacity-40")} />
      </button>
    </TableHead>
  );
}

const Dash = () => <span className="text-muted-foreground/50">—</span>;

// 表格行的悬停 / 选中底色写在 --row-bg 里；吸右的操作列用实底（bg-card）遮住横向滚动的内容，
// 再叠一层同一变量的渐变，保证与整行底色一致
const ROW_BG =
  "bg-(--row-bg) [--row-bg:transparent] hover:[--row-bg:color-mix(in_oklab,var(--color-muted)_50%,transparent)] data-[state=selected]:[--row-bg:color-mix(in_oklab,var(--color-primary)_6%,transparent)]";
const STICKY_BG = "bg-card bg-[linear-gradient(var(--row-bg),var(--row-bg))]";

// 账号绑定的代理：显示代理池条目名称；地址不在代理池时（无法迁移的旧数据）黄色提示
function ProxyBadge({ acct, proxyById }) {
  if (!acct.proxy) return <Dash />;
  const entry = proxyById[acct.proxy_id];
  if (!entry) {
    return (
      <Pill
        tone="warning"
        title={`代理不在代理池：${maskProxyUrl(acct.proxy)}，请编辑账号改选代理池中的代理`}
      >
        代理未入池
      </Pill>
    );
  }
  const addr = parseProxyUrl(entry.url)?.address;
  return (
    <Badge
      variant="outline"
      className="max-w-36 font-normal text-muted-foreground"
      title={[addr, entry.location].filter(Boolean).join(" · ")}
    >
      <Globe />
      <span className="truncate">{entry.name}</span>
    </Badge>
  );
}

const LIVE_LABEL = { alive: "有效", dead: "失效", blocked: "被拦截", error: "检测失败" };
const LIVE_TONE = { alive: "success", dead: "danger", blocked: "warning", error: "muted" };
const LIVE_ICON = { alive: ShieldCheck, dead: ShieldX, blocked: ShieldAlert, error: ShieldQuestionMark };

function LiveBadge({ status, checkedAt, detail }) {
  if (!status) return <span className="text-xs text-muted-foreground">未检测</span>;
  const Icon = LIVE_ICON[status] || ShieldQuestionMark;
  return (
    <Pill tone={LIVE_TONE[status] || "muted"} title={detail || ""}>
      <Icon />
      {LIVE_LABEL[status] || status}
      {checkedAt ? <span className="font-normal opacity-70">· {relTime(checkedAt)}</span> : null}
    </Pill>
  );
}

function untilTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.round((then - Date.now()) / 1000);
  if (sec <= 0) return "已过期";
  if (sec < 3600) return `${Math.max(1, Math.floor(sec / 60))} 分钟后过期`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时后过期`;
  return `${Math.floor(sec / 86400)} 天后过期`;
}

const PLAN_ICON = {
  max: Crown,
  max_5x: Crown,
  max_20x: Crown,
  pro: Sparkles,
  team: Building2,
  enterprise: Building2,
};

// 账号套餐：检测 sessionKey / 列组织 / OAuth 授权时自动识别；属于多个不同套餐的组织时显示 +N
function PlanBadge({ acct }) {
  if (!acct.plan) return <Dash />;
  const Icon = PLAN_ICON[acct.plan];
  const others = otherPlanCount(acct);
  return (
    <Pill tone={planTone(acct.plan)} title={planTooltip(acct)}>
      {Icon ? <Icon /> : null}
      {planLabel(acct.plan)}
      {others ? <span className="font-normal opacity-70">+{others}</span> : null}
    </Pill>
  );
}

const BAR_TONE = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-destructive",
  muted: "bg-muted-foreground/40",
};

// 一条用量进度：柱子长度是已用百分比，颜色跟随接口给出的严重程度
function UsageBar({ percent, tone, className }) {
  return (
    <div className={cn("h-1.5 overflow-hidden rounded-full bg-muted", className)}>
      <m.div
        className={cn("h-full rounded-full", BAR_TONE[tone] || BAR_TONE.muted)}
        initial={{ width: 0 }}
        animate={{ width: `${Math.min(100, Math.max(0, percent || 0))}%` }}
        transition={{ duration: 0.4, ease: "easeOut" }}
      />
    </div>
  );
}

function UsageRow({ label, title, percent, tone, text }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px] leading-none">
      <span className="w-9 shrink-0 truncate text-muted-foreground" title={title}>{label}</span>
      <UsageBar percent={percent} tone={tone} className="w-14 shrink-0" />
      <span className={cn("whitespace-nowrap tabular-nums", TONE_TEXT[tone] || TONE_TEXT.muted)}>{text}</span>
    </div>
  );
}

// 账号用量额度：检测 sessionKey 时顺带从 claude.ai 取主组织的用量。Pro / Max 显示 5 小时、
// 7 天与各模型周限额，Enterprise 显示用量额度金额；已过重置时间的窗口按 0 灰显
function UsageCell({ acct }) {
  const usage = parseUsage(acct.usage);
  const title = usageTooltip(acct);
  if (!usage) return <span title={title}><Dash /></span>;
  const rows = displayWindows(usage);
  if (!rows.length && !usage.spend) {
    return <span className="text-xs text-muted-foreground" title={title}>无数据</span>;
  }
  return (
    <div className="flex flex-col gap-1" title={title}>
      {rows.map((w) => {
        const pct = windowPercent(w);
        return (
          <UsageRow
            key={`${w.kind}:${w.model}`}
            label={windowShort(w)}
            title={windowLabel(w)}
            percent={pct}
            tone={w.missing ? "muted" : usageTone(w)}
            text={`${pct}%`}
          />
        );
      })}
      {usage.spend ? (
        <UsageRow
          label="额度"
          title="用量额度"
          percent={usage.spend.percent}
          tone={usageTone(usage.spend)}
          text={spendText(usage.spend)}
        />
      ) : null}
    </div>
  );
}

// 编辑抽屉里的额度明细：每个窗口一行，带重置时间
function UsageDetail({ acct }) {
  const usage = parseUsage(acct.usage);
  if (!usage) return null;
  const rows = displayWindows(usage);
  return (
    <div className="rounded-lg bg-background p-3 text-xs ring-1 ring-foreground/10">
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
        <span className="flex items-center gap-1.5 font-medium text-foreground">
          <Gauge className="size-3.5 text-primary" /> 用量额度
        </span>
        {usage.org_name ? <span className="truncate">{usage.org_name}</span> : null}
        {acct.usage_checked_at ? (
          <span title={acct.usage_checked_at}>· 获取于 {relTime(acct.usage_checked_at)}</span>
        ) : null}
      </div>
      {!rows.length && !usage.spend ? (
        <p className="text-muted-foreground">该组织没有返回用量限额（Free 套餐不提供）。</p>
      ) : (
        <div className="flex flex-col gap-2">
          {rows.map((w) => {
            const pct = windowPercent(w);
            const expired = windowExpired(w);
            const tone = w.missing ? "muted" : usageTone(w);
            return (
              <div key={`${w.kind}:${w.model}`} className="grid grid-cols-[7rem_1fr_auto] items-center gap-2">
                <span className="flex min-w-0 items-center gap-1.5" title={windowLabel(w)}>
                  <span className="truncate">{windowLabel(w)}</span>
                  {w.active ? (
                    <span className={cn("size-1.5 shrink-0 rounded-full bg-current", TONE_TEXT[tone])}
                      title="当前生效的限额" />
                  ) : null}
                </span>
                <UsageBar percent={pct} tone={tone} />
                <span className="text-right whitespace-nowrap text-muted-foreground tabular-nums">
                  <span className={cn("font-medium", TONE_TEXT[tone])}>{pct}%</span>
                  {w.missing ? " · 未开始" : expired ? ` · 已重置（上次 ${Math.round(w.percent)}%）` : ` · ${resetIn(w.resets_at)}`}
                </span>
              </div>
            );
          })}
          {usage.spend ? (
            <div className="grid grid-cols-[7rem_1fr_auto] items-center gap-2">
              <span>用量额度</span>
              <UsageBar percent={usage.spend.percent} tone={usageTone(usage.spend)} />
              <span className={cn("text-right font-medium whitespace-nowrap tabular-nums",
                TONE_TEXT[usageTone(usage.spend)])}>
                {spendText(usage.spend)}
              </span>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function OAuthBadge({ acct }) {
  if (!acct.access_token) return <Dash />;
  const expired = acct.oauth_expires_at && new Date(acct.oauth_expires_at).getTime() <= Date.now();
  return (
    <Pill
      tone={expired ? "muted" : "info"}
      title={`OAuth 授权时间：${acct.oauth_at || "未知"}\naccess_token 过期：${acct.oauth_expires_at || "未知"}${
        acct.org_name ? `\n组织：${acct.org_name}` : ""
      }`}
    >
      <KeyRound />
      {acct.oauth_expires_at ? untilTime(acct.oauth_expires_at) : "已授权"}
    </Pill>
  );
}

function ClaimedBadge({ acct }) {
  if (!acct.claimed_at) return <Dash />;
  return (
    <Pill tone="highlight" title={`获取时间：${acct.claimed_at}`}>
      <PackageCheck />
      已获取
      <span className="font-normal opacity-70">· {relTime(acct.claimed_at)}</span>
    </Pill>
  );
}

const OAUTH_COPY_FIELDS = [
  ["access_token", "access_token"],
  ["refresh_token", "refresh_token"],
  ["org_name", "组织名称"],
  ["org_uuid", "组织 UUID"],
  ["account_uuid", "账号 UUID"],
];

export default function Accounts({ attach, running, navigate }) {
  const [accounts, setAccounts] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [authorizing, setAuthorizing] = useState(() => new Set());
  const [batchOAuth, setBatchOAuth] = useState(null); // {done, total, ok, failed}
  const [batchCheck, setBatchCheck] = useState(null); // {done, total, alive, failed}
  const orgPicker = useOrgPicker();
  const [takeover, setTakeover] = useState({ running: false, email: null });
  const android = useAndroid();
  const [androidInstallOpen, setAndroidInstallOpen] = useState(false);
  const [relogging, setRelogging] = useState(false);
  const [checking, setChecking] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const busyRef = useRef(false);
  const [copiedEmail, setCopiedEmail] = useState("");
  const [editingEmail, setEditingEmail] = useState("");
  const [editForm, setEditForm] = useState({});
  // 待删除的邮箱列表；单个删除也走数组，空数组表示没有待确认的删除
  const [deleteTargets, setDeleteTargets] = useState([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [planFilter, setPlanFilter] = useState("all");
  // 表格排序：key 为空时保持接口顺序（最新添加在前）
  const [sort, setSort] = useState({ key: "", dir: "desc" });
  const [selected, setSelected] = useState(() => new Set());
  const [saving, setSaving] = useState(false);
  const [proxies, setProxies] = useState([]);
  const [batchProxy, setBatchProxy] = useState(null); // {emails, proxyId, busy}
  const proxyById = useMemo(
    () => Object.fromEntries(proxies.map((p) => [p.id, p])),
    [proxies],
  );

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = accounts.filter((a) =>
      matchFilter(a, filter) && matchPlanFilter(a.plan, planFilter) && matchQuery(a, q, proxyById));
    return sortAccounts(list, sort);
  }, [accounts, query, filter, planFilter, sort, proxyById]);
  const selectedVisible = visible.filter((a) => selected.has(a.email));
  const allVisibleSelected = visible.length > 0 && selectedVisible.length === visible.length;
  const filtered = filter !== "all" || planFilter !== "all" || !!query.trim();

  // 下拉框选项带上各自的账号数
  // 下拉框选项：数量显示在弹层右侧
  const filterOptions = useMemo(
    () => FILTERS.map((f) => (f
      ? { value: f[0], label: f[1], hint: String(accounts.filter((a) => matchFilter(a, f[0])).length) }
      : { separator: true })),
    [accounts],
  );
  const planOptions = useMemo(
    () => planFilterOptions(accounts, planFilter).flatMap((o) => {
      const item = { value: o.value, label: o.label, hint: String(o.count) };
      return PLAN_SEPARATE_BEFORE.has(o.value) ? [{ separator: true }, item] : [item];
    }),
    [accounts, planFilter],
  );

  const clearFilters = () => {
    setQuery("");
    setFilter("all");
    setPlanFilter("all");
  };

  // 点击表头：默认方向 → 反向 → 恢复接口顺序
  const toggleSort = (key) =>
    setSort((s) => {
      const first = SORTS[key].dir;
      if (s.key !== key) return { key, dir: first };
      if (s.dir === first) return { key, dir: first === "asc" ? "desc" : "asc" };
      return { key: "", dir: "desc" };
    });

  // 编辑抽屉关闭动画期间账号已不在编辑态，沿用最后一次的账号数据继续渲染
  const editingAcct = editingEmail ? accounts.find((a) => a.email === editingEmail) : null;
  const lastEditAcct = useRef(null);
  if (editingAcct) lastEditAcct.current = editingAcct;
  const sheetAcct = editingAcct || lastEditAcct.current;

  const stats = useMemo(() => ({
    total: accounts.length,
    alive: accounts.filter((a) => a.check_status === "alive").length,
    oauth: accounts.filter((a) => a.access_token).length,
    claimed: accounts.filter((a) => a.claimed_at).length,
  }), [accounts]);

  // 列表刷新后剔除已经不存在的选中项
  useEffect(() => {
    setSelected((prev) => {
      const alive = new Set(accounts.map((a) => a.email));
      const next = new Set([...prev].filter((e) => alive.has(e)));
      return next.size === prev.size ? prev : next;
    });
  }, [accounts]);

  const toggleSelect = (email) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(email)) next.delete(email);
      else next.add(email);
      return next;
    });

  const toggleSelectAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) visible.forEach((a) => next.delete(a.email));
      else visible.forEach((a) => next.add(a.email));
      return next;
    });

  function refreshLists() {
    api.listAccounts().then(setAccounts).catch(() => {}).finally(() => setLoaded(true));
    api.takeoverStatus().then(setTakeover).catch(() => {});
    android.refresh();
    api.getConfig().then((cfg) => setProxies(cfg.saved_proxies || [])).catch(() => {});
  }

  useEffect(() => {
    refreshLists();
  }, []);

  // 读取接管浏览器里的 sessionKey；新值会由后端检测后新建/更新账号。
  // manual=true 时是用户点按钮，结果都要提示；轮询时只在有新进展时提示。
  async function captureOnce(manual) {
    const res = await api.takeoverCapture();
    if (res.saved && !res.unchanged) {
      const tail = res.check_status === "alive" ? "" : "（存活检测暂时失败）";
      toast.success(
        `${res.created ? "已新建账号" : "已更新 sessionKey"}：${res.email}${tail}`,
        res.note ? { description: res.note } : undefined,
      );
      refreshLists();
    } else if (res.check_status === "dead" && (manual || !res.repeat)) {
      toast.error(`取到的 sessionKey 已失效：${res.check_detail || ""}`);
    } else if (manual) {
      if (!res.found) toast.error("浏览器里还没有 sessionKey，请先在接管画面完成登录");
      else toast.success(`sessionKey 未变化，已保存在 ${res.email}`);
    }
    return res;
  }

  // 接管期间每 5 秒自动取一次 sk：手动登录成功后无需任何操作即可入库。
  useEffect(() => {
    if (!takeover.running) return undefined;
    let inflight = false;
    const timer = setInterval(async () => {
      if (inflight || busyRef.current) return;
      inflight = true;
      try {
        await captureOnce(false);
      } catch (e) {
        if (e.status === 409) {
          api.takeoverStatus().then(setTakeover).catch(() => {});
        }
      } finally {
        inflight = false;
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [takeover.running]);

  const captureNow = async () => {
    busyRef.current = true;
    setCapturing(true);
    try {
      await captureOnce(true);
    } catch (e) {
      toast.error(e.body?.detail || `提取失败（${e.status || "?"}）`);
    } finally {
      busyRef.current = false;
      setCapturing(false);
    }
  };

  async function doRerun(acctEmail) {
    try {
      const res = await api.rerun(acctEmail);
      attach(res.run_id);
      toast.success("已开始重跑，正在跳转…");
      navigate("register");
    } catch (err) {
      toast.error(
        err.status === 409
          ? `「${acctEmail}」重跑失败：已有任务在运行`
          : `「${acctEmail}」重跑失败`,
      );
    }
  }

  async function doCheck(acctEmail) {
    setChecking(acctEmail);
    try {
      const res = await checkOne(acctEmail);
      if (res.plan) {
        const usage = usageSummary(res.usage);
        toast.success(
          `「${acctEmail}」${LIVE_LABEL[res.status] || res.status}，套餐 ${planLabel(res.plan)}${usage ? `，${usage}` : ""}`,
        );
      }
    } catch (e) {
      toast.error(`「${acctEmail}」检测失败（${e.status || "?"}）`);
    } finally {
      setChecking("");
    }
  }

  // 检测一个账号并合并结果（存活状态 + 识别出的套餐 + 用量额度），返回接口结果
  async function checkOne(acctEmail) {
    const res = await api.checkAccount(acctEmail);
    const { status, detail, usage_error: _usageError, ...rest } = res;
    setAccounts((list) =>
      list.map((a) =>
        a.email === acctEmail
          ? { ...a, ...rest, check_status: status, check_detail: detail }
          : a,
      ),
    );
    return res;
  }

  // 批量检测：前端并发 3 路逐个调用，顺带刷新套餐
  async function runBatchCheck(emails) {
    const queue = [...emails];
    const stat = { done: 0, total: emails.length, alive: 0, failed: 0 };
    setBatchCheck({ ...stat });
    const worker = async () => {
      while (queue.length) {
        const email = queue.shift();
        try {
          const res = await checkOne(email);
          if (res.status === "alive") stat.alive += 1;
          else stat.failed += 1;
        } catch {
          stat.failed += 1;
        }
        stat.done += 1;
        setBatchCheck({ ...stat });
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    setBatchCheck(null);
    const msg = `检测完成：有效 ${stat.alive} 个${stat.failed ? `，其它 ${stat.failed} 个` : ""}`;
    if (stat.failed) toast.warning(msg);
    else toast.success(msg);
  }

  const setAuthorizingFlag = (email, on) =>
    setAuthorizing((prev) => {
      const next = new Set(prev);
      if (on) next.add(email);
      else next.delete(email);
      return next;
    });

  // 用 sessionKey 获取 OAuth 令牌；成功返回 true。quiet 时不弹成功提示（批量用）。
  // pickOrg 时先取账号的组织列表，多个组织则弹窗选择工作空间；批量授权不弹窗，
  // 由后端沿用上次授权的组织（没有则 team 优先）。
  async function runOAuth(acctEmail, { quiet = false, pickOrg = false } = {}) {
    setAuthorizingFlag(acctEmail, true);
    try {
      let orgUuid;
      if (pickOrg) {
        orgUuid = await orgPicker.pick(acctEmail);
        if (orgUuid === undefined) return false; // 取消选择
      }
      const row = await api.accountOAuth(acctEmail, orgUuid);
      setAccounts((list) => list.map((a) => (a.email === acctEmail ? { ...a, ...row } : a)));
      if (!quiet) {
        toast.success(
          `「${acctEmail}」已获取 OAuth 令牌${row.org_name ? `（${row.org_name}）` : ""}`,
        );
      }
      return true;
    } catch (e) {
      if (e?.status === 401) return false;
      if (!quiet) {
        toast.error(e.body?.detail || `「${acctEmail}」OAuth 授权失败（${e.status || "?"}）`);
        if (e.status === 422) refreshLists(); // sessionKey 失效时后端已把检测结果记为失效
      }
      return false;
    } finally {
      setAuthorizingFlag(acctEmail, false);
    }
  }

  // 批量授权：前端并发 2 路逐个调用，避免单个长请求被反代超时，也能显示进度。
  async function runBatchOAuth(emails) {
    const queue = [...emails];
    const stat = { done: 0, total: emails.length, ok: 0, failed: 0 };
    setBatchOAuth({ ...stat });
    const worker = async () => {
      while (queue.length) {
        const email = queue.shift();
        const ok = await runOAuth(email, { quiet: true });
        stat.done += 1;
        if (ok) stat.ok += 1;
        else stat.failed += 1;
        setBatchOAuth({ ...stat });
      }
    };
    await Promise.all([worker(), worker()]);
    setBatchOAuth(null);
    refreshLists();
    if (stat.failed) toast.error(`OAuth 授权完成：成功 ${stat.ok} 个，失败 ${stat.failed} 个`);
    else toast.success(`OAuth 授权完成：成功 ${stat.ok} 个`);
  }

  async function copyLine(acct) {
    return copyText(acct.text || acct.email, acct.email);
  }

  async function copyText(text, copiedKey) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        // http 面板无 clipboard API，退回 execCommand
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopiedEmail(copiedKey);
      setTimeout(() => setCopiedEmail(""), 1500);
    } catch {
      toast.error("复制失败，请手动复制");
    }
  }

  const startTakeover = async (acctEmail) => {
    try {
      await api.takeoverStart(acctEmail);
      setTakeover(await api.takeoverStatus());
    } catch (e) {
      toast.error(
        e.status === 409
          ? "已有接管会话，请先结束"
          : e.status === 400
            ? "该账号无 sessionKey"
            : e.status === 403
              ? "接管功能已禁用"
              : `启动接管失败（${e.status || "?"}）`,
      );
    }
  };

  const stopTakeover = async () => {
    busyRef.current = true;
    try {
      // 结束前最后取一次，避免刚登录完还没轮询到就把浏览器关了。
      await captureOnce(false).catch(() => {});
      await api.takeoverStop();
      setTakeover({ running: false, email: null });
    } catch {
      /* ignore */
    } finally {
      busyRef.current = false;
    }
  };

  const reloginTakeover = async () => {
    busyRef.current = true;
    setRelogging(true);
    try {
      const res = await api.takeoverRelogin();
      refreshLists();
      toast.success(
        res.check_status === "alive"
          ? `「${res.email}」已重新登录并更新 sessionKey`
          : `「${res.email}」已更新 sessionKey，但存活检测暂时失败`,
      );
    } catch (e) {
      const detail = e.body?.detail;
      toast.error(
        e.status === 409
          ? "当前没有活动的接管会话"
          : e.status === 422
            ? detail || "重新登录失败，账号可能已被封"
            : detail || `重新登录失败（${e.status || "?"}）`,
      );
    } finally {
      busyRef.current = false;
      setRelogging(false);
    }
  };

  const startEdit = (a) => {
    setEditingEmail(a.email);
    const base = emptyAccountForm();
    const form = Object.fromEntries(Object.keys(base).map((k) => [k, a[k] || ""]));
    // 代理地址不在代理池时默认「保持原代理」，只改其它字段不会把它清掉
    if (a.proxy && !proxyById[a.proxy_id]) form.proxy_id = KEEP_PROXY;
    setEditForm(form);
  };

  const saveEdit = async () => {
    if (!String(editForm.email || "").trim()) return toast.error("邮箱不能为空");
    setSaving(true);
    try {
      const row = await api.accountUpdate(editingEmail, accountPayload(editForm));
      if (row.email !== editingEmail) {
        setSelected((prev) => {
          if (!prev.has(editingEmail)) return prev;
          const next = new Set(prev);
          next.delete(editingEmail);
          next.add(row.email);
          return next;
        });
      }
      setEditingEmail("");
      refreshLists();
      toast.success("已保存");
    } catch (e) {
      if (e?.status === 401) return;
      toast.error(e.body?.detail || `保存失败（${e.status || "?"}）`);
    } finally {
      setSaving(false);
    }
  };

  const toggleClaimed = async (a) => {
    const claimed = !a.claimed_at;
    try {
      const row = await api.setAccountClaimed(a.email, claimed);
      setAccounts((list) =>
        list.map((x) => (x.email === a.email ? { ...x, claimed_at: row.claimed_at } : x)),
      );
      toast.success(claimed ? "已标记为已获取" : "已取消「已获取」标记");
    } catch (e) {
      toast.error(`操作失败（${e.status || "?"}）`);
    }
  };

  const confirmBatchProxy = async () => {
    const { emails, proxyId } = batchProxy;
    setBatchProxy((s) => ({ ...s, busy: true }));
    try {
      const res = await api.accountsBatchProxy(emails, proxyId);
      const name = proxyId ? proxyById[proxyId]?.name || "所选代理" : "不使用代理";
      toast.success(`已将 ${res.updated} 个账号的代理设为「${name}」`);
      setBatchProxy(null);
      refreshLists();
    } catch (e) {
      if (e?.status === 401) return;
      toast.error(e.body?.detail || `设置代理失败（${e.status || "?"}）`);
      setBatchProxy((s) => s && { ...s, busy: false });
    }
  };

  const confirmDelete = async () => {
    const emails = deleteTargets;
    setDeleteTargets([]);
    try {
      if (emails.length === 1) {
        await api.accountDelete(emails[0]);
        toast.success(`已删除 ${emails[0]}`);
      } else {
        const res = await api.accountsBatchDelete(emails);
        toast.success(`已删除 ${res.deleted} 个账号`);
      }
      if (emails.includes(editingEmail)) setEditingEmail("");
      setSelected((prev) => {
        const next = new Set(prev);
        emails.forEach((e) => next.delete(e));
        return next;
      });
      refreshLists();
    } catch (e) {
      if (e?.status === 401) return;
      toast.error(e.body?.detail || `删除失败（${e.status || "?"}）`);
      refreshLists();
    }
  };

  const withSk = selectedVisible.filter((a) => a.session_key);

  // Android 模拟器按钮：本账号在跑就打开画面；运行环境没装先弹安装确认；否则启动。
  const androidAction = (a) => {
    const st = android.status;
    const hasEnv = (st.envs || []).includes(a.email);
    if (st.running && st.email === a.email) {
      return {
        tip: `Android 模拟器${st.phase === "running" ? "运行中" : "启动中"}，点击打开画面`,
        active: true,
        onClick: () => window.open(ANDROID_VIEWER, "_blank", "noopener"),
      };
    }
    if (st.running) return { tip: "已有其他账号的 Android 模拟器在运行，请先停止", disabled: true };
    if (!a.proxy) return { tip: "Android 模拟器只走账号代理：请先为账号绑定代理", disabled: true };
    if (st.install?.state === "installing") {
      return { tip: "Android 运行环境安装中，请稍候", disabled: true };
    }
    if (st.install && !st.install.ready) {
      return { tip: "启动 Android 模拟器（首次需先安装运行环境）", onClick: () => setAndroidInstallOpen(true) };
    }
    return {
      tip: hasEnv
        ? "启动该账号的 Android 模拟器（数据保留在上次的状态）"
        : "为该账号创建并启动 Android 模拟器（走账号代理）",
      onClick: () => android.start(a.email),
    };
  };

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Users}
        title="账号管理"
        description="查看账号的注册、存活与 OAuth 状态，管理 sessionKey、代理、浏览器接管与 Android 模拟器。"
        actions={
          <>
            {accounts.length > 0 && (
              <Button variant="outline" onClick={() => setExportOpen(true)}>
                <Download /> 导出
              </Button>
            )}
            <Button variant="outline" onClick={() => setImportOpen(true)}>
              <Upload /> 导入 SK
            </Button>
            <TipButton
              variant="outline"
              onClick={() => setManualOpen(true)}
              disabled={takeover.running}
              tip={takeover.running ? "已有接管会话，请先结束" : null}
            >
              <LogIn /> 手动登录
            </TipButton>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus /> 新增账号
            </Button>
          </>
        }
      />

      <StatGrid>
        <StatCard index={0} label="账号总数" value={stats.total} icon={Users} tone="primary"
          onClick={clearFilters} />
        <StatCard index={1} label="检测有效" value={stats.alive} icon={ShieldCheck} tone="success"
          onClick={() => setFilter("alive")} active={filter === "alive"} />
        <StatCard index={2} label="已授权 OAuth" value={stats.oauth} icon={KeyRound} tone="info"
          onClick={() => setFilter("oauth")} active={filter === "oauth"} />
        <StatCard index={3} label="已获取" value={stats.claimed} icon={PackageCheck} tone="highlight"
          onClick={() => setFilter("claimed")} active={filter === "claimed"} />
      </StatGrid>

      <AnimatePresence initial={false}>
        {takeover.running && (
          <m.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2 }}
          >
            <Callout
              tone="info"
              icon={MonitorPlay}
              title={
                <span className="flex items-center gap-2">
                  <span className="relative flex size-2">
                    <span className="absolute inline-flex size-full animate-ping rounded-full bg-info opacity-60" />
                    <span className="relative inline-flex size-2 rounded-full bg-info" />
                  </span>
                  {takeover.mode === "manual" && !takeover.email ? "手动登录中" : `正在接管：${takeover.email}`}
                </span>
              }
              actions={
                <>
                  <Button size="sm" asChild>
                    <a href="/takeover.html" target="_blank" rel="noopener noreferrer">
                      <ExternalLink /> 打开画面
                    </a>
                  </Button>
                  <Button variant="outline" size="sm" onClick={captureNow} disabled={relogging || capturing}>
                    {capturing ? <Spinner /> : <ScanSearch />}
                    {capturing ? "提取中…" : "提取 sk"}
                  </Button>
                  {takeover.email && (
                    <Button variant="outline" size="sm" onClick={reloginTakeover} disabled={relogging || capturing}>
                      {relogging ? <Spinner /> : <RotateCcw />}
                      {relogging ? "重新登录中…" : "重新自动登录"}
                    </Button>
                  )}
                  <Button variant="destructive" size="sm" onClick={stopTakeover} disabled={relogging}>
                    结束接管
                  </Button>
                </>
              }
            >
              {takeover.mode === "manual" && !takeover.email
                ? "在接管画面完成登录后，会自动提取 sessionKey 并新建账号。"
                : "接管期间每 5 秒自动提取一次 sessionKey，有变化时自动入库。"}
              {!window.isSecureContext && (
                <span className="mt-1 block text-warning">
                  当前通过 HTTP 访问面板，接管画面需要 HTTPS：请改用 https:// 地址打开面板。
                </span>
              )}
            </Callout>
          </m.div>
        )}
      </AnimatePresence>

      <AndroidCallout android={android} />

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center gap-2 border-b p-3">
          <InputGroup className="min-w-56 flex-1">
            <InputGroupAddon>
              <Search />
            </InputGroupAddon>
            <InputGroupInput
              value={query}
              placeholder="搜索邮箱 / 备注 / 代理名称 / sessionKey / 订阅"
              spellCheck={false}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <InputGroupAddon align="inline-end">
                <InputGroupButton size="icon-xs" aria-label="清除搜索" onClick={() => setQuery("")}>
                  <X />
                </InputGroupButton>
              </InputGroupAddon>
            )}
          </InputGroup>
          <OptionSelect
            aria-label="状态筛选"
            className={cn("w-full sm:w-44", filter !== "all" && "border-primary/60")}
            prefix={<ListFilter />}
            align="end"
            value={filter}
            options={filterOptions}
            onValueChange={setFilter}
          />
          <OptionSelect
            aria-label="订阅筛选"
            className={cn("w-full sm:w-48", planFilter !== "all" && "border-primary/60")}
            prefix={<Crown />}
            align="end"
            value={planFilter}
            options={planOptions}
            onValueChange={setPlanFilter}
          />
          {filtered && (
            <Button variant="ghost" onClick={clearFilters}>
              <X /> 清除筛选
            </Button>
          )}
        </div>

        {!loaded ? (
          <div className="divide-y">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="flex items-center gap-6 px-4 py-3.5">
                <Skeleton className="size-4" />
                <Skeleton className="h-3.5 w-56" />
                <Skeleton className="h-5 w-16 rounded-full" />
                <Skeleton className="h-5 w-24 rounded-full max-md:hidden" />
                <Skeleton className="h-5 w-24 rounded-full max-lg:hidden" />
                <Skeleton className="ml-auto h-6 w-40" />
              </div>
            ))}
          </div>
        ) : accounts.length === 0 ? (
          <Empty className="py-14">
            <EmptyHeader>
              <EmptyMedia variant="icon"><Users /></EmptyMedia>
              <EmptyTitle>还没有账号</EmptyTitle>
              <EmptyDescription>可以手动录入、导入 sessionKey，或在注册任务页自动注册。</EmptyDescription>
            </EmptyHeader>
            <EmptyContent className="flex-row justify-center">
              <Button variant="outline" onClick={() => setImportOpen(true)}><Upload /> 导入 SK</Button>
              <Button onClick={() => setCreateOpen(true)}><Plus /> 新增账号</Button>
            </EmptyContent>
          </Empty>
        ) : visible.length === 0 ? (
          <Empty className="py-14">
            <EmptyHeader>
              <EmptyMedia variant="icon"><SearchX /></EmptyMedia>
              <EmptyTitle>没有符合条件的账号</EmptyTitle>
              <EmptyDescription>换个关键词，或清除筛选条件后再看看。</EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Button variant="outline" onClick={clearFilters}>
                清除筛选
              </Button>
            </EmptyContent>
          </Empty>
        ) : (
          <Table>
            <TableHeader className="bg-muted/30 text-xs [&_th]:font-medium [&_th]:text-muted-foreground">
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-10 pl-4">
                  <Checkbox
                    checked={allVisibleSelected ? true : selectedVisible.length > 0 ? "indeterminate" : false}
                    onCheckedChange={toggleSelectAll}
                    aria-label="全选当前列表"
                  />
                </TableHead>
                <SortHead label="账号" sortKey="email" sort={sort} onSort={toggleSort} />
                <SortHead label="订阅" sortKey="plan" sort={sort} onSort={toggleSort} />
                <SortHead label="额度" sortKey="usage" sort={sort} onSort={toggleSort} />
                <SortHead label="存活检测" sortKey="check" sort={sort} onSort={toggleSort} />
                <TableHead>代理</TableHead>
                <TableHead className="sticky right-0 z-10 bg-card bg-linear-to-r from-muted/30 to-muted/30 pr-4 text-right">
                  操作
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((a, i) => {
                const isEditing = editingEmail === a.email;
                const isSelected = selected.has(a.email);
                const act = androidAction(a);
                const noSk = a.session_key ? null : "该账号没有 sessionKey";
                return (
                  <m.tr
                    key={a.email}
                    data-slot="table-row"
                    data-state={isSelected ? "selected" : undefined}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.2, delay: Math.min(i, 12) * 0.015 }}
                    className={cn("border-b", ROW_BG)}
                  >
                    <TableCell className="pl-4">
                      <Checkbox
                        aria-label={`选择 ${a.email}`}
                        checked={isSelected}
                        onCheckedChange={() => toggleSelect(a.email)}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex max-w-80 min-w-0 flex-col items-start gap-0.5">
                        <div className="flex max-w-full min-w-0 items-center gap-1.5">
                          <button
                            type="button"
                            className="min-w-0 truncate rounded-sm text-left font-medium outline-none hover:text-primary focus-visible:ring-3 focus-visible:ring-ring/50"
                            title={`${a.email}（点击编辑）`}
                            onClick={() => startEdit(a)}
                          >
                            {a.email}
                          </button>
                          {/* 注册状态只在非成功时提示 */}
                          {a.status !== "success" ? <StatusBadge status={a.status} /> : null}
                        </div>
                        {a.display_name || a.session_key ? (
                          <div className="flex max-w-full min-w-0 items-center gap-2 text-xs text-muted-foreground">
                            {a.display_name ? (
                              <span className="truncate" title={a.display_name}>{a.display_name}</span>
                            ) : null}
                            {a.session_key ? (
                              <span className="shrink-0 font-mono text-[11px] opacity-70" title="sessionKey">
                                {String(a.session_key).slice(7, 17)}…
                              </span>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell><PlanBadge acct={a} /></TableCell>
                    <TableCell><UsageCell acct={a} /></TableCell>
                    <TableCell>
                      <LiveBadge status={a.check_status} checkedAt={a.checked_at} detail={a.check_detail} />
                    </TableCell>
                    <TableCell><ProxyBadge acct={a} proxyById={proxyById} /></TableCell>
                    <TableCell className={cn("sticky right-0 z-10 pr-4", STICKY_BG)}>
                      <div className="flex items-center justify-end gap-0.5">
                        <TipButton variant="ghost" size="icon-sm"
                          tip={noSk || "检测 sessionKey 是否有效（顺带识别套餐、刷新额度）"}
                          onClick={() => doCheck(a.email)} disabled={!!noSk || checking === a.email}>
                          {checking === a.email ? <Spinner /> : <HeartPulse />}
                        </TipButton>
                        <TipButton
                          variant="ghost"
                          size="icon-sm"
                          tip={noSk || (a.access_token
                            ? "重新授权 OAuth（属于多个组织时可选择工作空间）"
                            : "用 sessionKey 获取 OAuth 令牌（属于多个组织时可选择工作空间）")}
                          onClick={() => runOAuth(a.email, { pickOrg: true })}
                          disabled={!!noSk || authorizing.has(a.email)}
                        >
                          {authorizing.has(a.email) ? <Spinner /> : <KeyRound />}
                        </TipButton>
                        <TipButton variant="ghost" size="icon-sm" tip={noSk || "接管浏览器"}
                          className="text-info hover:text-info" disabled={!!noSk}
                          onClick={() => startTakeover(a.email)}>
                          <MonitorPlay />
                        </TipButton>
                        <TipButton
                          variant={act.active ? "secondary" : "ghost"}
                          size="icon-sm"
                          tip={act.tip}
                          className={cn(act.active && "text-success hover:text-success")}
                          disabled={act.disabled}
                          onClick={act.onClick}
                        >
                          <Smartphone />
                        </TipButton>
                        <TipButton variant="ghost" size="icon-sm" tip={copiedEmail === a.email ? "已复制" : "复制账号信息"}
                          onClick={() => copyLine(a)}>
                          {copiedEmail === a.email ? <Check className="text-success" /> : <Copy />}
                        </TipButton>
                        <TipButton
                          variant={isEditing ? "secondary" : "ghost"}
                          size="icon-sm"
                          tip="编辑"
                          onClick={() => startEdit(a)}
                        >
                          <Pencil />
                        </TipButton>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon-sm" aria-label={`${a.email} 的更多操作`}>
                              <MoreHorizontal />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-44">
                            <DropdownMenuItem disabled={running} onSelect={() => doRerun(a.email)}>
                              <RotateCcw /> 重跑注册
                            </DropdownMenuItem>
                            <DropdownMenuItem onSelect={() => toggleClaimed(a)}>
                              <PackageCheck /> {a.claimed_at ? "取消已获取" : "标记已获取"}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive" onSelect={() => setDeleteTargets([a.email])}>
                              <Trash2 /> 删除账号
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </TableCell>
                  </m.tr>
                );
              })}
            </TableBody>
          </Table>
        )}

        {loaded && accounts.length > 0 && (
          <div className="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-1 border-t bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
            <span className="tabular-nums">
              {visible.length === accounts.length
                ? `共 ${accounts.length} 个账号`
                : `显示 ${visible.length} / ${accounts.length} 个账号`}
              {selectedVisible.length > 0 ? `，已选 ${selectedVisible.length} 个` : ""}
            </span>
            {sort.key && (
              <button
                type="button"
                className="ml-auto rounded-sm underline-offset-4 outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                onClick={() => setSort({ key: "", dir: "desc" })}
              >
                恢复默认排序
              </button>
            )}
          </div>
        )}
      </Card>

      {/* 批量操作栏：选中账号后吸底浮现 */}
      <AnimatePresence>
        {selectedVisible.length > 0 && (
          <m.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="pointer-events-none sticky bottom-4 z-20 flex justify-center"
          >
            <div className="pointer-events-auto flex flex-wrap items-center gap-1.5 rounded-xl bg-popover/90 p-1.5 pl-4 shadow-xl ring-1 ring-foreground/10 backdrop-blur-md">
              <span className="text-sm font-medium tabular-nums">已选 {selectedVisible.length} 个</span>
              <Separator orientation="vertical" className="mx-1.5 data-vertical:h-5 data-vertical:self-center" />
              <TipButton
                variant="ghost"
                size="sm"
                disabled={!!batchCheck || !withSk.length}
                tip="检测所选账号的 sessionKey 是否有效，并识别套餐、刷新额度（没有 sessionKey 的跳过）"
                onClick={() => runBatchCheck(withSk.map((a) => a.email))}
              >
                {batchCheck ? <Spinner /> : <HeartPulse />}
                {batchCheck ? `检测中 ${batchCheck.done}/${batchCheck.total}…` : `检测（${withSk.length}）`}
              </TipButton>
              <TipButton
                variant="ghost"
                size="sm"
                disabled={!!batchOAuth || !withSk.length}
                tip="用 sessionKey 为所选账号获取 Claude OAuth 令牌（没有 sessionKey 的跳过）"
                onClick={() => runBatchOAuth(withSk.map((a) => a.email))}
              >
                {batchOAuth ? <Spinner /> : <KeyRound />}
                {batchOAuth ? `授权中 ${batchOAuth.done}/${batchOAuth.total}…` : `获取 OAuth（${withSk.length}）`}
              </TipButton>
              <TipButton
                variant="ghost"
                size="sm"
                tip="为所选账号统一更换代理池中的代理"
                onClick={() =>
                  setBatchProxy({
                    emails: selectedVisible.map((a) => a.email),
                    proxyId: "",
                    busy: false,
                  })
                }
              >
                <Globe /> 设置代理
              </TipButton>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setDeleteTargets(selectedVisible.map((a) => a.email))}
              >
                <Trash2 /> 删除
              </Button>
              <TipButton variant="ghost" size="icon-sm" tip="取消选择" onClick={() => setSelected(new Set())}>
                <X />
              </TipButton>
            </div>
          </m.div>
        )}
      </AnimatePresence>

      {/* 编辑账号：右侧抽屉 */}
      <Sheet open={!!editingAcct} onOpenChange={(o) => !o && !saving && setEditingEmail("")}>
        <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-xl">
          {sheetAcct && (
            <>
              <SheetHeader className="border-b pr-12">
                <SheetTitle className="flex items-center gap-2">
                  <Pencil className="size-4 text-primary" /> 编辑账号
                </SheetTitle>
                <SheetDescription className="truncate" title={sheetAcct.email}>
                  {sheetAcct.email}
                </SheetDescription>
                <div className="flex flex-wrap items-center gap-1.5 pt-2 text-xs">
                  {sheetAcct.plan ? <PlanBadge acct={sheetAcct} /> : null}
                  <StatusBadge status={sheetAcct.status} />
                  <LiveBadge status={sheetAcct.check_status} checkedAt={sheetAcct.checked_at}
                    detail={sheetAcct.check_detail} />
                  {sheetAcct.access_token ? <OAuthBadge acct={sheetAcct} /> : null}
                  {sheetAcct.claimed_at ? <ClaimedBadge acct={sheetAcct} /> : null}
                </div>
              </SheetHeader>
              <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
                <AccountFields
                  form={editForm}
                  setForm={setEditForm}
                  disabled={saving}
                  idPrefix="edit"
                  proxies={proxies}
                  legacyProxy={sheetAcct.proxy || ""}
                />
                <UsageDetail acct={sheetAcct} />
                {sheetAcct.access_token && (
                  <div className="rounded-lg bg-background p-3 text-xs ring-1 ring-foreground/10">
                    <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
                      <span className="flex items-center gap-1.5 font-medium text-foreground">
                        <KeyRound className="size-3.5 text-info" /> OAuth 令牌
                      </span>
                      <span>授权于 {sheetAcct.oauth_at || "未知"}</span>
                      <span>· access_token 过期 {sheetAcct.oauth_expires_at || "未知"}</span>
                    </div>
                    <div className="divide-y divide-dashed">
                      {OAUTH_COPY_FIELDS.filter(([k]) => sheetAcct[k]).map(([k, label]) => {
                        const key = `${sheetAcct.email}:${k}`;
                        return (
                          <div key={k} className="flex items-center gap-2 py-1">
                            <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
                            <span className="min-w-0 flex-1 truncate font-mono">
                              {k.endsWith("_token") ? `${String(sheetAcct[k]).slice(0, 18)}…` : sheetAcct[k]}
                            </span>
                            <TipButton
                              variant="ghost"
                              size="icon-xs"
                              tip={copiedEmail === key ? "已复制" : `复制 ${label}`}
                              onClick={() => copyText(String(sheetAcct[k]), key)}
                            >
                              {copiedEmail === key ? <Check className="text-success" /> : <Copy />}
                            </TipButton>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
              <SheetFooter className="flex-row flex-wrap items-center border-t">
                <Button onClick={saveEdit} disabled={saving || !editingAcct}>
                  {saving ? <Spinner /> : <Check />}
                  {saving ? "保存中…" : "保存"}
                </Button>
                <Button variant="outline" onClick={() => setEditingEmail("")} disabled={saving}>
                  取消
                </Button>
                <span className="flex-1" />
                <Button variant="outline" onClick={() => toggleClaimed(sheetAcct)} disabled={saving}>
                  <PackageCheck /> {sheetAcct.claimed_at ? "取消已获取" : "标记已获取"}
                </Button>
                <Button variant="destructive" onClick={() => setDeleteTargets([sheetAcct.email])} disabled={saving}>
                  <Trash2 /> 删除
                </Button>
              </SheetFooter>
            </>
          )}
        </SheetContent>
      </Sheet>
      <AlertDialog
        open={deleteTargets.length > 0}
        onOpenChange={(o) => !o && setDeleteTargets([])}
      >
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogMedia className="bg-destructive/10 text-destructive">
              <Trash2 />
            </AlertDialogMedia>
            <AlertDialogTitle>删除账号？</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTargets.length === 1
                ? `确认删除账号 ${deleteTargets[0]}？此操作不可恢复。`
                : `确认删除所选的 ${deleteTargets.length} 个账号？此操作不可恢复。`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmDelete}>
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertDialog
        open={!!batchProxy}
        onOpenChange={(o) => !o && !batchProxy?.busy && setBatchProxy(null)}
      >
        <AlertDialogContent className="sm:max-w-md">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Globe className="size-4 text-primary" /> 批量设置代理
            </AlertDialogTitle>
            <AlertDialogDescription>
              为所选的 {batchProxy?.emails.length || 0} 个账号更换代理。账号代理只能从代理池中选择。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ProxySelect
            id="batch-proxy"
            aria-label="代理"
            value={batchProxy?.proxyId ?? ""}
            proxies={proxies}
            disabled={batchProxy?.busy}
            onChange={(v) => setBatchProxy((s) => ({ ...s, proxyId: v }))}
          />
          <AlertDialogFooter>
            <AlertDialogCancel disabled={batchProxy?.busy}>取消</AlertDialogCancel>
            <Button onClick={confirmBatchProxy} disabled={batchProxy?.busy}>
              {batchProxy?.busy ? <Spinner /> : <Globe />} {batchProxy?.busy ? "保存中…" : "确定"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ExportDialog open={exportOpen} onOpenChange={setExportOpen} />
      <AccountCreateDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={refreshLists} />
      <ManualLoginDialog
        open={manualOpen}
        onOpenChange={setManualOpen}
        onStarted={() => api.takeoverStatus().then(setTakeover).catch(() => {})}
      />
      <ImportDialog open={importOpen} onOpenChange={setImportOpen} onImported={refreshLists} />
      <AndroidInstallDialog
        open={androidInstallOpen}
        onOpenChange={setAndroidInstallOpen}
        android={android}
      />
      {orgPicker.dialog}
    </div>
  );
}
