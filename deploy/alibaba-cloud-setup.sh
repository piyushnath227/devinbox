#!/bin/bash
# =============================================================================
# DevInbox - Alibaba Cloud ECS Deployment Script
# =============================================================================
set -e
APP_PORT=8000

echo "[1/5] Updating system..."
apt-get update -qq && apt-get upgrade -y -qq

echo "[2/5] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | bash
fi

echo "[3/5] Configuring firewall..."
if command -v ufw &> /dev/null; then
    ufw --force reset
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw allow ${APP_PORT}/tcp
    ufw --force enable
fi

echo "[4/5] Building and starting DevInbox..."
if [[ -z "${SECRET_KEY:-}" ]]; then
    export SECRET_KEY=$(openssl rand -hex 32)
fi
echo "SECRET_KEY=${SECRET_KEY}" > .env
docker compose up -d --build

echo "[5/5] Waiting for startup..."
sleep 5
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "YOUR_SERVER_IP")

echo ""
echo "=============================================="
echo " 🎉 DevInbox Deployed!"
echo "=============================================="
echo " Dashboard: http://${PUBLIC_IP}:${APP_PORT}/dashboard/"
echo " Webhook:   http://${PUBLIC_IP}:${APP_PORT}/webhook/github"
echo " Health:    http://${PUBLIC_IP}:${APP_PORT}/health"
echo "=============================================="
