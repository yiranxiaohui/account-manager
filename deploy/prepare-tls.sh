#!/bin/sh
# 生成 Nginx 的面板监听配置，并按需准备 HTTPS 证书。
#
# 接管画面（Selkies）用浏览器的 WebCodecs 解码视频流，浏览器只在 HTTPS（安全上下文）
# 下提供它，所以面板默认直接以 HTTPS 监听 8790：
#   - data/tls/panel.crt + panel.key 已存在时直接使用（可换成自己的正式证书）；
#   - 不存在时生成一张 10 年期自签证书并保存在 data 目录，重建容器后浏览器的
#     「继续访问」例外仍然有效；
#   - 明文 HTTP 打到这个端口时（Nginx 内部码 497）跳转到同地址的 HTTPS，
#     原来的 http:// 书签照常可用。
# PANEL_TLS=off 时退回明文 HTTP，适合由外层反代统一终结 HTTPS 的部署。
set -eu

PANEL_TLS="${PANEL_TLS:-on}"
TLS_DIR="${TLS_DIR:-/app/data/tls}"
LISTEN_CONF="${LISTEN_CONF:-/etc/nginx/account-manager-listen.conf}"
PANEL_LISTEN_PORT="${PANEL_LISTEN_PORT:-8790}"
CERT="$TLS_DIR/panel.crt"
KEY="$TLS_DIR/panel.key"

write_conf() {
    tmp="$LISTEN_CONF.tmp.$$"
    cat > "$tmp"
    mv "$tmp" "$LISTEN_CONF"
}

case "$PANEL_TLS" in
    off|false|0|no)
        write_conf <<EOF
# 由 prepare-tls.sh 生成：PANEL_TLS=$PANEL_TLS，明文 HTTP。
listen $PANEL_LISTEN_PORT;
EOF
        echo "panel: HTTP on :$PANEL_LISTEN_PORT (PANEL_TLS=$PANEL_TLS)"
        exit 0
        ;;
    on|true|1|yes|"") ;;
    *)
        echo "prepare-tls: unknown PANEL_TLS=$PANEL_TLS (use on or off)" >&2
        exit 1
        ;;
esac

if [ -s "$CERT" ] && [ -s "$KEY" ]; then
    echo "panel: HTTPS on :$PANEL_LISTEN_PORT with $CERT"
else
    if [ -e "$CERT" ] || [ -e "$KEY" ]; then
        echo "prepare-tls: only one of $CERT / $KEY exists; refusing to overwrite it" >&2
        exit 1
    fi
    umask 077
    mkdir -p "$TLS_DIR"
    chmod 700 "$TLS_DIR"
    cnf="$(mktemp)"
    trap 'rm -f "$cnf" "$KEY.tmp" "$CERT.tmp"' EXIT
    cat > "$cnf" <<'EOF'
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = account-manager
[ext]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
subjectAltName = DNS:localhost, DNS:account-manager, IP:127.0.0.1
EOF
    openssl req -x509 -config "$cnf" -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
        -nodes -days 3650 -keyout "$KEY.tmp" -out "$CERT.tmp" 2>/dev/null
    chmod 600 "$KEY.tmp"
    chmod 644 "$CERT.tmp"
    mv "$KEY.tmp" "$KEY"
    mv "$CERT.tmp" "$CERT"
    echo "panel: generated self-signed certificate $CERT"
    echo "panel: SHA-256 fingerprint $(openssl x509 -in "$CERT" -noout -fingerprint -sha256 | cut -d= -f2)"
fi

write_conf <<EOF
# 由 prepare-tls.sh 生成：PANEL_TLS=$PANEL_TLS，HTTPS。
listen $PANEL_LISTEN_PORT ssl;
ssl_certificate $CERT;
ssl_certificate_key $KEY;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_session_cache shared:panel_tls:1m;
ssl_session_timeout 1d;
# 明文 HTTP 请求打到 HTTPS 端口（内部码 497）时跳转到同地址的 HTTPS。
error_page 497 =301 https://\$http_host\$request_uri;
EOF
