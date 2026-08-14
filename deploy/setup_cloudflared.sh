#!/bin/bash
# Cloudflare Tunnel 一键恢复脚本
# 服务器释放重分配后，运行此脚本恢复隧道
# 使用方法: bash /root/data/setup_cloudflared.sh

set -e

CONFIG_DIR="/root/data/cloudflared"

echo "========================================"
echo " Cloudflare Tunnel 一键恢复脚本"
echo "========================================"

# Step 1: 安装 cloudflared
echo ""
echo "[1/6] 安装 cloudflared..."
if command -v cloudflared &>/dev/null; then
    echo "  → cloudflared 已安装: $(cloudflared --version)"
else
    bash /root/data/install_cloudflared.sh
fi

# Step 2: 恢复认证文件
echo ""
echo "[2/6] 恢复认证文件..."
if [ -f "$CONFIG_DIR/cert.pem" ]; then
    mkdir -p /root/.cloudflared
    cp "$CONFIG_DIR/cert.pem" /root/.cloudflared/cert.pem
    chmod 600 /root/.cloudflared/cert.pem
    echo "  → 认证文件已从 /root/data/cloudflared/cert.pem 恢复"
else
    echo "  → 认证文件缺失！重新认证方法："
    echo "    1. 在本机电脑安装 cloudflared"
    echo "    2. 运行: cloudflared tunnel login"
    echo "    3. 将 ~/.cloudflared/cert.pem 上传到服务器 $CONFIG_DIR/cert.pem"
    exit 1
fi

# Step 3: 检查隧道配置
echo ""
echo "[3/6] 检查隧道配置..."
if [ -f "$CONFIG_DIR/config.yml" ] && [ -f "$CONFIG_DIR/credentials.json" ]; then
    echo "  → 配置文件就绪"
    chmod 600 "$CONFIG_DIR/credentials.json"
else
    echo "  → 配置文件缺失，需要重新创建隧道"
    echo "    请从备份恢复或重新运行完整配置流程"
    exit 1
fi

# Step 4: 添加到 /startup.sh（开机自启）
echo ""
echo "[4/6] 配置开机自启..."
if grep -q 'cloudflared' /startup.sh 2>/dev/null; then
    echo "  → cloudflared 启动命令已存在于 /startup.sh"
else
    sed -i '/# 启动jupyter/i\# 启动cloudflared tunnel\n/root/data/start_cloudflared.sh \&\nsleep 2' /startup.sh
    echo "  → 已添加 cloudflared 启动到 /startup.sh"
fi

# Step 5: 确保启动脚本就绪
echo ""
echo "[5/6] 确保启动脚本就绪..."
if [ ! -f /root/data/start_cloudflared.sh ]; then
    echo "  → 错误: /root/data/start_cloudflared.sh 缺失！"
    exit 1
fi
chmod +x /root/data/start_cloudflared.sh
echo "  → 启动脚本就绪"

# Step 6: 启动隧道
echo ""
echo "[6/6] 启动 cloudflared 隧道..."
# 先杀死旧进程
pkill -f 'cloudflared.*tunnel run' 2>/dev/null || true
sleep 1
# 启动隧道
nohup /usr/local/bin/cloudflared --config /root/data/cloudflared/config.yml --no-autoupdate tunnel run \
    > /root/data/cloudflared/tunnel.log 2>&1 &
echo "  → 隧道启动命令已执行 (PID: $!)"

echo ""
echo "========================================"
echo " 恢复完成！"
echo "========================================"
echo ""
echo "查看状态: cloudflared tunnel info web-tunnel"
echo "查看日志: tail -f /root/data/cloudflared/tunnel.log"
echo "启动Web服务: 先 conda activate Globemind_env，再运行你的服务命令"
echo "测试访问: curl -sI https://globemind.top"
