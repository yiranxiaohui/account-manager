#!/bin/sh
# 容器入口：先准备面板监听配置（默认 HTTPS，见 prepare-tls.sh），再交给 supervisord。
set -eu
/usr/local/bin/account-manager-prepare-tls
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/account-manager.conf
