#!/usr/bin/env bash
# startup-script Compute Engine (metadata): dijalankan tiap boot (termasuk setelah preempt → start).
# Metadata: warden-job, warden-core-url, warden-hmac, warden-bucket, warden-entry, warden-resume-cmd, warden-workdir, warden-harness-url
set -uo pipefail
md() { curl -sf -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" || true; }
JOB=$(md warden-job); CORE=$(md warden-core-url); HMAC=$(md warden-hmac); BUCKET=$(md warden-bucket); ENTRY=$(md warden-entry)
RESUME=$(md warden-resume-cmd); WORKDIR=$(md warden-workdir); HURL=$(md warden-harness-url)
[ -n "$JOB" ] || { echo "startup: metadata warden-job kosong — bukan mesin Warden"; exit 0; }
mkdir -p /opt/warden-src && cd /opt/warden-src
[ -n "$HURL" ] && gcloud storage cp -r "$HURL/*" /opt/warden-src/ -q 2>/dev/null || true
WARDEN_JOB="$JOB" WARDEN_CORE_URL="$CORE" WARDEN_HMAC="$HMAC" WARDEN_BUCKET="$BUCKET" WARDEN_ENTRY="$ENTRY" \
  WARDEN_RESUME_CMD="$RESUME" WARDEN_WORKDIR="${WORKDIR:-/}" bash /opt/warden-src/install.sh || echo "startup: preflight gagal (marker ditulis)"
# peluncuran/resume sadar fase: belum ada RUN_FIN (boot pertama, atau RUN_START terputus oleh preempt) → jalankan RESUME
D="/var/lib/warden/$JOB/markers"
# selesai hanya bila RUN_FIN milik run yang SAMA dengan RUN_START (RUN_FIN run lama ≠ selesai)
SELESAI=$(python3 - "$D" <<'PY'
import json, os, sys
d = sys.argv[1]
try:
    s = json.load(open(os.path.join(d, "RUN_START.json"))).get("run_id"); f = json.load(open(os.path.join(d, "RUN_FIN.json"))).get("run_id")
    print("ya" if s and f and s == f else "tidak")
except Exception:
    print("ya" if os.path.exists(os.path.join(d, "RUN_FIN.json")) and not os.path.exists(os.path.join(d, "RUN_START.json")) else "tidak")
PY
)
if [ "$SELESAI" != "ya" ] && [ -n "$RESUME" ]; then
  echo "startup: belum ada RUN_FIN ($( [ -f "$D/RUN_START.json" ] && echo 'RUN_START ada = lanjut setelah terputus' || echo 'boot pertama')) → $RESUME"
  mkdir -p "${WORKDIR:-/}"; (cd "${WORKDIR:-/}" && WARDEN_HMAC="$HMAC" nohup bash -c "$RESUME" > /var/log/warden-resume.log 2>&1 &)
fi
