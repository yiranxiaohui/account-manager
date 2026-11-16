import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Eye, EyeOff, FlaskConical, Globe, Loader2 } from "lucide-react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ProbeResult } from "@/components/proxy-probe-result";

const newId = () =>
  globalThis.crypto?.randomUUID?.() || `proxy-${Date.now()}-${Math.random().toString(36).slice(2)}`;

// entry 为空 = 新增；保存时回调 onSave(条目)，由页面负责落库。
export function ProxyEditDialog({ open, entry, onOpenChange, onSave }) {
  const [form, setForm] = useState({ id: "", name: "", url: "" });
  const [showUrl, setShowUrl] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [probe, setProbe] = useState(null);

  useEffect(() => {
    if (!open) return;
    setForm(entry ? { ...entry } : { id: newId(), name: "", url: "" });
    setShowUrl(!entry);
    setProbe(null);
  }, [open, entry]);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  async function test() {
    if (!form.url.trim()) return toast.error("请先填写代理地址");
    setTesting(true);
    setProbe(null);
    try {
      setProbe(await api.proxyTest({ url: form.url.trim() }));
    } catch (e) {
      if (e?.status !== 401) toast.error(e.body?.detail || "测试失败");
    } finally {
      setTesting(false);
    }
  }

  async function save(e) {
    e.preventDefault();
    if (!form.name.trim() || !form.url.trim()) return toast.error("请填写名称和地址");
    setSaving(true);
    try {
      const ok = await onSave({ ...form, name: form.name.trim(), url: form.url.trim() });
      if (ok) onOpenChange(false);
    } finally {
      setSaving(false);
    }
  }

  const fromSub2api = entry?.source === "sub2api";
  return (
    <AlertDialog open={open} onOpenChange={(o) => !saving && onOpenChange(o)}>
      <AlertDialogContent className="sm:max-w-lg">
        <form onSubmit={save} className="grid gap-4">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Globe className="size-4 text-primary" /> {entry ? "编辑代理" : "添加代理"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              支持 HTTP(S) 与 SOCKS5 代理，带认证的 SOCKS5 会自动经本地中继供浏览器使用。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="proxy-name">名称</Label>
            <Input id="proxy-name" placeholder="如：日本-东京-1" value={form.name}
              onChange={set("name")} autoFocus={!entry} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="proxy-url">地址</Label>
            <InputGroup>
              <InputGroupInput
                id="proxy-url"
                className="font-mono text-xs md:text-xs"
                type={showUrl ? "text" : "password"}
                placeholder="socks5://user:pass@host:port 或 http://host:port"
                value={form.url}
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => { set("url")(e); setProbe(null); }}
              />
              <InputGroupAddon align="inline-end">
                <InputGroupButton
                  size="icon-xs"
                  aria-label={showUrl ? "隐藏地址" : "显示地址"}
                  onClick={() => setShowUrl((v) => !v)}
                >
                  {showUrl ? <EyeOff /> : <Eye />}
                </InputGroupButton>
              </InputGroupAddon>
            </InputGroup>
            {fromSub2api && (
              <p className="text-xs text-warning">
                此代理导入自 sub2api（#{entry.source_id}），「同步 sub2api」时地址会按 sub2api 更新，名称保持不变。
              </p>
            )}
          </div>
          <div className="flex min-h-11 items-center gap-3 rounded-lg border border-dashed bg-muted/30 px-3 py-2 text-xs">
            <Button type="button" variant="outline" size="sm" onClick={test} disabled={testing}>
              {testing ? <Loader2 className="animate-spin" /> : <FlaskConical />} 测试连接
            </Button>
            {probe ? <ProbeResult result={probe} /> : (
              <span className="text-muted-foreground">
                {testing ? "正在经代理查询出口 IP…" : "保存前可先测试出口 IP、地区与延迟"}
              </span>
            )}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel type="button" disabled={saving}>取消</AlertDialogCancel>
            <Button type="submit" disabled={saving}>
              {saving && <Spinner />}
              {saving ? "保存中…" : "保存"}
            </Button>
          </AlertDialogFooter>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  );
}
