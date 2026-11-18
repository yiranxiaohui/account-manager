import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { toast } from "sonner";
import {
  Building2,
  Check,
  ChevronUp,
  Copy,
  Crown,
  Download,
  ExternalLink,
  Globe,
  HeartPulse,
  KeyRound,
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
import { matchPlan, otherPlanCount, planLabel, planTone, planTooltip } from "@/lib/plan";
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
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
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

const FILTERS = [
  ["all", "全部"],
  ["alive", "有效"],
  ["dead", "失效"],
  ["blocked", "被拦截"],
  ["unchecked", "未检测"],
  ["claimed", "已获取"],
  ["unclaimed", "未获取"],
  ["no_sk", "无 sessionKey"],
  ["oauth", "已授权 OAuth"],
  ["no_oauth", "未授权 OAuth"],
  ["plan_max", "套餐：Max"],
  ["plan_pro", "套餐：Pro"],
  ["plan_team", "套餐：Team / Enterprise"],
  ["plan_free", "套餐：Free"],
  ["plan_none", "套餐：未识别"],
];

function matchFilter(a, filter) {
  if (filter.startsWith("plan_")) return matchPlan(a.plan, filter.slice(5));
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

const AVATAR_TONES = [
  "bg-primary/14 text-primary",
  "bg-info/14 text-info",
  "bg-success/14 text-success",
  "bg-warning/16 text-warning",
  "bg-highlight/14 text-highlight",
];

function Avatar({ email }) {
  const text = String(email || "?");
  const hash = [...text].reduce((n, c) => (n * 31 + c.charCodeAt(0)) >>> 0, 7);
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-flex size-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold uppercase max-sm:hidden",
        AVATAR_TONES[hash % AVATAR_TONES.length],
      )}
    >
      {text[0]}
    </span>
  );
}

// 账号绑定的代理：显示代理池条目名称；地址不在代理池时（无法迁移的旧数据）黄色提示
function ProxyBadge({ acct, proxyById }) {
  if (!acct.proxy) return null;
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
      className="max-w-48 font-normal text-muted-foreground"
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
  if (!status) return null;
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
  if (!acct.plan) return null;
  const Icon = PLAN_ICON[acct.plan];
  const others = otherPlanCount(acct);
  return (
    <Pill tone={planTone(acct.plan)} title={planTooltip(acct)} className="shrink-0 self-center">
      {Icon ? <Icon /> : null}
      {planLabel(acct.plan)}
      {others ? <span className="font-normal opacity-70">+{others}</span> : null}
    </Pill>
  );
}

