#!/usr/bin/env bash
# Job kedua (kontrak penuh): toy_train di bawah wrun; resume otomatis dari checkpoint.
set -uo pipefail
JOB="${WARDEN_JOB:-toy-train}"; BUCKET="$(curl -sf -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/attributes/warden-bucket)"
mkdir -p /opt/job && cd /opt/job && gcloud storage cp "gs://$BUCKET/demo/toy_train.py" toy_train.py -q
python3 -c "import numpy" 2>/dev/null || { apt-get -o DPkg::Lock::Timeout=300 install -y -q python3-numpy >/dev/null 2>&1 || pip3 install -q --break-system-packages numpy; }
mkdir -p "/var/lib/warden/$JOB/artifacts"
WARDEN_BUCKET="$BUCKET" WARDEN_HMAC="$(curl -sf -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/attributes/warden-hmac)" \
  /usr/local/bin/wrun --job "$JOB" --phase train -- python3 /opt/job/toy_train.py --steps "${TOY_STEPS:-3000}" --sleep 0.1
