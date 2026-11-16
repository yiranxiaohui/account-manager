import { useState } from "react";
import { m } from "motion/react";
import { ArrowRight, Eye, EyeOff, LockKeyhole } from "lucide-react";
import { toast } from "sonner";
import { api } from "../api.js";
import { BrandMark } from "@/components/brand";
import { ThemeToggle } from "@/components/theme";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";

export default function Login({ onOk }) {
  const [password, setPassword] = useState("");
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!password) return;
    setBusy(true);
    try {
      await api.login(password);
      onOk();
    } catch (err) {
      const detail = err.body?.detail;
      toast.error(
        err.status === 401 ? detail || "密码错误" : "登录失败，请重试",
      );
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-svh flex-col items-center justify-center overflow-hidden bg-background p-6">
      {/* 装饰背景：网格 + 品牌色光晕 */}
      <div aria-hidden="true" className="bg-grid pointer-events-none absolute inset-0" />
      <div
        aria-hidden="true"
        className="brand-gradient pointer-events-none absolute top-[-14rem] left-1/2 size-[36rem] -translate-x-1/2 rounded-full opacity-25 blur-[120px] dark:opacity-30"
      />
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>

      <m.div
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
        className="relative w-full max-w-sm"
      >
        <div className="mb-7 flex flex-col items-center gap-4 text-center">
          <BrandMark className="size-12 rounded-2xl" iconClassName="size-6" />
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">欢迎回来</h1>
            <p className="text-sm text-muted-foreground">登录 Account Manager 管理面板</p>
          </div>
        </div>

        <form
          onSubmit={submit}
          className="space-y-4 rounded-2xl bg-card/80 p-6 shadow-xl shadow-black/5 ring-1 ring-foreground/10 backdrop-blur-xl dark:shadow-black/30"
        >
          <div className="space-y-2">
            <Label htmlFor="panel-password">面板密码</Label>
            <InputGroup className="h-10">
              <InputGroupAddon>
                <LockKeyhole />
              </InputGroupAddon>
              <InputGroupInput
                id="panel-password"
                type={shown ? "text" : "password"}
                autoComplete="current-password"
                placeholder="请输入面板密码"
                value={password}
                autoFocus
                onChange={(e) => setPassword(e.target.value)}
              />
              <InputGroupAddon align="inline-end">
                <InputGroupButton
                  size="icon-xs"
                  aria-label={shown ? "隐藏密码" : "显示密码"}
                  onClick={() => setShown((v) => !v)}
                >
                  {shown ? <EyeOff /> : <Eye />}
                </InputGroupButton>
              </InputGroupAddon>
            </InputGroup>
          </div>
          <Button type="submit" size="lg" className="h-10 w-full" disabled={busy || !password}>
            {busy ? <Spinner /> : null}
            {busy ? "登录中…" : "登录"}
            {!busy && <ArrowRight data-icon="inline-end" />}
          </Button>
        </form>

        <p className="mt-6 text-center text-xs text-muted-foreground">
          自动化注册 · 账号接管 · OAuth 授权 · sub2api 同步
        </p>
      </m.div>
    </div>
  );
}
