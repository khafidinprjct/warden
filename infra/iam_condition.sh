#!/usr/bin/env bash
# Checklist I2: bound the warden-core service account to Warden-named machines with an IAM condition (resource name prefix "warden-").
# Warden-launched VMs are named warden-<job_id> (relocations keep the prefix). Machines created by hand must follow the same naming
# to be manageable; the code-level label guard (warden-managed=true) stays in force as well.
#   bash infra/iam_condition.sh apply|test
set -euo pipefail
P=$(cat "$(dirname "$0")/../.gcp_project"); SA="warden-core@$P.iam.gserviceaccount.com"; ROLE="projects/$P/roles/wardenInstanceOperator"
G=${GCLOUD:-/home/ubuntu/google-cloud-sdk/bin/gcloud}
EXPR='resource.type != "compute.googleapis.com/Instance" || resource.name.extract("/instances/{name}").startsWith("warden-")'
case "${1:-}" in
  apply)
    $G projects remove-iam-policy-binding "$P" --member="serviceAccount:$SA" --role="$ROLE" --all --quiet >/dev/null 2>&1 || true
    $G projects add-iam-policy-binding "$P" --member="serviceAccount:$SA" --role="$ROLE" --condition="title=warden-named-instances-only,description=core may act only on instances named warden-*,expression=$EXPR" --quiet | grep -A3 "condition" | head -5
    ;;
  test)
    # negative test: impersonate the core SA and try to touch a machine that is NOT named warden-* (expects PERMISSION_DENIED)
    OTHER=$($G compute instances list --project="$P" --filter="NOT name~^warden-" --format="value(name,zone.basename())" | head -1)
    [ -n "$OTHER" ] || { echo "no non-warden instance to test against"; exit 0; }
    set -- $OTHER
    if $G compute instances add-labels "$1" --zone="$2" --project="$P" --labels=warden-iam-test=1 --impersonate-service-account="$SA" --quiet 2>&1 | grep -qiE "denied|forbidden|403"; then
      echo "NEGATIVE TEST OK: core SA cannot act on $1 (not warden-*)"
    else
      echo "NEGATIVE TEST FAILED: core SA could act on $1"; exit 1
    fi
    ;;
  *) echo "usage: $0 apply|test"; exit 64;;
esac
