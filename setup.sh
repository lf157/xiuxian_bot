#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  修仙之路 · 一键部署（总控脚本）
#
#  逻辑：
#    1) 检查/安装依赖
#    2) 调用 xiuxian-web/build.sh
#    3) 调用 xiuxian-web/deploy.sh
#
#  用法：
#    sudo bash setup.sh              # build + deploy(all)
#    sudo bash setup.sh update-web   # build + deploy(update-web)
#    sudo bash setup.sh update-gw    # build + deploy(update-gw)
# ═══════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="${ROOT}/xiuxian-web"
BUILD_SCRIPT="${WEB_DIR}/build.sh"
DEPLOY_SCRIPT="${WEB_DIR}/deploy.sh"
GO_MOD_FILE="${WEB_DIR}/gateway/go.mod"

ACTION="${1:-all}"
BUILD_TARGET="${BUILD_TARGET:-linux}"
PREFERRED_GO_VERSION="${PREFERRED_GO_VERSION:-1.25.6}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

APT_UPDATED=0

require_root() {
    if [ "${EUID}" -ne 0 ]; then
        fail "请使用 root 执行：sudo bash setup.sh"
    fi
}

ensure_apt_updated() {
    if [ "${APT_UPDATED}" -eq 0 ]; then
        apt-get update -y
        APT_UPDATED=1
    fi
}

normalize_semver() {
    local raw="${1:-0.0.0}"
    raw="$(echo "${raw}" | sed -E 's/[^0-9.].*$//')"
    local major minor patch
    IFS='.' read -r major minor patch <<< "${raw}"
    major="${major:-0}"
    minor="${minor:-0}"
    patch="${patch:-0}"
    echo "${major}.${minor}.${patch}"
}

version_lt() {
    # $1 < $2 => return 0
    [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" != "$2" ]
}

version_max() {
    if version_lt "$1" "$2"; then
        printf '%s' "$2"
    else
        printf '%s' "$1"
    fi
}

install_go_version() {
    local target_version="$1"
    local tar_file="/tmp/go${target_version}.linux-amd64.tar.gz"
    local url="https://go.dev/dl/go${target_version}.linux-amd64.tar.gz"

    echo "  安装 Go ${target_version} ..."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "${url}" -o "${tar_file}"
    elif command -v wget >/dev/null 2>&1; then
        wget -q "${url}" -O "${tar_file}"
    else
        ensure_apt_updated
        apt-get install -y curl
        curl -fsSL "${url}" -o "${tar_file}"
    fi

    rm -rf /usr/local/go
    tar -C /usr/local -xzf "${tar_file}"
    rm -f "${tar_file}"

    ln -sf /usr/local/go/bin/go /usr/local/bin/go
    ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt
    export PATH="/usr/local/go/bin:/usr/local/bin:${PATH}"
    hash -r
}

echo ""
echo "══════════════════════════════════════"
echo "  修仙之路 · 一键部署"
echo "══════════════════════════════════════"
echo ""

case "${ACTION}" in
    all|update-web|update-gw) ;;
    *)
        fail "参数错误：${ACTION}（可选：all | update-web | update-gw）"
        ;;
esac

require_root
[ -d "${WEB_DIR}" ] || fail "未找到目录: ${WEB_DIR}"
[ -f "${BUILD_SCRIPT}" ] || fail "未找到脚本: ${BUILD_SCRIPT}"
[ -f "${DEPLOY_SCRIPT}" ] || fail "未找到脚本: ${DEPLOY_SCRIPT}"
[ -f "${GO_MOD_FILE}" ] || fail "未找到 Go 模块文件: ${GO_MOD_FILE}"

export PATH="/root/.local/share/pnpm:/usr/local/go/bin:/usr/local/bin:${PATH}"

echo "[0] 检查依赖..."

# Node.js
if ! command -v node >/dev/null 2>&1; then
    echo "  安装 Node.js 20..."
    ensure_apt_updated
    apt-get install -y ca-certificates curl gnupg
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
fi
ok "Node $(node -v)"

