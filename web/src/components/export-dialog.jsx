import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Download, Info } from "lucide-react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const STORAGE_KEY = "cr:export-options";

const FORMATS = [
  ["text", "文本块（每行「字段：值」）"],
  ["line", "单行（值用分隔符连接）"],
  ["csv", "CSV（Excel 可打开）"],
  ["json", "JSON"],
  ["sub2api", "sub2api 导入文件（仅已授权 OAuth 的账号）"],
];

const SCOPES = [
  ["all", "全部账号", {}],
  ["success", "仅注册成功（有 sessionKey）", { status: "success" }],
  ["alive", "仅检测有效", { check_status: "alive" }],
  ["unclaimed", "仅未获取", { claimed: "false" }],
  ["claimed", "仅已获取", { claimed: "true" }],
];

const EXT = { text: "txt", line: "txt", csv: "csv", json: "json", sub2api: "json" };

function isEmptyExport(text, format) {
  const body = text.replace(/^\ufeff/, "").trim();
  if (!body || (format === "json" && body === "[]")) return true;
  if (format === "sub2api") {
    try {
      return !JSON.parse(body).accounts?.length;
    } catch {
      return false;
    }
  }
  return false;
}

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch {
    return null;
  }
}

export function ExportDialog({ open, onOpenChange }) {
  const [meta, setMeta] = useState(null);
  const [fields, setFields] = useState([]);
  const [format, setFormat] = useState("text");
  const [sep, setSep] = useState("----");
  const [scope, setScope] = useState("all");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || meta) return;
    api
      .exportFields()
      .then((d) => {
        setMeta(d);
        const saved = loadSaved();
        const known = new Set(d.fields.map((f) => f.key));
        const savedFields = (saved?.fields || []).filter((k) => known.has(k));
        setFields(savedFields.length ? savedFields : d.default_fields);
        if (saved?.format && EXT[saved.format]) setFormat(saved.format);
        if (saved?.sep) setSep(saved.sep);
        if (saved?.scope && SCOPES.some(([k]) => k === saved.scope)) setScope(saved.scope);
      })
      .catch((e) => {
        if (e?.status !== 401) toast.error("加载导出字段失败");
      });
  }, [open, meta]);

  function toggle(key) {
    setFields((prev) => {
      if (prev.includes(key)) return prev.filter((k) => k !== key);
      // 保持字段表里的顺序，导出列顺序可预期
      const order = meta.fields.map((f) => f.key);
      return [...prev, key].sort((a, b) => order.indexOf(a) - order.indexOf(b));
    });
  }

  async function doExport() {
    const sub2api = format === "sub2api";
    if (!sub2api && !fields.length) return toast.error("至少选择一个字段");
    if (format === "line" && !sep) return toast.error("请填写分隔符");
    setBusy(true);
    try {
      const params = { format, ...SCOPES.find(([k]) => k === scope)[2] };
      if (!sub2api) params.fields = fields.join(",");
      if (format === "line") params.sep = sep;
      const text = await api.exportAccountsText(params);
      if (isEmptyExport(text, format)) {
        toast.error(sub2api ? "没有已授权 OAuth 的账号" : "没有符合条件的账号");
        return;
      }
      const type = { json: "application/json", sub2api: "application/json", csv: "text/csv" }[format]
        || "text/plain";
      const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
      const a = document.createElement("a");
      a.href = url;
      a.download = sub2api ? "sub2api-accounts.json" : `accounts.${EXT[format]}`;
      a.click();
      URL.revokeObjectURL(url);
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ fields, format, sep, scope }));
      onOpenChange(false);
    } catch (e) {
      if (e?.status !== 401) toast.error("导出失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="sm:max-w-lg">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <Download className="size-4 text-primary" /> 导出账号
          </AlertDialogTitle>
          <AlertDialogDescription>选择要导出的信息和格式。</AlertDialogDescription>
        </AlertDialogHeader>
        {!meta ? (
          <div className="space-y-3">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : (
          <div className="space-y-4 text-sm">
            {format === "sub2api" ? (
              <div className="flex gap-2.5 rounded-lg border border-info/25 bg-info/8 p-3 text-xs text-muted-foreground">
                <Info className="mt-px size-4 shrink-0 text-info" />
                <span>
                  生成 sub2api 的「导入数据」文件（Anthropic · OAuth 账号，含账号绑定的代理），
                  只包含已获取 OAuth 令牌的账号。导入后由 sub2api 自行刷新令牌；refresh_token
                  只能使用一次，同一份令牌不要同时导入多个系统。
                </span>
              </div>
            ) : (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>导出字段</Label>
                <span className="flex gap-1">
                  <Button type="button" variant="ghost" size="xs" onClick={() => setFields(meta.default_fields)}>
                    默认
                  </Button>
                  <Button type="button" variant="ghost" size="xs"
                    onClick={() => setFields(meta.fields.map((f) => f.key))}>
                    全选
                  </Button>
                </span>
              </div>
              <div className="grid grid-cols-2 gap-1 rounded-lg p-1.5 ring-1 ring-foreground/10">
                {meta.fields.map((f) => (
                  <label
                    key={f.key}
                    className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors hover:bg-muted/60"
                  >
                    <Checkbox checked={fields.includes(f.key)} onCheckedChange={() => toggle(f.key)} />
                    <span className="truncate">{f.title}</span>
                    {f.secret && (
                      <span className="ml-auto shrink-0 rounded bg-warning/14 px-1 text-[10px] text-warning">敏感</span>
                    )}
                  </label>
                ))}
              </div>
            </div>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="export-format">格式</Label>
                <NativeSelect
                  id="export-format"
                  className="w-full"
                  value={format}
                  onChange={(e) => setFormat(e.target.value)}
                >
                  {FORMATS.map(([k, label]) => <NativeSelectOption key={k} value={k}>{label}</NativeSelectOption>)}
                </NativeSelect>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="export-scope">范围</Label>
                <NativeSelect
                  id="export-scope"
                  className="w-full"
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                >
                  {SCOPES.map(([k, label]) => <NativeSelectOption key={k} value={k}>{label}</NativeSelectOption>)}
                </NativeSelect>
              </div>
            </div>
            {format === "line" && (
              <div className="space-y-1.5">
                <Label htmlFor="export-sep">分隔符</Label>
                <Input id="export-sep" value={sep} maxLength={16} onChange={(e) => setSep(e.target.value)} />
              </div>
            )}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <Button onClick={doExport} disabled={!meta || busy}>
            {busy ? <Spinner /> : <Download />} {busy ? "导出中…" : "导出"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
