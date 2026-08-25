PY=.venv/bin/python
ENV=set -a; . $$HOME/.config/warden/.env; set +a;

.PHONY: test smoke emulators dev

test:
	$(PY) -m pytest -q

emulators:
	@pgrep -f "[c]loud-firestore-emulator" >/dev/null || (nohup /home/ubuntu/google-cloud-sdk/bin/gcloud emulators firestore start --host-port=127.0.0.1:8081 --project=warden-local > data/emulator/firestore.log 2>&1 &)
	@pgrep -f "[p]ubsub-emulator" >/dev/null || (nohup /home/ubuntu/google-cloud-sdk/bin/gcloud beta emulators pubsub start --host-port=127.0.0.1:8085 --project=warden-local > data/emulator/pubsub.log 2>&1 &)
	@sleep 3; echo "emulator siap"

smoke: emulators
	$(ENV) WARDEN_DEV=1 $(PY) -m warden.smoke

dev: emulators
	$(ENV) WARDEN_DEV=1 .venv/bin/uvicorn warden.main:app --host 127.0.0.1 --port 8080 --reload
