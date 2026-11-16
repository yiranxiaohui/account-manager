import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, UserPlus } from "lucide-react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { KEEP_PROXY, ProxySelect } from "@/components/proxy-select";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

// [字段, 标签, 占位, 是否占满一行, 是否等宽字体]；proxy_id 渲染成代理池下拉框
export const ACCOUNT_FIELDS = [
  ["email", "邮箱", "user@example.com", false, false],
  ["display_name", "备注", "给账号起个名字", false, false],
  ["password", "密码", "登录密码", false, false],
  ["session_key", "sessionKey", "sk-ant-sid01-…", true, true],
  ["proxy_id", "代理", "", true, false],
  ["mail_base_url", "收件地址 (mailUrl)", "https://mail.example.com", true, true],
  ["mail_key", "收件 Key (mailKey)", "AnyMail 子 Key", true, true],
];

export const emptyAccountForm = () =>
  Object.fromEntries(ACCOUNT_FIELDS.map(([k]) => [k, ""]));

// 提交前去掉「保持原代理」占位：不提交 proxy_id = 不改代理
export function accountPayload(form) {
  const body = { ...form };
  if (body.proxy_id === KEEP_PROXY) delete body.proxy_id;
  return body;
}

// 加载代理池条目（账号代理只能从这里选）
export function useSavedProxies(active = true) {
  const [proxies, setProxies] = useState([]);
  useEffect(() => {
    if (!active) return;
    api
      .getConfig()
      .then((cfg) => setProxies(cfg.saved_proxies || []))
      .catch((e) => {
        if (e?.status !== 401) toast.error("代理池加载失败");
      });
  }, [active]);
  return proxies;
}

export function AccountFields({ form, setForm, disabled, idPrefix, proxies, legacyProxy = "" }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {ACCOUNT_FIELDS.map(([key, label, hint, wide, mono]) => (
        <div key={key} className={`flex min-w-0 flex-col gap-1.5 ${wide ? "sm:col-span-2" : ""}`}>
          <Label htmlFor={`${idPrefix}-${key}`} className="gap-1 text-xs text-muted-foreground">
            {label}
            {key === "email" ? <span className="text-destructive">*</span> : null}
          </Label>
          {key === "proxy_id" ? (
            <>
              <ProxySelect
                id={`${idPrefix}-${key}`}
                value={form.proxy_id ?? ""}
                proxies={proxies}
                legacyUrl={legacyProxy}
                disabled={disabled}
                onChange={(v) => setForm((f) => ({ ...f, proxy_id: v }))}
              />
              <span className="text-xs text-muted-foreground">
                只能选择<a className="text-primary underline-offset-4 hover:underline" href="#/proxies">代理池</a>中的代理；检测、接管、OAuth 都会走它。
              </span>
            </>
          ) : (
            <Input
              id={`${idPrefix}-${key}`}
              className={mono ? "font-mono text-xs" : ""}
              value={form[key] ?? ""}
              placeholder={hint}
              spellCheck={false}
              disabled={disabled}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
            />
          )}
        </div>
      ))}
    </div>
  );
}

export function AccountCreateDialog({ open, onOpenChange, onCreated }) {
  const [form, setForm] = useState(emptyAccountForm);
  const [busy, setBusy] = useState(false);
  const proxies = useSavedProxies(open);

  useEffect(() => {
    if (open) setForm(emptyAccountForm());
  }, [open]);

  async function doCreate() {
    if (!form.email.trim()) return toast.error("请填写邮箱");
    setBusy(true);
    try {
      const row = await api.accountCreate(accountPayload(form));
      toast.success(`已新增账号：${row.email}`);
      onOpenChange(false);
      onCreated?.(row);
    } catch (e) {
      if (e?.status === 401) return;
      toast.error(
        e.status === 409
          ? "该邮箱的账号已存在"
          : e.body?.detail || `新增失败（${e.status || "?"}）`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <AlertDialogContent className="sm:max-w-xl">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <UserPlus className="size-4 text-primary" /> 新增账号
          </AlertDialogTitle>
          <AlertDialogDescription>
            手动录入一个已有账号。只有邮箱必填，其余字段可以之后再编辑。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AccountFields
          form={form}
          setForm={setForm}
          disabled={busy}
          idPrefix="create"
          proxies={proxies}
        />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
          <Button onClick={doCreate} disabled={busy}>
            {busy ? <Spinner /> : <Plus />} {busy ? "保存中…" : "新增"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
