#!/usr/bin/env bash
# 外向き通信の制限（server_setup.md §5.3、NET-12）。社内のアドレス帯を引数で渡す（複数可）。
#   sudo ./firewall.sh 192.168.0.0/16 10.0.0.0/8
set -euo pipefail
if [ $# -lt 1 ]; then
  echo "usage: $0 <社内のアドレス帯>..." >&2
  exit 2
fi
FRONTEND=172.30.0.0/24
EGRESS=172.31.0.0/24
for lan in "$@"; do
  # frontend（gateway・grafana）から社内LAN以外への新規接続を落とす
  iptables -C DOCKER-USER -s "$FRONTEND" ! -d "$lan" -m conntrack --ctstate NEW -j DROP 2>/dev/null ||
    iptables -I DOCKER-USER -s "$FRONTEND" ! -d "$lan" -m conntrack --ctstate NEW -j DROP
  # web-relay から社内LANへの接続を禁止（SSRF 対策の多重化）
  iptables -C DOCKER-USER -s "$EGRESS" -d "$lan" -j DROP 2>/dev/null ||
    iptables -I DOCKER-USER -s "$EGRESS" -d "$lan" -j DROP
done
netfilter-persistent save
echo "DOCKER-USER rules applied for: $*"
