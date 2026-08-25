"""Rotate the harness HMAC without downtime (Phase 12).
1. add a new Secret Manager version of warden-ingest-hmac
2. point warden-core at it while keeping the previous version as WARDEN_INGEST_HMAC_SECRET_PREV (grace window)
3. write the new secret into instance metadata `warden-hmac` of every warden-managed instance (agents pick it up on restart/boot)
Usage: python -m infra.rotate_hmac [--finish]   (--finish removes the previous secret from core after agents restarted)"""
import os, secrets, subprocess, sys
from pathlib import Path

P = Path(__file__).resolve().parents[1].joinpath(".gcp_project").read_text().strip()
G = os.environ.get("GCLOUD", "gcloud"); REGION = "us-central1"; SECRET = "warden-ingest-hmac"


def sh(*a, check=True):
    return subprocess.run(list(a), capture_output=True, text=True, check=check).stdout.strip()


def main(finish: bool):
    if finish:
        sh(G, "run", "services", "update", "warden-core", "--region", REGION, "--project", P, "--remove-env-vars", "WARDEN_INGEST_HMAC_SECRET_PREV", "--quiet")
        print("previous secret removed from warden-core"); return
    versions = sh(G, "secrets", "versions", "list", SECRET, "--project", P, "--filter", "state=enabled", "--format", "value(name)").split()
    current = versions[0] if versions else None
    new = secrets.token_hex(32)
    ver = sh("bash", "-c", f"printf %s '{new}' | {G} secrets versions add {SECRET} --project {P} --data-file=- --format='value(name)'")
    print("new version:", ver)
    prev_val = sh(G, "secrets", "versions", "access", current, "--secret", SECRET, "--project", P) if current else ""
    sh(G, "run", "services", "update", "warden-core", "--region", REGION, "--project", P, "--update-secrets", f"WARDEN_INGEST_HMAC_SECRET={SECRET}:{ver.split('/')[-1]}",
       "--update-env-vars", f"WARDEN_INGEST_HMAC_SECRET_PREV={prev_val}", "--quiet")
    print("warden-core: new secret active, previous accepted during the grace window")
    insts = sh(G, "compute", "instances", "list", "--project", P, "--filter", "labels.warden-managed=true", "--format", "value(name,zone.basename())").splitlines()
    for line in insts:
        if not line.strip():
            continue
        name, zone = line.split()
        sh(G, "compute", "instances", "add-metadata", name, "--zone", zone, "--project", P, "--metadata", f"warden-hmac={new}")
        print("metadata updated:", name)
    print("done — restart warden-agent on each instance (or let the next boot pick it up), then run with --finish")


if __name__ == "__main__":
    main("--finish" in sys.argv)
