import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Upload } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
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

const STORAGE_KEY = "cr:import-options";

const RESULT_LABEL = {
  created: ["新增", "text-success"],
  updated: ["已更新", "text-info"],
  skipped: ["跳过（失效）", "text-warning"],
  invalid: ["无法识别", "text-destructive"],
  duplicate: ["重复", "text-muted-foreground"],
};

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null") || {};
  } catch {
    return {};
  }
}

export function ImportDialog({ open, onOpenChange, onImported }) {
  const [proxies, setProxies] = useState([]);
  const [proxyId, setProxyId] = useState("");
  const [check, setCheck] = useState(true);
  const [skipDead, setSkipDead] = useState(true);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);

  useEffect(() => {
    if (!open) return;
    setReport(null);
    const saved = loadSaved();
    if (typeof saved.check === "boolean") setCheck(saved.check);
    if (typeof saved.skipDead === "boolean") setSkipDead(saved.skipDead);
    api
      .getConfig()
      .then((cfg) => {
        const list = cfg.saved_proxies || [];
        setProxies(list);
        setProxyId(list.some((p) => p.id === saved.proxyId) ? saved.proxyId : "");
      })
      .catch((e) => {
        if (e?.status !== 401) toast.error("代理列表加载失败");
      });
  }, [open]);

  async function doImport() {
    if (!text.trim()) return toast.error("请粘贴要导入的 sessionKey");
    setBusy(true);
    try {
      const res = await api.importAccounts({
        text,
        proxy_id: proxyId || undefined,
        check,
        skip_dead: skipDead,
      });
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ proxyId, check, skipDead }));
      setReport(res);
      const s = res.summary;
      if (s.created + s.updated > 0) {
        toast.success(`导入完成：新增 ${s.created}，更新 ${s.updated}`);
        setText("");
        onImported?.();
      } else {
        toast.error("没有导入任何账号，请查看明细");
      }
    } catch (e) {
      if (e?.status !== 401) toast.error(e.body?.detail || `导入失败（${e.status || "?"}）`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <AlertDialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <Upload className="size-4 text-primary" /> 导入 sessionKey
          </AlertDialogTitle>
          <AlertDialogDescription>
            每行一个账号：<code className="rounded bg-muted px-1">sk-ant-…</code>，或{" "}
            <code className="rounded bg-muted px-1">邮箱----sk-ant-…</code>（分隔符不限）。
            只有 sk 时会在检测时向 claude.ai 查询邮箱。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-4 text-sm">
          <Textarea
            aria-label="要导入的 sessionKey"
            className="field-sizing-fixed h-40 resize-y font-mono text-xs md:text-xs"
            placeholder={"sk-ant-sid01-xxxx\nuser@example.com----sk-ant-sid01-yyyy"}
            spellCheck={false}
            value={text}
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="space-y-1.5">
            <Label htmlFor="import-proxy">代理</Label>
            <NativeSelect
              id="import-proxy"
              className="w-full"
              value={proxyId}
              disabled={busy}
              onChange={(e) => setProxyId(e.target.value)}
            >
              <NativeSelectOption value="">不使用代理（直连检测，账号不绑定代理）</NativeSelectOption>
              {proxies.map((p) => <NativeSelectOption key={p.id} value={p.id}>{p.name}</NativeSelectOption>)}
            </NativeSelect>
            <p className="text-xs text-muted-foreground">
              所选代理会用于检测，并写入账号的代理字段（之后检测 / 接管都走它）。在
              <a className="text-primary underline-offset-4 hover:underline" href="#/proxies">代理池</a>中管理代理。
            </p>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <label className="flex cursor-pointer items-center gap-2.5">
              <Checkbox checked={check} disabled={busy} onCheckedChange={(v) => setCheck(v === true)} />
              导入前检测有效性并查询邮箱
            </label>
            <label className={cn("flex cursor-pointer items-center gap-2.5", (busy || !check) && "opacity-50")}>
              <Checkbox checked={skipDead} disabled={busy || !check}
                onCheckedChange={(v) => setSkipDead(v === true)} />
              跳过已失效的 sk
            </label>
          </div>
          {report && (
            <div className="max-h-48 overflow-auto rounded-lg bg-muted/40 p-2.5 text-xs ring-1 ring-foreground/10">
              {report.results.map((r) => {
                const [label, cls] = RESULT_LABEL[r.result] || [r.result, ""];
                return (
                  <div key={r.line} className="flex gap-2 py-0.5">
                    <span className="w-10 shrink-0 text-muted-foreground">#{r.line}</span>
                    <span className={`w-20 shrink-0 ${cls}`}>{label}</span>
                    <span className="min-w-0 truncate" title={r.detail || ""}>
                      {r.email || r.sk || ""}
                      {r.detail ? <span className="text-muted-foreground"> · {r.detail}</span> : null}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>关闭</AlertDialogCancel>
          <Button onClick={doImport} disabled={busy}>
            {busy ? <Spinner /> : <Upload />} {busy ? (check ? "检测并导入中…" : "导入中…") : "导入"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
