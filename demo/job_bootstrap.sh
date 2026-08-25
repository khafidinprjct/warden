#!/usr/bin/env bash
# Dijalankan di mesin demo oleh startup (lewat warden-resume-cmd) — memasang dependensi sekali, menarik payload
# pipeline climate dari GCS, lalu menjalankan pipeline di bawah wrun (marker+denyut+artefak).
set -uo pipefail
JOB="${WARDEN_JOB:-climate-demo}"; BUCKET="$(curl -sf -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/attributes/warden-bucket)"
WD=/opt/job; mkdir -p "$WD"; cd "$WD"
if [ ! -f .deps_ok ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o DPkg::Lock::Timeout=300 update -q >/dev/null 2>&1 && apt-get -o DPkg::Lock::Timeout=300 install -y -q python3-pip python3-venv >/dev/null 2>&1
  python3 -m venv venv && ./venv/bin/pip install -q --upgrade pip
  ./venv/bin/pip install -q numpy pandas scikit-learn scipy lightgbm xgboost catboost h3 pyarrow optuna pygam tabicl torch --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple \
    && ./venv/bin/python -c "import numpy, sklearn, lightgbm, xgboost, catboost, tabicl, h3, pygam" && touch .deps_ok || { echo "DEPS GAGAL" ; exit 3; }
fi
gcloud storage cp "gs://$BUCKET/demo/climate_payload.tgz" payload.tgz -q && tar xzf payload.tgz && rm -f payload.tgz
mkdir -p "/var/lib/warden/$JOB/artifacts"
cd "$WD/climate-health" && WARDEN_ARTIFACTS="" WARDEN_HMAC="$(curl -sf -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/attributes/warden-hmac)" \
  /usr/local/bin/wrun --job "$JOB" --phase F0 -- bash -c '
    ../venv/bin/python run_pipeline.py --fast --jobs 2 --folds 2 --repeats 1 --optuna 0
    EX=$?
    cp submissions/smoke/submission_v16d.csv /var/lib/warden/'"$JOB"'/artifacts/pred.csv 2>/dev/null
    python3 - <<PY
import csv, json
rows = sum(1 for _ in open("/var/lib/warden/'"$JOB"'/artifacts/pred.csv")) - 1
json.dump({"rows": {"pred.csv": rows}}, open("/var/lib/warden/'"$JOB"'/evidence.json", "w"))
PY
    exit $EX'
