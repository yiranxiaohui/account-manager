import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { toast } from "sonner";
import { Download, ExternalLink, Smartphone, Square, TriangleAlert } from "lucide-react";
import { api } from "../api.js";
import { Callout } from "@/components/callout";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export const ANDROID_VIEWER = "/android.html";

const EMPTY = { running: false, email: null, phase: null, kvm: true, install: null, envs: [] };
const PHASE_LABEL = {
  starting: "正在准备",
  booting: "系统启动中",
  running: "运行中",
};

const gb = (bytes) => `${(bytes / 1024 ** 3).toFixed(1)} GB`;

// Android 模拟器状态：启动/安装进行中时每 3 秒刷新一次；会话意外结束时提示原因。
export function useAndroid() {
  const [status, setStatus] = useState(EMPTY);
  const [stopping, setStopping] = useState(false);
  const prev = useRef(EMPTY);

  const refresh = useCallback(async () => {
    try {
      const next = await api.androidStatus();
      const before = prev.current;
      if (before.running && !next.running && next.last_error) {
        toast.error(`Android 模拟器：${next.last_error}`);
      } else if (before.running && before.phase !== "running" && next.phase === "running") {
        toast.success(`Android 模拟器已就绪：${next.email}`);
      }
      if (before.install?.state === "installing" && next.install?.state === "ready") {
        toast.success("Android 运行环境已安装，可以启动模拟器了");
      } else if (before.install?.state === "installing" && next.install?.state === "error") {
        toast.error(`安装 Android 运行环境失败：${next.install.error || "未知错误"}`);
      }
      prev.current = next;
      setStatus(next);
      return next;
    } catch {
      return null;
    }
  }, []);

  const busy = status.running || status.install?.state === "installing";
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    if (!busy) return undefined;
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [busy, refresh]);

  const start = useCallback(async (email) => {
    // 弹窗必须在点击回调里同步打开，否则会被浏览器拦截；启动失败再关掉。
    const viewer = window.open("about:blank", "_blank");
    try {
      await api.androidStart(email);
      if (viewer) viewer.location.href = ANDROID_VIEWER;
      toast.success(`正在启动 Android 模拟器：${email}`, {
        description: "冷启动约需 30～90 秒，画面页会显示启动进度。",
      });
    } catch (e) {
      viewer?.close();
      if (e?.status === 401) return;
      toast.error(e.body?.detail || `启动 Android 模拟器失败（${e.status || "?"}）`);
    } finally {
      refresh();
    }
  }, [refresh]);

  const stop = useCallback(async () => {
    setStopping(true);
    try {
      await api.androidStop();
      toast.success("Android 模拟器已停止");
    } catch (e) {
      if (e?.status !== 401) toast.error(e.body?.detail || `停止失败（${e.status || "?"}）`);
    } finally {
      setStopping(false);
      refresh();
    }
  }, [refresh]);

  const install = useCallback(async () => {
    try {
      await api.androidInstall();
      toast.success("已开始下载 Android 运行环境");
    } catch (e) {
      if (e?.status !== 401) toast.error(e.body?.detail || `安装失败（${e.status || "?"}）`);
    } finally {
      refresh();
    }
  }, [refresh]);

  return { status, refresh, start, stop, install, stopping };
}

// 运行中 / 安装中的提示条。
export function AndroidCallout({ android }) {
  const { status, stop, stopping, install } = android;
  const inst = status.install;
  const installing = inst?.state === "installing";
  const failed = !status.running && !installing && inst?.state === "error";
  return (
    <AnimatePresence initial={false}>
      {(status.running || installing || failed) && (
        <m.div
          key={status.running ? "running" : installing ? "installing" : "failed"}
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.2 }}
        >
          {status.running ? (
            <Callout
              tone="success"
              icon={Smartphone}
              title={
                <span className="flex items-center gap-2">
                  <span className="relative flex size-2">
                    {status.phase !== "running" && (
                      <span className="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-60" />
                    )}
                    <span className="relative inline-flex size-2 rounded-full bg-success" />
                  </span>
                  Android 模拟器：{status.email}
                  <span className="font-normal text-muted-foreground">
                    · {PHASE_LABEL[status.phase] || status.phase}
                  </span>
                </span>
              }
              actions={
                <>
                  <Button size="sm" asChild>
                    <a href={ANDROID_VIEWER} target="_blank" rel="noopener noreferrer">
                      <ExternalLink /> 打开画面
                    </a>
                  </Button>
                  <Button variant="destructive" size="sm" onClick={stop} disabled={stopping}>
                    {stopping ? <Spinner /> : <Square />}
                    {stopping ? "停止中…" : "停止"}
                  </Button>
                </>
              }
            >
              全部流量经该账号绑定的代理出去{status.timezone ? `，时区 ${status.timezone}` : ""}。
              数据按账号保留，下次启动仍在；关闭画面页后长时间无人查看会自动停止。
              {!window.isSecureContext && (
                <span className="mt-1 block text-warning">
                  当前通过 HTTP 访问面板，模拟器画面需要 HTTPS：请改用 https:// 地址打开面板。
                </span>
              )}
            </Callout>
          ) : installing ? (
            <Callout
              tone="info"
              icon={Download}
              title={
                <span className="flex items-center gap-2">
                  <Spinner className="size-3.5" />
                  正在安装 Android 运行环境：{inst.step || "准备中"}
                </span>
              }
            >
              已下载 {gb(inst.downloaded || 0)} / {gb(inst.total || 0)}
              （{Math.min(100, Math.floor(((inst.downloaded || 0) / (inst.total || 1)) * 100))}%），
              完成后即可为账号启动模拟器。
            </Callout>
          ) : (
            <Callout
              tone="danger"
              icon={TriangleAlert}
              title="Android 运行环境安装失败"
              actions={
                <Button variant="outline" size="sm" onClick={install}>
                  <Download /> 重试
                </Button>
              }
            >
              {inst.error || "未知错误"}
            </Callout>
          )}
        </m.div>
      )}
    </AnimatePresence>
  );
}

// 运行环境未安装时，点启动先弹这个确认框。
export function AndroidInstallDialog({ open, onOpenChange, android }) {
  const total = android.status.install?.total || 0;
  const kvm = android.status.kvm;
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="sm:max-w-md">
        <AlertDialogHeader>
          <AlertDialogMedia className="bg-primary/10 text-primary">
            <Smartphone />
          </AlertDialogMedia>
          <AlertDialogTitle>安装 Android 运行环境？</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2">
              <p>
                从 Google 官方仓库安装 Android 模拟器和 Android 17（Google Play，API 37.2）系统镜像。
                完整下载约 {gb(total)}，已安装的组件会跳过。解压后约占 9 GiB，设备数据另计。
                下载在后台进行，安装好之后各账号共用。
              </p>
              <p>下载即表示同意 Android SDK 许可协议。</p>
              {!kvm && (
                <p className="text-warning">
                  当前容器里没有可用的 /dev/kvm，装好后也无法启动：请先在宿主开放 KVM，
                  并在 docker-compose.yml 里为服务加上 devices: ["/dev/kvm:/dev/kvm"]。
                </p>
              )}
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <Button
            onClick={() => {
              onOpenChange(false);
              android.install();
            }}
          >
            <Download /> 下载安装
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
