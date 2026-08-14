#!/usr/bin/env bash
# 生成 iOS / iPad PWA 所需的自签证书（HTTPS 必需）
#
# 用法:
#   ./scripts/gen-certs.sh -h <你的.tailnet主机名> -i <局域网IP>
# 例:
#   ./scripts/gen-certs.sh -h mynas.foo.ts.net -i 192.168.2.200
#
# 生成的 cert.pem / key.pem 位于 ./certs，已挂载进 caddy 容器。
# 之后在 iPhone/iPad 上信任根 CA 即可（见脚本末尾提示）。
set -euo pipefail

HOST=""
IP=""
while getopts "h:i:" opt; do
  case "$opt" in
    h) HOST="$OPTARG" ;;
    i) IP="$OPTARG" ;;
    *) echo "用法: $0 -h <host.ts.net> -i <lan-ip>" >&2; exit 1 ;;
  esac
done

OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/certs"
mkdir -p "$OUT_DIR"
CERT="$OUT_DIR/cert.pem"
KEY="$OUT_DIR/key.pem"

SAN="DNS:localhost,IP:127.0.0.1"
[ -n "$IP" ] && SAN="$SAN,IP:$IP"
[ -n "$HOST" ] && SAN="$SAN,DNS:$HOST"

# 1) 自建根 CA
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$OUT_DIR/ca.key" -out "$OUT_DIR/ca.pem" \
  -days 825 -subj "/CN=Memory Worker Local CA" 2>/dev/null

# 2) 服务端证书签名请求
openssl req -newkey rsa:2048 -nodes \
  -keyout "$KEY" -out "$OUT_DIR/csr.pem" \
  -subj "/CN=${HOST:-memory-worker}" 2>/dev/null

# 3) 用根 CA 签发（带 SAN）
cat > "$OUT_DIR/san.cnf" <<EOF
subjectAltName = $SAN
EOF

openssl x509 -req -in "$OUT_DIR/csr.pem" \
  -CA "$OUT_DIR/ca.pem" -CAkey "$OUT_DIR/ca.key" -CAcreateserial \
  -out "$CERT" -days 825 -extfile "$OUT_DIR/san.cnf" 2>/dev/null

echo "✅ 证书已生成: $CERT"
echo "   主题备用名(SAN): $SAN"
echo
echo "─── 下一步：在 iOS 上信任根 CA ───"
echo "  1) 把 $OUT_DIR/ca.pem 传到 iPhone/iPad（AirDrop / 邮件 / 文件 App）并安装描述文件。"
echo "  2) 设置 → 通用 → 关于本机 → 证书信任设置，开启「Memory Worker Local CA」的完全信任。"
echo "  3) 启动代理: docker compose up -d caddy"
echo
echo "─── 或优先使用 Tailscale 节点证书（免自建 CA、内外网一套）───"
echo "  在 NAS 上执行:  tailscale cert ${HOST:-<你的主机名>.ts.net}"
echo "  得到 _cert.pem / _key.pem 后，改名覆盖 ./certs/cert.pem 与 ./certs/key.pem，"
echo "  再 docker compose up -d caddy。iOS 同样只需信任一次 Tailscale 根 CA。"
