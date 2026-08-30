#!/usr/bin/env bash
# One-shot Discord wiring for Warden (checklist K4). Everything else is already built — this only carries the
# credentials from the Discord developer portal to Cloud Run, registers the slash commands and proves the channel works.
#
#   WARDEN_DISCORD_APP_ID=… WARDEN_DISCORD_PUBLIC_KEY=… WARDEN_DISCORD_BOT_TOKEN=… \
#   WARDEN_DISCORD_CHANNEL_ID=… WARDEN_APPROVERS=… bash infra/discord_setup.sh
#
# The bot token is the only secret: it goes to Secret Manager and is referenced by the service, never passed as a
# plain environment value. The public key, application id and channel id are not secrets.
set -euo pipefail

PROJECT="$(cat "$(dirname "$0")/../.gcp_project")"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-warden-core}"

for v in WARDEN_DISCORD_APP_ID WARDEN_DISCORD_PUBLIC_KEY WARDEN_DISCORD_BOT_TOKEN WARDEN_DISCORD_CHANNEL_ID; do
  if [ -z "${!v:-}" ]; then echo "missing $v" >&2; exit 64; fi
done
APPROVERS="${WARDEN_APPROVERS:-}"

echo "1/5 · bot token → Secret Manager"
if gcloud secrets describe warden-discord-bot --project "$PROJECT" >/dev/null 2>&1; then
  printf '%s' "$WARDEN_DISCORD_BOT_TOKEN" | gcloud secrets versions add warden-discord-bot --data-file=- --project "$PROJECT" >/dev/null
else
  printf '%s' "$WARDEN_DISCORD_BOT_TOKEN" | gcloud secrets create warden-discord-bot --data-file=- --replication-policy=automatic --project "$PROJECT" >/dev/null
fi
SA="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(spec.template.spec.serviceAccountName)')"
gcloud secrets add-iam-policy-binding warden-discord-bot --member="serviceAccount:${SA}" \
  --role=roles/secretmanager.secretAccessor --project "$PROJECT" >/dev/null
echo "     secret ready, readable by ${SA}"

echo "2/5 · wiring the service"
gcloud run services update "$SERVICE" --region "$REGION" --project "$PROJECT" --quiet \
  --update-env-vars "WARDEN_DISCORD_PUBLIC_KEY=${WARDEN_DISCORD_PUBLIC_KEY},WARDEN_DISCORD_CHANNEL_ID=${WARDEN_DISCORD_CHANNEL_ID},WARDEN_APPROVERS=${APPROVERS}" \
  --update-secrets "WARDEN_DISCORD_BOT_TOKEN=warden-discord-bot:latest" >/dev/null
CORE="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.url)')"
echo "     $SERVICE updated · interactions endpoint: ${CORE}/discord/interactions"

echo "3/5 · waiting for the new revision to serve"
for _ in $(seq 1 40); do
  rev="$(curl -sS --http1.1 --max-time 20 "${CORE}/health" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("revision",""))' 2>/dev/null || true)"
  latest="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.latestReadyRevisionName)')"
  [ -n "$rev" ] && [ "$rev" = "$latest" ] && { echo "     $rev"; break; }
  sleep 8
done

echo "4/5 · registering the /warden slash commands"
python3 "$(dirname "$0")/discord_register.py"

echo "5/5 · sending a test card to the channel"
python3 - <<'PY'
import os, httpx
tok, ch = os.environ["WARDEN_DISCORD_BOT_TOKEN"], os.environ["WARDEN_DISCORD_CHANNEL_ID"]
r = httpx.post(f"https://discord.com/api/v10/channels/{ch}/messages",
               headers={"Authorization": f"Bot {tok}", "Content-Type": "application/json"},
               json={"embeds": [{"title": "Warden is connected",
                                 "description": "Incident cards will arrive here. Approvals from this channel go through the same policy path as the dashboard.",
                                 "color": 0x2F5FD6}]}, timeout=20)
print("     channel:", r.status_code, "" if r.status_code < 300 else r.text[:200])
r.raise_for_status()
PY

echo
echo "done. Set the Interactions Endpoint URL in the Discord developer portal to:"
echo "    ${CORE}/discord/interactions"
echo "Discord will call it once to verify the signature; the endpoint answers that check already."
