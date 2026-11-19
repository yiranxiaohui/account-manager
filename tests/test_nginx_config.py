from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_selkies_is_proxied_with_panel_auth():
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

    assert "location /vnc/" in config
    assert "location = /websockify" not in config
    # 接管与 Android 模拟器两个 Selkies 画面都要经面板登录鉴权。
    assert config.count("auth_request /_vnc_auth;") == 2
    block = config.split("location /vnc/ {", 1)[1].split("}", 1)[0]
    # Selkies 以 /vnc 为前缀运行，路径原样转发（proxy_pass 不带 URI）。
    assert "proxy_pass http://selkies;" in block
    assert "server 127.0.0.1:14500;" in config
    # Selkies 按 Host 校验 WebSocket Origin：Host 必须带上浏览器看到的端口。
    assert "proxy_set_header Host $http_host;" in block
    assert "proxy_set_header Upgrade $http_upgrade;" in block
    assert "proxy_read_timeout 86400s;" in block
    assert "proxy_socket_keepalive on;" in block
    assert "proxy_buffering off;" in block
    assert "xpra" not in config.lower()


def test_android_selkies_is_proxied_with_panel_auth():
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

    assert "server 127.0.0.1:14501;" in config
    assert "location = /android-vnc {" in config
    block = config.split("location /android-vnc/ {", 1)[1].split("}", 1)[0]
    assert "auth_request /_vnc_auth;" in block
    assert "proxy_pass http://selkies_android;" in block
    assert "proxy_set_header Host $http_host;" in block
    assert "proxy_set_header Upgrade $http_upgrade;" in block
    assert "proxy_read_timeout 86400s;" in block
    assert "proxy_buffering off;" in block


def test_android_ports_match_manager():
    from server import android

    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert f"server 127.0.0.1:{android.WEB_PORT};" in config
    assert f"location {android.WEB_SUBFOLDER}/ {{" in config
    viewer = (ROOT / "web" / "public" / "android.html").read_text(encoding="utf-8")
    assert f'viewer.src = "{android.WEB_SUBFOLDER}/"' in viewer
    assert "/api/android/heartbeat" in viewer


def test_panel_listen_is_generated_by_entrypoint():
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert "include /etc/nginx/account-manager-listen.conf;" in config
    assert "listen 8790;" not in config


def test_takeover_api_outlives_default_nginx_read_timeout():
    """接管 start/relogin 是长同步请求，默认 60s 会抢先回 504、吞掉真实报错。"""
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

    block = config.split("location /api/takeover {", 1)[1].split("}", 1)[0]
    # 带结尾斜杠的前缀会把状态接口 GET /api/takeover 自动 301 到 /api/takeover/。
    assert "location /api/takeover/ {" not in config
    assert "proxy_pass http://panel;" in block
    assert "proxy_read_timeout 600s;" in block
    assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in block


def test_nginx_auth_subrequest_targets_fastapi():
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

    assert "location = /_vnc_auth" in config
    assert "internal;" in config
    assert "proxy_pass http://panel/api/vnc-auth;" in config
    assert "proxy_set_header Cookie $http_cookie;" in config


def test_runtime_uses_supervisor_for_panel_and_nginx():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    supervisor = (ROOT / "deploy" / "supervisord.conf").read_text(encoding="utf-8")

    assert "nginx" in dockerfile
    assert "supervisor" in dockerfile
    assert "ACCOUNT_MANAGER_INTERNAL_PORT=8791" in dockerfile
    assert "[program:panel]" in supervisor
    assert "[program:nginx]" in supervisor


