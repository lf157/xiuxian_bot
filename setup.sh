#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  修仙之路 · 一键部署
#
#  使用方式：
#    首次部署：  sudo bash setup.sh
#    更新代码：  git pull && sudo bash setup.sh
#
#  这个脚本做三件事：
#    1. 构建前端 + Go 网关
#    2. 部署到 /var/www/xiuxian-web
#    3. 配置 systemd + nginx（首次）
# ═══════════════════════════════════════════════════════════
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
WEB_SRC="${ROOT}/xiuxian-web"
WEB_DIST="${WEB_SRC}/dist"
WEB_DEPLOY="/var/www/xiuxian-web"
GW_BIN="/usr/local/bin/xiuxian-gateway"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

echo ""
echo "══════════════════════════════════════"
echo "  修仙之路 · 一键部署"
echo "══════════════════════════════════════"
echo ""

# ──────────────────────────────────────
# 0. 检查 & 安装依赖
# ──────────────────────────────────────
echo "[0] 检查依赖..."

# Node.js
if ! command -v node &>/dev/null; then
    echo "  安装 Node.js..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi
ok "Node $(node -v)"

# pnpm
if ! command -v pnpm &>/dev/null; then
    echo "  安装 pnpm..."
    npm install -g pnpm
fi
ok "pnpm $(pnpm -v)"

# Go
if ! command -v go &>/dev/null; then
    echo "  安装 Go..."
    GO_VER="1.23.4"
    wget -q "https://go.dev/dl/go${GO_VER}.linux-amd64.tar.gz" -O /tmp/go.tar.gz
    rm -rf /usr/local/go
    tar -C /usr/local -xzf /tmp/go.tar.gz
    rm /tmp/go.tar.gz
    export PATH=$PATH:/usr/local/go/bin
    echo 'export PATH=$PATH:/usr/local/go/bin' >> /etc/profile.d/golang.sh
fi
ok "Go $(go version | awk '{print $3}')"

# Redis
if ! command -v redis-cli &>/dev/null; then
    echo "  安装 Redis..."
    apt-get install -y redis-server
    systemctl enable redis-server
    systemctl start redis-server
fi
ok "Redis $(redis-cli --version | awk '{print $2}')"

# Nginx
if ! command -v nginx &>/dev/null; then
    fail "Nginx 未安装，请先安装 Nginx"
fi
ok "Nginx $(nginx -v 2>&1 | awk -F/ '{print $2}')"

echo ""

# ──────────────────────────────────────
# 1. 构建前端
# ──────────────────────────────────────
echo "[1] 构建前端..."
cd "${WEB_SRC}"
pnpm install --dir "${WEB_SRC}" --frozen-lockfile 2>/dev/null || pnpm install --dir "${WEB_SRC}"
pnpm run --dir "${WEB_SRC}" build:fast
ok "前端构建完成"
echo ""

# ──────────────────────────────────────
# 2. 构建 Go 网关
# ──────────────────────────────────────
echo "[2] 构建 Go 网关..."
cd "${WEB_SRC}/gateway"
CGO_ENABLED=0 go build -ldflags="-s -w" -o "${WEB_DIST}/xiuxian-gateway" ./cmd/
ok "网关构建完成 ($(du -h "${WEB_DIST}/xiuxian-gateway" | cut -f1))"
echo ""

# ──────────────────────────────────────
# 3. 部署到 /var/www
# ──────────────────────────────────────
echo "[3] 部署前端到 ${WEB_DEPLOY}..."
mkdir -p "${WEB_DEPLOY}"
rsync -a --delete --exclude='xiuxian-gateway' "${WEB_DIST}/" "${WEB_DEPLOY}/"
ok "前端已部署"

echo "  部署网关到 ${GW_BIN}..."
cp "${WEB_DIST}/xiuxian-gateway" "${GW_BIN}"
chmod +x "${GW_BIN}"
ok "网关已部署"
echo ""

# ──────────────────────────────────────
# 4. 读取 config.json 中的 token
# ──────────────────────────────────────
API_TOKEN=""
CONFIG_FILE="${ROOT}/config.json"
if [ -f "${CONFIG_FILE}" ]; then
    API_TOKEN=$(python3 -c "
import json
d=json.load(open('${CONFIG_FILE}',encoding='utf-8'))
print(d.get('core_server',{}).get('api_token',''))
" 2>/dev/null || echo "")
fi
if [ -z "$API_TOKEN" ]; then
    API_TOKEN="change_me_internal_token"
    warn "未从 config.json 读取到 token，使用默认值"
fi

# ──────────────────────────────────────
# 5. systemd: Go 网关
# ──────────────────────────────────────
echo "[4] 配置 systemd 服务..."

cat > /etc/systemd/system/xiuxian-gateway.service << EOF
[Unit]
Description=XiuXian Go Gateway
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=simple
ExecStart=${GW_BIN} \\
    -listen :8080 \\
    -redis 127.0.0.1:6379 \\
    -backend http://127.0.0.1:11450 \\
    -token ${API_TOKEN}
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xiuxian-gateway
systemctl restart xiuxian-gateway
ok "xiuxian-gateway 服务已启动并设为开机自启"
echo ""

# ──────────────────────────────────────
# 6. Nginx 配置（仅首次）
# ──────────────────────────────────────
NGINX_CONF="/etc/nginx/sites-available/xiuxian"

echo "[5] 配置 Nginx..."
if [ -f "${NGINX_CONF}" ]; then
    ok "Nginx 配置已存在，跳过（如需更新：nano ${NGINX_CONF}）"
else
    echo "  请输入域名（如 game.example.com，回车跳过）："
    read -r DOMAIN
    if [ -z "$DOMAIN" ]; then
        DOMAIN="_"
        warn "未输入域名，使用 server_name _ (匹配所有)"
    fi

    cat > "${NGINX_CONF}" << NGINXEOF
server {
    listen 80;
    server_name ${DOMAIN};

    gzip on;
    gzip_vary on;
    gzip_min_length 256;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml;

    root /var/www/xiuxian-web;
    index index.html;

    # 静态资源强缓存
    location /assets/ {
        expires 365d;
        add_header Cache-Control "public, immutable";
    }

    # Vue SPA 路由
    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # API → Go 网关
    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_buffering off;
        proxy_read_timeout 30s;
    }
}
NGINXEOF

    ln -sf "${NGINX_CONF}" /etc/nginx/sites-enabled/
    # 移除默认站点（避免冲突）
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null
    ok "Nginx 配置已生成: ${DOMAIN}"
fi

nginx -t && systemctl reload nginx
ok "Nginx 已重载"
echo ""

# ──────────────────────────────────────
# 7. 完成
# ──────────────────────────────────────
echo "══════════════════════════════════════"
echo "  部署完成！"
echo "══════════════════════════════════════"
echo ""
echo "  服务状态："
systemctl is-active --quiet redis-server   && ok "Redis         运行中" || warn "Redis         未运行"
systemctl is-active --quiet xiuxian-gateway && ok "Go 网关       运行中" || warn "Go 网关       未运行"
systemctl is-active --quiet nginx          && ok "Nginx         运行中" || warn "Nginx         未运行"
echo ""
echo "  验证："
echo "    curl http://127.0.0.1:8080/api/health"
echo "    curl http://$(hostname -I | awk '{print $1}')/api/health"
echo ""
echo "  Python 后端需要单独启动："
echo "    cd ${ROOT} && python3 start.py"
echo ""
echo "  日后更新只需："
echo "    cd ${ROOT} && git pull && sudo bash setup.sh"
echo ""
