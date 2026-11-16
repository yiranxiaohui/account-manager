import { useEffect, useState } from "react";
import { m } from "motion/react";
import { toast } from "sonner";
import { Eraser, FlaskConical, Plus, Server, Trash2 } from "lucide-react";
import { api } from "../api.js";
import { PageHeader, SectionTitle } from "@/components/page-header";
import { SaveBar } from "@/components/save-bar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";

const XUI_KEYS = ["xui_enabled", "xui_expiry_days", "xui_port_min", "xui_port_max", "xui_nodes"];

const POOL_FIELDS = [
  { key: "xui_expiry_days", label: "代理有效期（天）" },
  { key: "xui_port_min", label: "端口范围下限" },
  { key: "xui_port_max", label: "端口范围上限" },
];

const NODE_FIELDS = [
  { key: "name", label: "名称", placeholder: "如 usa-4" },
  { key: "base_url", label: "面板地址", placeholder: "http://host:2053" },
  { key: "username", label: "用户名", placeholder: "admin" },
  { key: "password", label: "密码", placeholder: "登录密码" },
  { key: "proxy_host", label: "代理出口主机", placeholder: "对外连接用的 host" },
];

const EMPTY_NODE = { name: "", base_url: "", username: "", password: "", proxy_host: "" };

export default function Nodes() {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [testingIndex, setTestingIndex] = useState(-1);
  const [cleaning, setCleaning] = useState(false);

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        const picked = {};
        for (const k of XUI_KEYS) picked[k] = cfg[k];
        picked.xui_nodes = cfg.xui_nodes || [];
        setForm(picked);
      })
      .catch(() => setLoadError("加载配置失败"));
  }, []);

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function setNode(i, key, value) {
    const nodes = [...form.xui_nodes];
    nodes[i] = { ...nodes[i], [key]: value };
    setField("xui_nodes", nodes);
  }

  async function save() {
    setSaving(true);
    try {
      const updated = await api.putConfig(form);
      const picked = {};
      for (const k of XUI_KEYS) picked[k] = updated[k];
      picked.xui_nodes = updated.xui_nodes || [];
      setForm(picked);
      toast.success("已保存");
    } catch {
      toast.error("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  async function testNode(node, i) {
    setTestingIndex(i);
    try {
      const r = await api.xuiTest(node);
      toast.success(`节点连接成功，现有 ${r.inbound_count} 个 inbound`);
    } catch (e) {
      toast.error(`节点连接失败：${e.body?.detail || e.message}`);
    } finally {
      setTestingIndex(-1);
    }
  }

  async function cleanupExpired() {
    setCleaning(true);
    try {
      const r = await api.xuiCleanup();
      toast.success(`已清理过期 inbound：共 ${r.total} 个`);
    } catch {
      toast.error("清理失败，请重试");
    } finally {
      setCleaning(false);
    }
  }

  const header = (
    <PageHeader
      icon={Server}
      title="节点设置"
      description="配置 3x-ui 节点：注册时自动在节点上创建临时代理 inbound，到期后可一键清理。"
      actions={form && (
        <Button variant="outline" onClick={cleanupExpired} disabled={cleaning}>
          {cleaning ? <Spinner /> : <Eraser />} 清理过期 inbound
        </Button>
      )}
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
        <Skeleton className="h-44 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {header}

      <Card>
        <CardHeader>
          <CardTitle>代理池设置</CardTitle>
          <CardDescription>开启后，注册任务未指定代理时会从下方节点自动分配一个临时 inbound。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="flex items-center justify-between gap-4 rounded-lg bg-muted/40 px-3.5 py-3 ring-1 ring-foreground/5">
            <div className="space-y-0.5">
              <Label htmlFor="xui_enabled">启用 3x-ui 代理池</Label>
              <p className="text-xs text-muted-foreground">关闭时注册使用固定代理或直连。</p>
            </div>
            <Switch
              id="xui_enabled"
              checked={!!form.xui_enabled}
              onCheckedChange={(v) => setField("xui_enabled", v)}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            {POOL_FIELDS.map((f) => (
              <div className="space-y-1.5" key={f.key}>
                <Label htmlFor={f.key}>{f.label}</Label>
                <Input
                  id={f.key}
                  type="number"
                  value={form[f.key] ?? ""}
                  onChange={(e) => setField(f.key, Number(e.target.value))}
                />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <SectionTitle
          title={`节点列表（${form.xui_nodes.length}）`}
          description="每个节点需要 3x-ui 面板地址与登录凭据；保存前可先测试连接。"
          actions={
            <Button
              variant="outline"
              onClick={() => setField("xui_nodes", [...form.xui_nodes, { ...EMPTY_NODE }])}
            >
              <Plus /> 添加节点
            </Button>
          }
        />
        {form.xui_nodes.length === 0 ? (
          <Card>
            <Empty className="py-10">
              <EmptyHeader>
                <EmptyMedia variant="icon"><Server /></EmptyMedia>
                <EmptyTitle>暂无节点</EmptyTitle>
                <EmptyDescription>添加一个 3x-ui 节点后，注册任务即可自动分配临时代理。</EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button onClick={() => setField("xui_nodes", [{ ...EMPTY_NODE }])}>
                  <Plus /> 添加节点
                </Button>
              </EmptyContent>
            </Empty>
          </Card>
        ) : (
          <div className="grid gap-4">
            {form.xui_nodes.map((node, i) => (
                <m.div
                  key={i}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <Card size="sm">
                    <CardHeader className="flex flex-row items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                          <Server className="size-4" />
                        </span>
                        <div className="min-w-0">
                          <CardTitle className="truncate">{node.name || `节点 ${i + 1}`}</CardTitle>
                          <p className="truncate font-mono text-xs text-muted-foreground">
                            {node.base_url || "尚未填写面板地址"}
                          </p>
                        </div>
                      </div>
                      <span className="flex shrink-0 gap-1.5">
                        <Button variant="outline" size="sm" onClick={() => testNode(node, i)}
                          disabled={testingIndex === i}>
                          {testingIndex === i ? <Spinner /> : <FlaskConical />} 测试
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() =>
                            setField("xui_nodes", form.xui_nodes.filter((_, j) => j !== i))
                          }
                        >
                          <Trash2 /> 删除
                        </Button>
                      </span>
                    </CardHeader>
                    <CardContent>
                      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
                        {NODE_FIELDS.map((f) => (
                          <div className="flex flex-col gap-1.5" key={f.key}>
                            <Label htmlFor={`node-${i}-${f.key}`} className="text-xs text-muted-foreground">
                              {f.label}
                            </Label>
                            <Input
                              id={`node-${i}-${f.key}`}
                              type={f.type || "text"}
                              placeholder={f.placeholder}
                              value={node[f.key] ?? ""}
                              onChange={(e) => setNode(i, f.key, e.target.value)}
                            />
                          </div>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                </m.div>
            ))}
          </div>
        )}
      </div>

      <SaveBar type="button" onClick={save} saving={saving} label="保存节点配置" hint="节点与代理池设置修改后需保存" />
    </div>
  );
}