function OAuthBadge({ acct }) {
  if (!acct.access_token) return null;
  const expired = acct.oauth_expires_at && new Date(acct.oauth_expires_at).getTime() <= Date.now();
  return (
    <Pill
      tone={expired ? "muted" : "info"}
      title={`OAuth 授权时间：${acct.oauth_at || "未知"}\naccess_token 过期：${acct.oauth_expires_at || "未知"}${
        acct.org_name ? `\n组织：${acct.org_name}` : ""
      }`}
    >
      <KeyRound />
      OAuth
      {acct.oauth_expires_at ? (
        <span className="font-normal opacity-70">· {untilTime(acct.oauth_expires_at)}</span>
      ) : null}
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
    return accounts.filter((a) => matchFilter(a, filter) && matchQuery(a, q, proxyById));
  }, [accounts, query, filter, proxyById]);
  const selectedVisible = visible.filter((a) => selected.has(a.email));
  const allVisibleSelected = visible.length > 0 && selectedVisible.length === visible.length;

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
        toast.success(`「${acctEmail}」${LIVE_LABEL[res.status] || res.status}，套餐 ${planLabel(res.plan)}`);
      }
    } catch (e) {
      toast.error(`「${acctEmail}」检测失败（${e.status || "?"}）`);
    } finally {
      setChecking("");
    }
  }

  // 检测一个账号并合并结果（存活状态 + 识别出的套餐），返回接口结果
  async function checkOne(acctEmail) {
    const res = await api.checkAccount(acctEmail);
    const { status, detail, ...rest } = res;
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

  return (
    <div className="space-y-6">
      <PageHeader
        icon={Users}
        title="账号管理"
        description="查看账号的注册、存活与 OAuth 状态，管理 sessionKey、代理和浏览器接管。"
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
          onClick={() => setFilter("all")} />
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

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center gap-2 border-b p-3">
          <InputGroup className="min-w-56 flex-1">
            <InputGroupAddon>
              <Search />
            </InputGroupAddon>
            <InputGroupInput
              value={query}
              placeholder="搜索邮箱 / 备注 / 代理名称 / sessionKey"
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
          <NativeSelect aria-label="筛选" value={filter} onChange={(e) => setFilter(e.target.value)}>
            {FILTERS.map(([v, label]) => (
              <NativeSelectOption key={v} value={v}>{label}</NativeSelectOption>
            ))}
          </NativeSelect>
        </div>

        <div className="flex h-10 items-center gap-3 border-b bg-muted/30 px-4 text-xs text-muted-foreground">
          <label className="flex cursor-pointer items-center gap-2.5 select-none">
            <Checkbox
              checked={allVisibleSelected ? true : selectedVisible.length > 0 ? "indeterminate" : false}
              disabled={visible.length === 0}
              onCheckedChange={toggleSelectAll}
              aria-label="全选"
            />
            全选
          </label>
          <span className="tabular-nums">
            {visible.length === accounts.length
              ? `共 ${accounts.length} 个账号`
              : `显示 ${visible.length} / ${accounts.length} 个`}
            {selectedVisible.length > 0 ? `，已选 ${selectedVisible.length} 个` : ""}
          </span>
        </div>

        {!loaded ? (
          <div className="divide-y">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-3.5">
                <Skeleton className="size-4" />
                <Skeleton className="size-9 rounded-full" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-3.5 w-56" />
                  <Skeleton className="h-3 w-80 max-w-full" />
                </div>
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
              <Button variant="outline" onClick={() => { setQuery(""); setFilter("all"); }}>
                清除筛选
              </Button>
            </EmptyContent>
          </Empty>
        ) : (
          <ul className="divide-y">
            {visible.map((a, i) => {
              const isEditing = editingEmail === a.email;
              const isSelected = selected.has(a.email);
              return (
                <m.li
                  key={a.email}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.2, delay: Math.min(i, 12) * 0.015 }}
                  className={cn("transition-colors", isSelected && "bg-primary/5", isEditing && "bg-muted/30")}
                >
                  <div className="flex items-center gap-3 px-4 py-3 max-md:flex-wrap">
                    <Checkbox
                      aria-label={`选择 ${a.email}`}
                      checked={isSelected}
                      onCheckedChange={() => toggleSelect(a.email)}
                    />
                    <Avatar email={a.email} />
                    {/* 窄屏时信息区占满一行，操作按钮换到下一行 */}
                    <div className="flex min-w-0 flex-1 flex-col gap-1.5 max-md:basis-[calc(100%-1.75rem)]">
                      <div className="flex min-w-0 items-baseline gap-2">
                        <span className="truncate text-sm font-medium" title={a.email}>{a.email}</span>
                        <PlanBadge acct={a} />
                        {a.display_name ? (
                          <span className="shrink-0 truncate text-xs text-muted-foreground">{a.display_name}</span>
                        ) : null}
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                        <StatusBadge status={a.status} />
                        <LiveBadge status={a.check_status} checkedAt={a.checked_at} detail={a.check_detail} />
                        {a.claimed_at ? (
                          <Pill tone="highlight" title={`获取时间：${a.claimed_at}`}>
                            <PackageCheck />
                            已获取
                            <span className="font-normal opacity-70">· {relTime(a.claimed_at)}</span>
                          </Pill>
                        ) : null}
                        <OAuthBadge acct={a} />
                        <ProxyBadge acct={a} proxyById={proxyById} />
                        {a.session_key ? (
                          <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                            sk {String(a.session_key).slice(7, 17)}…
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-0.5 max-md:ml-auto">
                      {a.session_key && (
                        <TipButton variant="ghost" size="icon-sm" tip="检测 sessionKey 是否有效（顺带识别套餐）"
                          onClick={() => doCheck(a.email)} disabled={checking === a.email}>
                          {checking === a.email ? <Spinner /> : <HeartPulse />}
                        </TipButton>
                      )}
                      {a.session_key && (
                        <TipButton
                          variant="ghost"
                          size="icon-sm"
                          tip={a.access_token
                            ? "重新授权 OAuth（属于多个组织时可选择工作空间）"
                            : "用 sessionKey 获取 OAuth 令牌（属于多个组织时可选择工作空间）"}
                          onClick={() => runOAuth(a.email, { pickOrg: true })}
                          disabled={authorizing.has(a.email)}
                        >
                          {authorizing.has(a.email) ? <Spinner /> : <KeyRound />}
                        </TipButton>
                      )}
                      {a.session_key && (
                        <TipButton variant="ghost" size="icon-sm" tip="接管浏览器" className="text-info hover:text-info"
                          onClick={() => startTakeover(a.email)}>
                          <MonitorPlay />
                        </TipButton>
                      )}
                      {/* 没有 sessionKey 的行补占位，让各行操作按钮列对齐 */}
                      {!a.session_key && <span aria-hidden="true" className="w-[5.5rem] shrink-0 max-md:hidden" />}
                      <TipButton variant="ghost" size="icon-sm" tip={copiedEmail === a.email ? "已复制" : "复制账号信息"}
                        onClick={() => copyLine(a)}>
                        {copiedEmail === a.email ? <Check className="text-success" /> : <Copy />}
                      </TipButton>
                      <TipButton
                        variant={isEditing ? "secondary" : "ghost"}
                        size="icon-sm"
                        tip={isEditing ? "收起" : "编辑"}
                        onClick={() => (isEditing ? setEditingEmail("") : startEdit(a))}
                      >
                        {isEditing ? <ChevronUp /> : <Pencil />}
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
                  </div>
                  <AnimatePresence initial={false}>
                    {isEditing && (
                      <m.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.2, ease: "easeOut" }}
                        className="overflow-hidden"
                      >
                        <div className="flex flex-col gap-4 border-t border-dashed px-4 py-4 md:pl-[4.75rem]">
                          <AccountFields
                            form={editForm}
                            setForm={setEditForm}
                            disabled={saving}
                            idPrefix={`edit-${a.email}`}
                            proxies={proxies}
                            legacyProxy={a.proxy || ""}
                          />
                          {a.access_token && (
                            <div className="rounded-lg bg-background p-3 text-xs ring-1 ring-foreground/10">
                              <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
                                <span className="flex items-center gap-1.5 font-medium text-foreground">
                                  <KeyRound className="size-3.5 text-info" /> OAuth 令牌
                                </span>
                                <span>授权于 {a.oauth_at || "未知"}</span>
                                <span>· access_token 过期 {a.oauth_expires_at || "未知"}</span>
                              </div>
                              <div className="divide-y divide-dashed">
                                {OAUTH_COPY_FIELDS.filter(([k]) => a[k]).map(([k, label]) => (
                                  <div key={k} className="flex items-center gap-2 py-1">
                                    <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
                                    <span className="min-w-0 flex-1 truncate font-mono">
                                      {k.endsWith("_token") ? `${String(a[k]).slice(0, 18)}…` : a[k]}
                                    </span>
                                    <TipButton
                                      variant="ghost"
                                      size="icon-xs"
                                      tip={copiedEmail === `${a.email}:${k}` ? "已复制" : `复制 ${label}`}
                                      onClick={() => copyText(String(a[k]), `${a.email}:${k}`)}
                                    >
                                      {copiedEmail === `${a.email}:${k}` ? <Check className="text-success" /> : <Copy />}
                                    </TipButton>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                          <div className="flex flex-wrap items-center gap-2">
                            <Button onClick={saveEdit} disabled={saving}>
                              {saving ? <Spinner /> : <Check />}
                              {saving ? "保存中…" : "保存"}
                            </Button>
                            <Button variant="outline" onClick={() => setEditingEmail("")}>
                              取消
                            </Button>
                            <span className="flex-1" />
                            <Button variant="outline" onClick={() => toggleClaimed(a)}>
                              <PackageCheck /> {a.claimed_at ? "取消已获取" : "标记已获取"}
                            </Button>
                            <Button variant="destructive" onClick={() => setDeleteTargets([a.email])}>
                              <Trash2 /> 删除账号
                            </Button>
                          </div>
                        </div>
                      </m.div>
                    )}
                  </AnimatePresence>
                </m.li>
              );
            })}
          </ul>
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
                tip="检测所选账号的 sessionKey 是否有效，并识别套餐（没有 sessionKey 的跳过）"
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
      {orgPicker.dialog}
    </div>
  );
}
