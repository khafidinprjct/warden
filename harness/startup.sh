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
# resume sadar fase: RUN_START tanpa RUN_FIN dari boot sebelumnya → job terputus (preempt) → jalankan resume
D="/var/lib/warden/$JOB/markers"
if [ -f "$D/RUN_START.json" ] && [ ! -f "$D/RUN_FIN.json" ] && [ -n "$RESUME" ]; then
  echo "startup: RUN_START tanpa RUN_FIN → resume: $RESUME"
  (cd "${WORKDIR:-/}" && WARDEN_HMAC="$HMAC" nohup bash -c "$RESUME" > /var/log/warden-resume.log 2>&1 &)
fi
