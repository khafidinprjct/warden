#!/usr/bin/env bash
# install.sh — pasang harness Warden di mesin (idempoten, stdlib saja, tanpa pip).
#   sudo WARDEN_JOB=<id> WARDEN_CORE_URL=https://... WARDEN_HMAC=<rahasia> [WARDEN_BUCKET=<bucket>] [WARDEN_ENTRY=<substr>] bash install.sh
set -euo pipefail
: "${WARDEN_JOB:?WARDEN_JOB is required}"; : "${WARDEN_CORE_URL:?WARDEN_CORE_URL is required}"; : "${WARDEN_HMAC:?WARDEN_HMAC is required}"
SRC="$(cd "$(dirname "$0")" && pwd)"
install -d /opt/warden /etc/warden "/var/lib/warden/$WARDEN_JOB/markers" "/var/lib/warden/$WARDEN_JOB/artifacts" /run/lock
install -m 0755 "$SRC/wrun" /usr/local/bin/wrun
install -m 0755 "$SRC/warden-agent.py" /opt/warden/warden-agent.py
install -m 0644 "$SRC/warden_beat.py" /opt/warden/warden_beat.py
install -m 0644 "$SRC/warden-agent.service" /etc/systemd/system/warden-agent.service
cat > /etc/warden/agent.env <<ENV
WARDEN_JOB=$WARDEN_JOB
WARDEN_CORE_URL=$WARDEN_CORE_URL
WARDEN_HMAC=$WARDEN_HMAC
WARDEN_BUCKET=${WARDEN_BUCKET:-}
WARDEN_ENTRY=${WARDEN_ENTRY:-}
WARDEN_RESUME_CMD=${WARDEN_RESUME_CMD:-true}
WARDEN_WORKDIR=${WARDEN_WORKDIR:-/}
WARDEN_DIR=/var/lib/warden
ENV
chmod 0600 /etc/warden/agent.env
# --- PREFLIGHT (mode #12): python3, disk, (opsional) torch/cuda ---
FAIL=""
python3 -c "import json,hashlib,hmac,urllib.request" || FAIL="python3 stdlib rusak"
AVAIL=$(df -BG --output=avail / | tail -1 | tr -dc '0-9'); [ "${AVAIL:-0}" -ge "${WARDEN_MIN_DISK_GB:-5}" ] || FAIL="disk sisa ${AVAIL}G < ${WARDEN_MIN_DISK_GB:-5}G"
if [ -n "${WARDEN_PREFLIGHT_PY:-}" ]; then $WARDEN_PREFLIGHT_PY -c "import torch; assert torch.cuda.is_available()" 2>/dev/null || FAIL="torch/cuda tidak siap"; fi
if [ -n "$FAIL" ]; then
  echo "{\"kind\":\"PREFLIGHT_FAIL\",\"job_id\":\"$WARDEN_JOB\",\"ts\":\"$(date -u -Iseconds)\",\"reason\":\"$FAIL\"}" > "/var/lib/warden/$WARDEN_JOB/markers/PREFLIGHT_FAIL.json"
  echo "PREFLIGHT FAILED: $FAIL" >&2
fi
systemctl daemon-reload && systemctl enable --now warden-agent >/dev/null 2>&1 || true
echo "harness terpasang: job=$WARDEN_JOB core=$WARDEN_CORE_URL preflight=${FAIL:-OK}"
[ -z "$FAIL" ]
