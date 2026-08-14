#!/bin/bash
# Cloudflared 安装脚本 - Ubuntu/Debian
# 保存到 /root/data/install_cloudflared.sh

set -e

echo "[1/3] 下载 cloudflared..."
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -O /tmp/cloudflared.deb

echo "[2/3] 安装 cloudflared..."
dpkg -i /tmp/cloudflared.deb

echo "[3/3] 清理临时文件..."
rm -f /tmp/cloudflared.deb

echo "安装完成: $(cloudflared --version)"