# pnpm
if ! command -v pnpm >/dev/null 2>&1; then
    echo "  安装 pnpm..."
    if command -v corepack >/dev/null 2>&1; then
        corepack enable >/dev/null 2>&1 || true
        corepack prepare pnpm@latest --activate
    elif command -v npm >/dev/null 2>&1; then
        npm install -g pnpm
    else
        fail "未找到 npm/corepack，无法安装 pnpm"
    fi
fi
ok "pnpm $(pnpm -v)"

# Go（默认 1.25.6，且不低于 go.mod 需求）
REQUIRED_GO_RAW="$(awk '/^go / {print $2; exit}' "${GO_MOD_FILE}")"
REQUIRED_GO="$(normalize_semver "${REQUIRED_GO_RAW}")"
PREFERRED_GO="$(normalize_semver "${PREFERRED_GO_VERSION}")"
TARGET_GO="$(version_max "${REQUIRED_GO}" "${PREFERRED_GO}")"

CURRENT_GO="0.0.0"
if command -v go >/dev/null 2>&1; then
    CURRENT_GO_RAW="$(go version | awk '{print $3}' | sed 's/^go//')"
    CURRENT_GO="$(normalize_semver "${CURRENT_GO_RAW}")"
fi

if ! command -v go >/dev/null 2>&1 || version_lt "${CURRENT_GO}" "${TARGET_GO}"; then
    warn "当前 Go ${CURRENT_GO} < 目标 ${TARGET_GO}，开始升级"
    install_go_version "${TARGET_GO}"
    CURRENT_GO_RAW="$(go version | awk '{print $3}' | sed 's/^go//')"
    CURRENT_GO="$(normalize_semver "${CURRENT_GO_RAW}")"
fi

if version_lt "${CURRENT_GO}" "${TARGET_GO}"; then
    fail "Go 版本仍不足（当前 ${CURRENT_GO}，要求 >= ${TARGET_GO}）"
fi
ok "Go $(go version | awk '{print $3}')"

# rsync
if ! command -v rsync >/dev/null 2>&1; then
    echo "  安装 rsync..."
    ensure_apt_updated
    apt-get install -y rsync
fi
ok "rsync $(rsync --version | head -n1 | awk '{print $3}')"

# Redis
if ! command -v redis-cli >/dev/null 2>&1; then
    echo "  安装 Redis..."
    ensure_apt_updated
    apt-get install -y redis-server
fi
systemctl enable redis-server >/dev/null 2>&1 || true
systemctl restart redis-server >/dev/null 2>&1 || true
ok "Redis $(redis-cli --version | awk '{print $2}')"

# Nginx
if ! command -v nginx >/dev/null 2>&1; then
    echo "  安装 Nginx..."
    ensure_apt_updated
    apt-get install -y nginx
fi
systemctl enable nginx >/dev/null 2>&1 || true
ok "Nginx $(nginx -v 2>&1 | awk -F/ '{print $2}')"
echo ""

echo "[1] 调用 xiuxian-web/build.sh ..."
chmod +x "${BUILD_SCRIPT}"
if [ "${BUILD_TARGET}" = "linux" ]; then
    bash "${BUILD_SCRIPT}"
else
    bash "${BUILD_SCRIPT}" "${BUILD_TARGET}"
fi
ok "build.sh 执行完成"
echo ""

echo "[2] 调用 xiuxian-web/deploy.sh ${ACTION} ..."
chmod +x "${DEPLOY_SCRIPT}"
bash "${DEPLOY_SCRIPT}" "${ACTION}"
ok "deploy.sh 执行完成"
echo ""

echo "══════════════════════════════════════"
echo "  部署完成"
echo "══════════════════════════════════════"
echo ""
echo "  服务状态："
systemctl is-active --quiet redis-server && ok "Redis            运行中" || warn "Redis            未运行"
systemctl is-active --quiet xiuxian-gateway && ok "Go 网关          运行中" || warn "Go 网关          未运行"
systemctl is-active --quiet nginx && ok "Nginx            运行中" || warn "Nginx            未运行"
systemctl is-active --quiet xiuxian && ok "Python 后端      运行中" || warn "Python 后端      未运行"
echo ""
echo "  快速验证："
echo "    curl http://127.0.0.1:8080/api/health"
echo ""
