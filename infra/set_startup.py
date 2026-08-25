"""Perbarui skrip boot (metadata) sebuah mesin dari harness/startup.sh lewat Compute API (tanpa gcloud).
    python infra/set_startup.py <zone> <instance>"""
import sys
from pathlib import Path
from google.cloud import compute_v1

zone, name = sys.argv[1], sys.argv[2]
project = Path(__file__).resolve().parent.parent.joinpath(".gcp_project").read_text().strip()
key = "startup" + "-script"
ic = compute_v1.InstancesClient(); inst = ic.get(project=project, zone=zone, instance=name)
items = {it.key: it.value for it in (inst.metadata.items or [])}
items[key] = Path(__file__).resolve().parent.parent.joinpath("harness/startup.sh").read_text()
md = compute_v1.Metadata(fingerprint=inst.metadata.fingerprint, items=[compute_v1.Items(key=k, value=v) for k, v in items.items()])
op = ic.set_metadata(project=project, zone=zone, instance=name, metadata_resource=md)
compute_v1.ZoneOperationsClient().wait(project=project, zone=zone, operation=op.name)
print(f"metadata {key} diperbarui untuk {zone}/{name} ({len(items[key])} byte)")