def test_runtime_installs_hash_locked_selkies_instead_of_xpra():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "deploy" / "selkies-requirements.txt").read_text(encoding="utf-8")

    assert "xpra" not in dockerfile.lower()
    assert "KASMVNC_VERSION" not in dockerfile
    assert not (ROOT / "deploy" / "xpra-lts.sources").exists()
    assert "--require-hashes" in dockerfile
    assert "ACCOUNT_MANAGER_SELKIES_BIN=/opt/selkies/bin/selkies" in dockerfile
    assert "selkies==2.2.1 \\" in requirements
    assert "--hash=sha256:" in requirements
    # 入口脚本先生成监听配置，再交给 supervisord。
    assert 'CMD ["/usr/local/bin/account-manager-entrypoint"]' in dockerfile
    entry = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "account-manager-prepare-tls" in entry
    assert "exec /usr/bin/supervisord" in entry


def test_takeover_wrapper_keeps_session_alive():
    page = (ROOT / "web" / "public" / "takeover.html").read_text(encoding="utf-8")

    assert 'viewer.src = "/vnc/";' in page
    assert "clipboard-read; clipboard-write" in page
    assert 'fetch("/api/takeover/heartbeat"' in page
    assert "setInterval(heartbeat, 20_000)" in page
    # Selkies 需要安全上下文（WebCodecs），明文 HTTP 下给出明确提示。
    assert "window.isSecureContext" in page
    # Xpra 时代的重连与 resize 补丁不应残留。
    assert "jQuery" not in page and "Xpra" not in page


def _run_prepare(tmp_path, **env):
    import os
    import subprocess

    run_env = dict(os.environ)
    run_env.update(TLS_DIR=str(tmp_path / "tls"), LISTEN_CONF=str(tmp_path / "listen.conf"))
    run_env.update(env)
    return subprocess.run(
        ["sh", str(ROOT / "deploy" / "prepare-tls.sh")],
        env=run_env, capture_output=True, text=True, check=False,
    )


def test_prepare_tls_generates_persistent_self_signed_cert(tmp_path):
    import shutil
    import subprocess

    import pytest

    if shutil.which("openssl") is None:
        pytest.skip("需要 openssl")
    result = _run_prepare(tmp_path)
    assert result.returncode == 0, result.stderr
    conf = (tmp_path / "listen.conf").read_text(encoding="utf-8")
    assert "listen 8790 ssl;" in conf
    assert f"ssl_certificate {tmp_path / 'tls' / 'panel.crt'};" in conf
    assert "error_page 497 =301 https://$http_host$request_uri;" in conf
    key = tmp_path / "tls" / "panel.key"
    cert = tmp_path / "tls" / "panel.crt"
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert oct((tmp_path / "tls").stat().st_mode & 0o777) == "0o700"
    text = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-text"],
                          capture_output=True, text=True, check=True).stdout
    assert "CA:FALSE" in text and "TLS Web Server Authentication" in text

    # 再次启动沿用同一张证书（浏览器的「继续访问」例外不失效）。
    before = cert.read_bytes()
    result = _run_prepare(tmp_path)
    assert result.returncode == 0, result.stderr
    assert cert.read_bytes() == before


def test_prepare_tls_off_serves_plain_http(tmp_path):
    result = _run_prepare(tmp_path, PANEL_TLS="off")
    assert result.returncode == 0, result.stderr
    conf = (tmp_path / "listen.conf").read_text(encoding="utf-8")
    assert "listen 8790;" in conf
    assert "ssl" not in conf.replace("PANEL_TLS", "")
    assert not (tmp_path / "tls").exists()


def test_prepare_tls_rejects_unknown_mode_and_half_cert(tmp_path):
    assert _run_prepare(tmp_path, PANEL_TLS="maybe").returncode != 0
    (tmp_path / "tls").mkdir()
    (tmp_path / "tls" / "panel.crt").write_text("x")
    result = _run_prepare(tmp_path)
    assert result.returncode != 0
    assert (tmp_path / "tls" / "panel.crt").read_text() == "x"


def test_panel_proxy_keeps_host_port_and_outer_proto():
    """API 文档的 Base URL 与登录 Cookie 的 Secure 依赖这两个头。"""
    conf = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    panel = conf[conf.index("        location / {"):]
    assert "proxy_set_header Host $http_host;" in panel
    assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in panel
    assert "map $http_x_forwarded_proto $forwarded_proto" in conf
