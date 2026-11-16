import React from "react";
import ReactDOM from "react-dom/client";
import { LazyMotion, MotionConfig, domAnimation } from "motion/react";
import App from "./App.jsx";
import { ThemeProvider } from "@/components/theme";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider>
      {/* 动画只加载 domAnimation 特性集（不含 layout 动画），并尊重系统「减少动态效果」设置 */}
      <LazyMotion features={domAnimation} strict>
        <MotionConfig reducedMotion="user">
          <TooltipProvider delayDuration={300}>
            <App />
          </TooltipProvider>
        </MotionConfig>
      </LazyMotion>
    </ThemeProvider>
  </React.StrictMode>,
);
