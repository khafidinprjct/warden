#!/usr/bin/env bash
# Adegan 2: suntik artefak korup lewat mailbox (agent memotong CSV + NaN), lalu job menulis RUN_FIN exit 0.
cd "$(dirname "$0")/.." && set -a && . ~/.config/warden/.env && set +a && unset FIRESTORE_EMULATOR_HOST
WARDEN_PROJECT=$GOOGLE_CLOUD_PROJECT WARDEN_FIRESTORE_DB= .venv/bin/python -c "
from warden.store import firestore as db
db.client().collection('cmd').document('${1:-climate-demo}').set({'cmd': 'inject', 'args': {'what': 'corrupt_csv'}, 'by': 'demo'})
print('adegan 2: inject corrupt_csv dikirim ke ${1:-climate-demo}')"
