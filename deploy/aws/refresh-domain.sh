#!/bin/bash
# Runs at every boot (systemd unit "ease"): point the site's address at this server's current public IP, then
# start Ease. The address is <ip-with-dashes>.sslip.io - a free DNS name that resolves to that IP, so Caddy can
# get a real HTTPS certificate without buying a domain.
set -euo pipefail
cd /opt/ease/deploy/aws

TOKEN=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
IP=$(curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)
DOMAIN="${IP//./-}.sslip.io"

if grep -q '^EASE_DOMAIN=' .env; then
  sed -i "s/^EASE_DOMAIN=.*/EASE_DOMAIN=${DOMAIN}/" .env
else
  echo "EASE_DOMAIN=${DOMAIN}" >> .env
fi
echo "https://${DOMAIN}" > /opt/ease/URL.txt

docker compose up -d --build
echo "Ease is at https://${DOMAIN}"
