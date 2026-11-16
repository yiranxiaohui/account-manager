import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Bot,
  Braces,
  Check,
  CodeXml,
  Copy,
  ExternalLink,
  Eye,
  EyeOff,
  FileText,
  Mail,
  CirclePlay,
  Plug,
  RefreshCw,
  Settings as SettingsIcon,
  ShieldCheck,
  SlidersHorizontal,
  SquareTerminal,
} from "lucide-react";
import { api } from "../api.js";
import { PageHeader } from "@/components/page-header";
import { SaveBar } from "@/components/save-bar";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const GROUPS = [
  {
    title: "面板",
    description: "管理面板的访问密码与监听端口。",
    icon: ShieldCheck,
    fields: [
      { key: "panel_password", label: "面板密码", hint: "留空不修改；修改后需用新密码重新登录。", type: "password" },
      { key: "panel_port", label: "面板端口", type: "number" },
    ],
  },
  {
    title: "AnyMail 邮箱",
    description: "注册时用 AnyMail 创建邮箱并接收验证码。",
    icon: Mail,
    fields: [
      { key: "anymail_api_key", label: "AnyMail API Key", type: "text" },
      { key: "anymail_base_url", label: "AnyMail Base URL", type: "text" },
      { key: "anymail_domain", label: "AnyMail 域名", type: "text" },
      { key: "anymail_expires_hours", label: "邮箱有效期（小时）", hint: "0 表示永久。", type: "number" },
    ],
  },
  {
    title: "注册参数",
    description: "控制注册流程的超时、自动登录、验证码识别与代理。",
    icon: SlidersHorizontal,
    fields: [
      { key: "register_login_timeout", label: "登录超时（秒）", type: "number" },
      { key: "register_auto_login", label: "注册后自动登录", type: "checkbox" },
      {
        key: "oauth_auto_after_register",
        label: "注册成功后自动获取 Claude OAuth 令牌",
        type: "checkbox",
      },
      { key: "register_code_regex", label: "验证码正则", type: "text" },
      {
        key: "register_proxy",
        label: "注册代理",
        hint: "留空直连。",
        type: "text",
        placeholder: "http://user:pass@host:port 或 socks5://host:port",
      },
    ],
  },
  {
    title: "sub2api 对接",
    description: "用于账号同步与重新授权写回、代理导入，以及导出 sub2api 导入文件。",
    icon: Plug,
    fields: [
      {
        key: "sub2api_base_url",
        label: "sub2api 地址",
        type: "text",
        placeholder: "http://sub2api.syixn.com:8080",
      },
      {
        key: "sub2api_admin_key",
        label: "sub2api 管理员 API Key",
        hint: "留空不修改；在 sub2api 后台 → 系统设置里生成。",
        type: "password",
      },
    ],
  },
  {
    title: "开放 API",
    description: "供外部脚本触发注册、导出与领取账号、获取 OAuth 令牌。",
    icon: CodeXml,
    fields: [
      { key: "api_enabled", label: "启用开放 API（/api/v1/*）", type: "checkbox" },
      { key: "api_key", label: "API Key", type: "apikey" },
    ],
  },
];

