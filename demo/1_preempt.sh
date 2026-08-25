#!/usr/bin/env bash
# Adegan 1: simulasi preempt mesin spot. Fallback: stop biasa (Watcher menangani 'stopped_external').
P=$(cat "$(dirname "$0")/../.gcp_project"); Z=${ZONE:-us-central1-a}; N=${1:-demo-train-1}
gcloud compute instances simulate-maintenance-event "$N" --zone "$Z" --project "$P" --quiet 2>&1 | tail -1 \
  || gcloud compute instances stop "$N" --zone "$Z" --project "$P" --quiet 2>&1 | tail -1
echo "adegan 1: $N → cek dashboard/Discord dalam ≤ 4 menit (2 tick)"
