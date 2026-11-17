# 1) 前端
FROM oven/bun:1 AS web
WORKDIR /web
COPY web/package.json ./
RUN bun install
COPY web/ ./
RUN bun run build

# 2) 运行镜像（含 Python + Xvfb + Playwright Chromium）
FROM python:3.13-slim
# Chromium 自身的系统依赖由下面的 `playwright install --with-deps` 按官方清单安装；
# 这里只装 Xvfb（注册/接管的虚拟显示）、字体和面板运行所需的服务。
# Mesa 软件 GL 显式安装（以前由其他软件包间接带入），保持虚拟屏上浏览器的 GL 环境不变；
# libgbm/pixman/xkbcommon/drm 是 Selkies 编码库 pixelflux 的系统依赖。
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    libgl1-mesa-dri \
    libglx-mesa0 \
    libgbm1 \
    libpixman-1-0 \
    libxkbcommon0 \
    libdrm2 \
    fonts-liberation \
    fonts-unifont \
    nginx \
    supervisor \
    openssl \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
# Selkies：把接管虚拟屏以 H.264 视频流经 WebSocket 推给网页。
# 装在独立 venv，依赖不与面板混在一起；面板只按 ACCOUNT_MANAGER_SELKIES_BIN 调用它。
# 版本与哈希锁定在 deploy/selkies-requirements.txt。
COPY deploy/selkies-requirements.txt /tmp/selkies-requirements.txt
RUN uv venv /opt/selkies --python /usr/local/bin/python3 && \
    uv pip install --python /opt/selkies/bin/python --no-cache --require-hashes \
      -r /tmp/selkies-requirements.txt && \
    rm /tmp/selkies-requirements.txt && \
    /opt/selkies/bin/selkies --help > /dev/null
ENV ACCOUNT_MANAGER_SELKIES_BIN=/opt/selkies/bin/selkies
WORKDIR /app
COPY pyproject.toml uv.lock ./
# 此时源码还没拷入，只装第三方依赖；项目本体（hatchling 构建需要 README.md/源码）留到下面装
RUN uv sync --frozen --no-dev --no-install-project
# Playwright 自带的 Chromium（完整版，不装 headless shell：注册与接管都以有头模式
# 挂在 Xvfb 上）。版本由 uv.lock 锁定的 playwright 决定，放在拷源码之前以复用缓存。
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN uv run --no-sync playwright install --with-deps --no-shell chromium && \
    rm -rf /var/lib/apt/lists/*
COPY account_manager/ ./account_manager/
COPY server/ ./server/
COPY serve.py main.py README.md ./
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY deploy/supervisord.conf /etc/supervisor/conf.d/account-manager.conf
COPY deploy/prepare-tls.sh /usr/local/bin/account-manager-prepare-tls
COPY deploy/entrypoint.sh /usr/local/bin/account-manager-entrypoint
# 构建时校验 HTTPS 与明文两种监听配置，镜像里默认留明文那份；
# 容器启动时入口脚本按 PANEL_TLS 重新生成（默认 HTTPS）。
RUN TLS_DIR=/tmp/tls-check /usr/local/bin/account-manager-prepare-tls && nginx -t && \
    rm -rf /tmp/tls-check && \
    PANEL_TLS=off /usr/local/bin/account-manager-prepare-tls && nginx -t
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./web/dist
ENV ACCOUNT_MANAGER_INTERNAL_PORT=8791
EXPOSE 8790
CMD ["/usr/local/bin/account-manager-entrypoint"]