function ApiKeyField({ value, onRotated }) {
  const [shown, setShown] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function rotate() {
    setBusy(true);
    try {
      const { api_key } = await api.rotateApiKey();
      onRotated(api_key);
      setShown(true);
      toast.success(value ? "已生成新 Key，旧 Key 立即失效" : "已生成 API Key");
    } catch (err) {
      if (err?.status !== 401) toast.error("生成失败，请重试");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("已复制");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 非 HTTPS 页面没有剪贴板权限：显示出来让用户手动复制
      setShown(true);
      toast.error("浏览器不允许自动复制，请手动选中复制");
    }
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor="api_key">API Key</Label>
      <div className="flex gap-2">
        <InputGroup>
          <InputGroupInput
            id="api_key"
            readOnly
            type={shown ? "text" : "password"}
            value={value || ""}
            placeholder="尚未生成"
            className="font-mono"
            onFocus={(e) => shown && e.target.select()}
          />
          {value && (
            <InputGroupAddon align="inline-end">
              <InputGroupButton size="icon-xs" aria-label={shown ? "隐藏" : "显示"} onClick={() => setShown((v) => !v)}>
                {shown ? <EyeOff /> : <Eye />}
              </InputGroupButton>
              <InputGroupButton size="icon-xs" aria-label="复制" onClick={copy}>
                {copied ? <Check className="text-success" /> : <Copy />}
              </InputGroupButton>
            </InputGroupAddon>
          )}
        </InputGroup>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => (value ? setConfirming(true) : rotate())}
        >
          <RefreshCw className={busy ? "animate-spin" : ""} />
          {value ? "重新生成" : "生成"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        调用时放在请求头 <code className="rounded bg-muted px-1 py-0.5">Authorization: Bearer &lt;key&gt;</code> 或{" "}
        <code className="rounded bg-muted px-1 py-0.5">X-API-Key</code>。生成后立即生效，无需再点保存。
      </p>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>重新生成 API Key？</AlertDialogTitle>
            <AlertDialogDescription>
              旧 Key 会立即失效，正在使用它的脚本需要换成新 Key。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={rotate}>重新生成</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ApiDocs() {
  const origin = window.location.origin;
  const links = [
    [`${origin}/api/v1/docs.md`, "接口文档（Markdown，可直接发给 AI）", FileText],
    [`${origin}/api/v1/docs`, "在线调试（Swagger）", CirclePlay],
    [`${origin}/api/v1/openapi.json`, "OpenAPI 规范（导入 Postman / Apifox）", Braces],
    [`${origin}/llms.txt`, "AI 入口（llms.txt）", Bot],
  ];
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">接口文档</div>
      <p className="text-xs text-muted-foreground">免 Key 访问，可分享给调用方。</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {links.map(([href, label, Icon]) => (
          <a
            key={href}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="group flex items-center gap-3 rounded-lg px-3 py-2.5 ring-1 ring-foreground/10 transition-colors hover:bg-muted/50"
          >
            <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Icon className="size-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm">{label}</span>
              <span className="block truncate font-mono text-xs text-muted-foreground">{href.replace(origin, "")}</span>
            </span>
            <ExternalLink className="size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
          </a>
        ))}
      </div>
    </div>
  );
}

function ApiUsage() {
  const base = `${window.location.origin}/api/v1`;
  const example = [
    `# 1. 触发注册（可选 body：{"email": "...", "proxy_id": "..."}）`,
    `curl -X POST -H "Authorization: Bearer $KEY" ${base}/register`,
    `# → {"run_id": 12, "status": "running"}`,
    ``,
    `# 2. 等结果（wait 最长 50 秒，未完成就重复调用），只取需要的字段`,
    `curl -H "Authorization: Bearer $KEY" \\`,
    `  "${base}/register/12?wait=50&fields=email,session_key,password"`,
    ``,
    `# 3. 批量导出（format: text/json/csv/line；可按 status/check_status/claimed/emails 过滤）`,
    `curl -H "Authorization: Bearer $KEY" \\`,
    `  "${base}/accounts/export?format=line&fields=email,session_key&status=success"`,
    ``,
    `# 4. 获取一个账号（每次一个，自动标记为「已获取」，不会重复发放；没有可用账号返回 404）`,
    `curl -X POST -H "Authorization: Bearer $KEY" \\`,
    `  "${base}/accounts/claim?fields=email,session_key,password"`,
    ``,
    `# 5. 用账号的 sessionKey 获取 Claude OAuth 令牌（access_token / refresh_token）`,
    `curl -X POST -H "Authorization: Bearer $KEY" -H "content-type: application/json" \\`,
    `  -d '{"email":"alice@example.com"}' "${base}/accounts/oauth"`,
  ].join("\n");
  return (
    <details className="group text-sm">
      <summary className="flex cursor-pointer list-none items-center gap-2 font-medium select-none [&::-webkit-details-marker]:hidden">
        <SquareTerminal className="size-4 text-muted-foreground" />
        调用示例
        <span className="text-xs font-normal text-muted-foreground group-open:hidden">（点击展开）</span>
      </summary>
      <pre className="log-panel mt-3 overflow-x-auto rounded-lg bg-terminal p-3.5 text-[12px] text-terminal-foreground">
        {example}
      </pre>
      <p className="mt-2 text-xs text-muted-foreground">
        可用字段：<code>GET {base}/fields</code>；可选代理：<code>GET {base}/proxies</code>。
      </p>
    </details>
  );
}

const OWN_KEYS = GROUPS.flatMap((g) => g.fields.map((f) => f.key));

function SettingsSection({ group, children }) {
  const Icon = group.icon;
  return (
    <section className="grid gap-4 @3xl:grid-cols-[15rem_minmax(0,1fr)] @3xl:gap-8">
      <div className="flex items-start gap-3 @3xl:pt-1">
        <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground ring-1 ring-foreground/5">
          <Icon className="size-4" />
        </span>
        <div className="space-y-1">
          <h2 className="text-sm font-semibold tracking-tight">{group.title}</h2>
          <p className="text-xs text-pretty text-muted-foreground">{group.description}</p>
        </div>
      </div>
      <Card>
        <CardContent className="space-y-5">{children}</CardContent>
      </Card>
    </section>
  );
}

export default function Settings({ onPasswordSet }) {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        const picked = {};
        for (const k of OWN_KEYS) picked[k] = cfg[k];
        // 密码不回填：避免明文展示，也让「留空=不修改」语义对得上。
        picked.panel_password = "";
        setForm(picked);
      })
      .catch((err) => {
        if (err?.status !== 401) setLoadError("加载配置失败");
      });
  }, []);

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    const changedPassword = !!(form.panel_password || "").trim();
    try {
      const updated = await api.putConfig(form);
      const picked = {};
      for (const k of OWN_KEYS) picked[k] = updated[k];
      picked.panel_password = "";
      setForm(picked);
      if (changedPassword) {
        // 会话签名绑定密码，改密码 = 当前 cookie 立即失效。
        // 不提示的话，面板会变成处处报错的「僵尸页」。
        toast.success("密码已更新，请用新密码重新登录");
        onPasswordSet?.();
        return;
      }
      toast.success("已保存");
    } catch (err) {
      if (err?.status !== 401) toast.error("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  const header = (
    <PageHeader
      icon={SettingsIcon}
      title="系统设置"
      description="调整面板、邮箱、注册流程与外部对接参数。修改后点击底部「保存设置」生效。"
    />
  );

  if (loadError) {
    return (
      <div className="space-y-6">
        {header}
        <div className="text-sm text-destructive">{loadError}</div>
      </div>
    );
  }
  if (!form) {
    return (
      <div className="space-y-6">
        {header}
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-40 w-full rounded-xl" />)}
      </div>
    );
  }

  return (
    <div className="@container space-y-6">
      {header}
      <form onSubmit={save} className="space-y-8">
        {GROUPS.map((group) => (
          <SettingsSection key={group.title} group={group}>
            {group.fields.map((f) =>
              f.type === "apikey" ? (
                <ApiKeyField
                  key={f.key}
                  value={form[f.key]}
                  onRotated={(k) => setField(f.key, k)}
                />
              ) : f.type === "checkbox" ? (
                <div
                  className="flex items-center justify-between gap-4 rounded-lg bg-muted/40 px-3.5 py-3 ring-1 ring-foreground/5"
                  key={f.key}
                >
                  <Label htmlFor={f.key} className="leading-snug">{f.label}</Label>
                  <Switch
                    id={f.key}
                    checked={!!form[f.key]}
                    onCheckedChange={(v) => setField(f.key, v)}
                  />
                </div>
              ) : (
                <div className="space-y-1.5" key={f.key}>
                  <Label htmlFor={f.key}>{f.label}</Label>
                  <Input
                    id={f.key}
                    type={f.type}
                    placeholder={f.placeholder ?? ""}
                    value={form[f.key] ?? ""}
                    autoComplete={f.type === "password" ? "new-password" : undefined}
                    onChange={(e) =>
                      setField(
                        f.key,
                        f.type === "number"
                          ? e.target.value.replace(/[^0-9.]/g, "")
                          : e.target.value,
                      )
                    }
                  />
                  {f.hint && <p className="text-xs text-muted-foreground">{f.hint}</p>}
                </div>
              ),
            )}
            {group.title === "开放 API" && (
              <>
                <ApiDocs />
                <ApiUsage />
              </>
            )}
          </SettingsSection>
        ))}
        <SaveBar saving={saving} />
      </form>
    </div>
  );
}
