#!/usr/bin/env bash
# vm_create.sh — mesin demo/latihan yang dijaga Warden (spot, STOP, no-auto-delete, label managed).
# Harness Warden = gerbang praterbangnya sendiri (preflight di install.sh, marker exit code, denyut, resume).
#   bash infra/vm_create.sh <nama> <job_id> [machine_type=e2-medium] [zone=us-central1-a]
set -euo pipefail
NAME="${1:?nama}"; JOB="${2:?job_id}"; MT="${3:-e2-medium}"; ZONE="${4:-us-central1-a}"
P=$(cat "$(dirname "$0")/../.gcp_project"); CORE_URL="${WARDEN_CORE_URL:?WARDEN_CORE_URL}"; HMAC="${WARDEN_HMAC:?WARDEN_HMAC}"
BUCKET="$P-warden"
gcloud storage cp -r "$(dirname "$0")/../harness/"* "gs://$BUCKET/harness/" -q
gcloud compute instances create "$NAME" --project="$P" --zone="$ZONE" --machine-type="$MT" \
  --provisioning-model=SPOT --instance-termination-action=STOP --no-boot-disk-auto-delete \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud --boot-disk-size=20GB --boot-disk-type=pd-balanced \
  --service-account="warden-vm@$P.iam.gserviceaccount.com" --scopes=cloud-platform \
  --labels="warden-managed=true,warden-job=$JOB" \
  --metadata="warden-job=$JOB,warden-core-url=$CORE_URL,warden-hmac=$HMAC,warden-bucket=$BUCKET,warden-harness-url=gs://$BUCKET/harness,warden-entry=${WARDEN_ENTRY:-run_pipeline.py},warden-resume-cmd=${WARDEN_RESUME_CMD:-},warden-workdir=${WARDEN_WORKDIR:-/opt/job}" \
  --metadata-from-file=startup-script="$(dirname "$0")/../harness/startup.sh" --format="table(name,zone.basename(),status,scheduling.provisioningModel)"
echo "ledger: $ZONE/$NAME job=$JOB — Warden melihatnya di tick berikutnya"
