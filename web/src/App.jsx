import { useEffect, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import {
  BookOpen,
  ChevronRight,
  ChevronsUpDown,
  Globe,
  KeyRound,
  LogOut,
  Play,
  Server,
  Settings as SettingsIcon,
  ShieldCheck,
  TriangleAlert,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import { useTheme } from "next-themes";
import { api, SESSION_EXPIRED_EVENT } from "./api.js";
import { useRunStream } from "./hooks/useRunStream.js";
import { Toaster } from "@/components/ui/sonner";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { BrandMark } from "@/components/brand";
import { Callout } from "@/components/callout";
import { THEMES, ThemeToggle } from "@/components/theme";
import Login from "./pages/Login.jsx";
import Register from "./pages/Register.jsx";
import Accounts from "./pages/Accounts.jsx";
import Nodes from "./pages/Nodes.jsx";
import Proxies from "./pages/Proxies.jsx";
import Settings from "./pages/Settings.jsx";
import Sub2API from "./pages/Sub2API.jsx";

const NAV_GROUPS = [
  {
    label: "工作台",
    items: [
      { key: "register", label: "注册任务", Icon: Play },
      { key: "accounts", label: "账号管理", Icon: Users },
    ],
  },
  {
    label: "集成",
    items: [
      { key: "sub2api", label: "sub2api 同步", Icon: KeyRound },
      { key: "proxies", label: "代理池", Icon: Globe },
      { key: "nodes", label: "节点设置", Icon: Server },
    ],
  },
  {
    label: "系统",
    items: [{ key: "settings", label: "系统设置", Icon: SettingsIcon }],
  },
];

const NAV = NAV_GROUPS.flatMap((g) => g.items.map((item) => ({ ...item, group: g.label })));
const VIEW_KEYS = NAV.map((n) => n.key);

function viewFromHash() {
  const v = window.location.hash.replace(/^#\/?/, "");
  return VIEW_KEYS.includes(v) ? v : "register";
}

export default function App() {
  // Toaster 只挂一个、放在所有分支之外：登录态切换时若跟着分支卸载/重挂，
  // 切换瞬间发出的提示（如「登录已失效」）会丢。
  return (
    <>
      <AppBody />
      <Toaster position="top-right" />
    </>
  );
}

function AppBody() {
  const [authed, setAuthed] = useState(null); // null=checking, false=need login, true=ok
  const authedRef = useRef(authed);
  authedRef.current = authed;
  // 引导态：服务端还没设面板密码，只能去设置页设密码，其余接口全 401。
  const [bootstrap, setBootstrap] = useState(false);
  const [view, setView] = useState(viewFromHash);
  const runStream = useRunStream();

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        // 引导态下 /api/config 也返回 200，必须看 panel_password 才能区分
        // 「已登录」和「未设密码」，否则会直接进一个处处 401 的空面板。
        setBootstrap(!cfg.panel_password);
        setAuthed(true);
      })
      .catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    const onExpired = () => {
      // 会话失效（密码被改/cookie 过期）→ 立即回登录页，而不是停在空页面。
      // 副作用不放进 setState 更新函数（StrictMode 下会被调两次）；
      // 并发多个 401 时用固定 id 去重，只弹一条。
      if (authedRef.current === true) {
        toast.error("登录已失效，请重新登录", { id: "session-expired" });
      }
      setAuthed(false);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  useEffect(() => {
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function navigate(next) {
    window.location.hash = `#/${next}`;
  }

  async function logout() {
    try {
      await api.logout();
    } catch {
      /* 登出失败也要回登录页 */
    }
    setAuthed(false);
    setBootstrap(false);
  }

  if (authed === null) {
    return (
      <div className="flex min-h-svh flex-col items-center justify-center gap-4 bg-background">
        <BrandMark className="size-11 rounded-xl" iconClassName="size-5 animate-pulse" />
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> 正在加载面板…
        </div>
      </div>
    );
  }

  if (!authed) {
    return (
      <Login
        onOk={() => {
          setBootstrap(false);
          setAuthed(true);
        }}
      />
    );
  }

  // 引导态：只展示设置页，引导用户先把面板密码设上。
  if (bootstrap) {
    return (
      <div className="min-h-svh bg-background">
        <header className="sticky top-0 z-30 border-b bg-background/75 backdrop-blur-lg">
          <div className="mx-auto flex h-14 max-w-5xl items-center gap-3 px-4 md:px-8">
            <BrandMark className="size-7" iconClassName="size-3.5" />
            <span className="flex-1 font-semibold tracking-tight">Account Manager</span>
            <ThemeToggle />
          </div>
        </header>
        <div className="relative">
          <div aria-hidden="true" className="page-glow pointer-events-none absolute inset-x-0 top-0 h-72" />
          <div className="relative mx-auto w-full max-w-5xl space-y-6 px-4 py-8 md:px-8">
            <Callout tone="warning" icon={TriangleAlert} title="面板尚未设置密码">
              {"未设密码时任何人都能打开本页并接管面板，注册、账号、代理等功能均已锁定。"
                + "请先设定「面板密码」并保存，保存后需用新密码登录。"}
            </Callout>
            <Settings
              onPasswordSet={() => {
                // 密码一旦设定，旧的引导态会话立即失效 → 回登录页。
                setBootstrap(false);
                setAuthed(false);
              }}
            />
          </div>
        </div>
      </div>
    );
  }

  const current = NAV.find((n) => n.key === view);
  const running = runStream.activeStatus === "running";
  return (
    <SidebarProvider>
      <Sidebar variant="inset" collapsible="icon">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" tooltip="Account Manager" onClick={() => navigate("register")}>
                <BrandMark />
                <span className="grid min-w-0 flex-1 text-left leading-tight">
                  <span className="truncate font-semibold tracking-tight text-sidebar-accent-foreground">
                    Account Manager
                  </span>
                  <span className="truncate text-xs text-sidebar-foreground/60">自动化注册工作台</span>
                </span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <NavMain view={view} running={running} navigate={navigate} />
        </SidebarContent>
        <SidebarFooter>
          <NavUser onLogout={logout} />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset className="isolate min-w-0 overflow-x-clip md:ring-1 md:ring-foreground/6">
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b bg-background/75 px-3 backdrop-blur-lg md:rounded-t-xl md:px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mx-1 data-vertical:h-4 data-vertical:self-center" />
          <nav aria-label="当前位置" className="flex min-w-0 flex-1 items-center gap-1.5 text-sm">
            <span className="hidden text-muted-foreground sm:inline">{current?.group}</span>
            <ChevronRight className="hidden size-3.5 text-muted-foreground/60 sm:inline" />
            <span className="truncate font-medium">{current?.label}</span>
          </nav>
          {running && view !== "register" && (
            <Button variant="outline" size="sm" onClick={() => navigate("register")}>
              <span className="size-1.5 animate-pulse rounded-full bg-success" />
              注册运行中
            </Button>
          )}
          <ThemeToggle />
        </header>
        <div className="relative flex flex-1 flex-col">
          <div aria-hidden="true" className="page-glow pointer-events-none absolute inset-x-0 top-0 h-80" />
          <AnimatePresence mode="wait">
            <m.main
              key={view}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0, transition: { duration: 0.22, ease: "easeOut" } }}
              exit={{ opacity: 0, y: -4, transition: { duration: 0.1, ease: "easeIn" } }}
              className="relative mx-auto w-full max-w-7xl min-w-0 flex-1 px-4 py-6 md:px-8 md:py-8"
            >
              {view === "register" && <Register runStream={runStream} />}
              {view === "accounts" && (
                <Accounts attach={runStream.attach} running={running} navigate={navigate} />
              )}
              {view === "sub2api" && <Sub2API navigate={navigate} />}
              {view === "proxies" && <Proxies navigate={navigate} />}
              {view === "nodes" && <Nodes />}
              {view === "settings" && <Settings />}
            </m.main>
          </AnimatePresence>
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}

// 侧栏导航；手机上侧栏是抽屉，点选后自动收起
function NavMain({ view, running, navigate }) {
  const { isMobile, setOpenMobile } = useSidebar();
  return NAV_GROUPS.map((group) => (
    <SidebarGroup key={group.label}>
      <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
      <SidebarMenu>
        {group.items.map(({ key, label, Icon }) => (
          <SidebarMenuItem key={key}>
            <SidebarMenuButton
              isActive={view === key}
              tooltip={label}
              onClick={() => {
                navigate(key);
                if (isMobile) setOpenMobile(false);
              }}
              className="data-active:[&>svg]:text-primary"
            >
              <Icon />
              <span>{label}</span>
            </SidebarMenuButton>
            {key === "register" && running && (
              <SidebarMenuBadge>
                <span className="relative flex size-2">
                  <span className="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-60" />
                  <span className="relative inline-flex size-2 rounded-full bg-success" />
                </span>
              </SidebarMenuBadge>
            )}
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  ));
}

// 侧栏底部：管理员菜单（外观、接口文档、退出登录）
function NavUser({ onLogout }) {
  const { isMobile } = useSidebar();
  const { theme, setTheme } = useTheme();
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton size="lg" className="data-open:bg-sidebar-accent">
              <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-accent text-sidebar-accent-foreground ring-1 ring-sidebar-border">
                <ShieldCheck className="size-4" />
              </span>
              <span className="grid min-w-0 flex-1 text-left leading-tight">
                <span className="truncate text-sm font-medium text-sidebar-accent-foreground">管理员</span>
                <span className="truncate text-xs text-sidebar-foreground/60">已登录</span>
              </span>
              <ChevronsUpDown className="ml-auto size-4 text-sidebar-foreground/50" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            side={isMobile ? "bottom" : "right"}
            align="end"
            sideOffset={6}
            className="w-56"
          >
            <DropdownMenuLabel>外观</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={theme} onValueChange={setTheme}>
              {THEMES.map(([value, label, Icon]) => (
                <DropdownMenuRadioItem key={value} value={value}>
                  <Icon /> {label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem asChild>
                <a href="/api/v1/docs" target="_blank" rel="noopener noreferrer">
                  <BookOpen /> 开放 API 文档
                </a>
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={onLogout}>
              <LogOut /> 退出登录
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
