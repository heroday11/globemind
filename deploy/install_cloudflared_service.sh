#!/bin/bash
# Cloudflare Tunnel 开机自启配置脚本
# 此环境无 systemd，通过修改 /startup.sh 实现开机启动
# 备用脚本：如需手动管理隧道

set -e

CONFIG_DIR="/root/data/cloudflared"

echo "=================================================="
echo " Cloudflare Tunnel 开机自启配置"
echo " 环境: 无 systemd (容器/Docker)"
echo "=================================================="
echo ""

# 检查 /startup.sh 是否已有 cloudflared 启动项
if grep -q 'cloudflared' /startup.sh 2>/dev/null; then
    echo "[OK] cloudflared 启动命令已存在于 /startup.sh"
else
    echo "[正在配置] 添加 cloudflared 到 /startup.sh..."
    sed -i '/# 启动jupyter/i\# 启动cloudflared tunnel\n/root/data/start_cloudflared.sh \&\nsleep 2' /startup.sh
    echo "[OK] 已添加到 /startup.sh"
fi

echo ""
echo "当前 /startup.sh 中的 cloudflared 配置:"
grep -n 'cloudflared\|start_cloudflared' /startup.sh
echo ""
echo "管理命令:"
echo "  启动:   nohup /usr/local/bin/cloudflared --config $CONFIG_DIR/config.yml --no-autoupdate tunnel run > $CONFIG_DIR/tunnel.log 2>&1 &"
echo "  停止:   pkill -f 'cloudflared.*tunnel run'"
echo "  重启:   pkill -f 'cloudflared.*tunnel run'; sleep 1; nohup /usr/local/bin/cloudflared --config $CONFIG_DIR/config.yml --no-autoupdate tunnel run > $CONFIG_DIR/tunnel.log 2>&1 &"
echo "  状态:   cloudflared tunnel info web-tunnel"
echo "  日志:   tail -f $CONFIG_DIR/tunnel.log"
