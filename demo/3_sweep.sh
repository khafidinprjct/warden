#!/usr/bin/env bash
# Adegan 3: picu steward sekarang (yatim/idle → STOP + proyeksi biaya).
U=${WARDEN_CORE_URL:-https://warden-core-603873318528.us-central1.run.app}
curl -s -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$U/steward" | head -c 400; echo
curl -s -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$U/tick" | head -c 400; echo
