#!/bin/bash
# Ease on AWS EC2 (Ubuntu 24.04). Paste this whole file into "Advanced details -> User data" when launching the
# instance. It runs once, as root, on first boot; progress goes to /var/log/ease-setup.log.
set -euxo pipefail
exec > /var/log/ease-setup.log 2>&1

# 4 GB of swap: headroom for Chromium and the one-time build on a 4 GB instance
if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Docker (official convenience script) and the Ease source
curl -fsSL https://get.docker.com | sh
git clone --depth 1 https://github.com/Ease-Autonomous-Multi-Agent-System/Ease.git /opt/ease
chmod +x /opt/ease/deploy/aws/refresh-domain.sh

# Secrets, generated once on this server and never shown anywhere else. They must stay the same for the life of
# the data volume: VAULT_MASTER_KEY encrypts every user's saved API keys.
cd /opt/ease/deploy/aws
umask 077
cat > .env <<EOF
JWT_SECRET=$(openssl rand -base64 48 | tr -d '\n')
VAULT_MASTER_KEY=$(openssl rand -base64 32)
EOF

# Start Ease now and on every boot (re-reads the public IP each time)
cat > /etc/systemd/system/ease.service <<'EOF'
[Unit]
Description=Ease public website
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/ease/deploy/aws/refresh-domain.sh
TimeoutStartSec=1800

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ease.service
