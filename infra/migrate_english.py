"""One-off: translate Indonesian runtime text already stored in Firestore (incidents, decisions, evidence) to English.
Idempotent. Usage: WARDEN_PROJECT=... WARDEN_FIRESTORE_DB= python -m infra.migrate_english [--dry]"""
import re, sys
from warden.store import firestore as db

RULES = [
    (r"TERMINATED tanpa RUN_FIN \((preempt|dihentikan dari luar)\); job (\S+) fase (\S+)", lambda m: f"TERMINATED without RUN_FIN ({'preempted' if m.group(1)=='preempt' else 'stopped externally'}); job {m.group(2)} phase {m.group(3)}"),
    (r"hidup \$([0-9.]+)/jam tanpa job aktif", r"running at $\1/h with no active job"),
    (r"job (\S+) exit 0 — menunggu verifikasi artefak", r"job \1 exit 0 — awaiting artifact verification"),
    (r"job (\S+) berakhir exit=(\d+)", r"job \1 ended with exit=\2"),
    (r"selesai ≠ utuh — (\d+)/(\d+) artefak gagal:", r"finished ≠ intact — \1/\2 artifacts failed:"),
    (r"artefak tidak tersedia untuk diverifikasi", "artifact not available for verification"),
    (r"diharapkan tapi tidak ada di RUN_FIN \(artefak tidak mendarat\)", "expected but not in RUN_FIN (artifact did not land)"),
    (r"(\d+) artefak belum tersedia — tunggu unggahan", r"\1 artifact(s) not yet available — waiting for upload"),
    (r"(\d+)/(\d+) artefak lolos", r"\1/\2 artifacts passed"),
    (r"artefak terbuka & utuh", "artifacts opened & intact"), (r"VERIFIED ditulis", "VERIFIED written"),
    (r"artefak dikarantina; butuh manusia untuk rerun/rollback", "artifact quarantined; human needed for rerun/rollback"),
    (r"sudah dikarantina trainer — tidak dinilai", "already quarantined by trainer — not evaluated"),
    (r"^aturan (\S+)$", r"rule \1"), (r"butuh diagnosis LLM", "LLM diagnosis required"),
    (r"diminta-vs-jadi cocok", "requested vs observed match"), (r"^diminta-vs-jadi$", "requested vs observed match"),
    (r"ditolak kebijakan", "denied by policy"), (r"izin kedaluwarsa", "approval expired"),
    (r"^disetujui (.+)$", r"approved by \1"), (r"^ditolak (\S+): ?(.*)$", r"denied by \1: \2"),
    (r"pagar keras lolos; tingkat awal (L\d)", r"hard guards passed; base level \1"),
    (r"circuit terpicu \(aksi/jam atau verifikasi gagal\) → L1", "circuit breaker tripped (actions/hour or failed verifications) → L1"),
    (r"circuit OPEN → turun ke L1", "circuit breaker OPEN → downgraded to L1"),
    (r"L1: minta izin manusia", "L1: human approval required"), (r"^(L\d): otomatis$", r"\1: automatic"), (r"L0: hanya amati", "L0: observe only"),
    (r"job legacy \(sinyal sintetis\) → L1", "legacy job (synthetic signals) → L1"),
    (r"pagu belanja otomatis harian → L1", "daily auto-spend cap → L1"),
    (r"biaya \$([0-9.]+) > \$([0-9.]+) → L1", r"cost $\1 > $\2 → L1"),
    (r"confidence ([0-9.]+) < 0,7 → L1", r"confidence \1 < 0.7 → L1"),
    (r"cek silang/vonis kedua meminta manusia → L1", "crosscheck/second opinion requires human → L1"),
    (r"Warden DIBEKUKAN \(tombol merah\)", "Warden FROZEN (red button)"), (r"TOLAK: ", "DENY: "), (r"TAHAN: ", "HOLD: "),
    (r"tindakan dilarang keras", "action is hard-denied"), (r"mesin tidak berlabel warden-managed", "instance not labeled warden-managed"),
    (r"batas (\d+)/jam tercapai", r"limit \1/hour reached"), (r"batas (\d+)/hari tercapai", r"limit \1/day reached"),
    (r"^dinilai ulang oleh (\S+) dari (\S+)$", r"re-evaluated by \1 from \2"), (r"^dinilai ulang → (.*)$", r"re-evaluated → \1"),
    (r"denyut basi ([0-9.]+) mnt \(> ([0-9.]+)\) DAN mesin diam", r"heartbeat stale \1 min (> \2) AND machine idle"),
    (r"harness tidak berdenyut ([0-9.]+) mnt saat mesin RUNNING", r"no harness heartbeat for \1 min while machine RUNNING"),
    (r"marker DONE tanpa RUN_FIN/exit code — TIDAK diterima", "DONE marker without RUN_FIN/exit code — NOT accepted"),
    (r"RUN_FIN tidak sah: ", "RUN_FIN invalid: "), (r"tanpa exit_code", "missing exit_code"), (r"tanpa run_id", "missing run_id"), (r"tanda tangan tidak cocok", "signature mismatch"),
    (r"loss non-finite di step (\d+)", r"non-finite loss at step \1"), (r"idle ≥ (\d+) mnt", r"idle ≥ \1 min"),
    (r"(\d+) proses entrypoint berjalan bersamaan", r"\1 entrypoint processes running concurrently"),
    (r"disk sisa ([0-9.]+) GB \(butuh ≥ ([0-9.]+)\)", r"disk free \1 GB (need ≥ \2)"),
    (r"Gemini gagal: ", "Gemini failed: "), (r"(\d+) baris log", r"\1 log lines"), (r"size_nonzero: 0 byte", "size_nonzero: 0 bytes"),
    (r"lease job dipegang pihak lain \(anti balapan\)", "job lease held by another party (race guard)"),
    (r"instance tidak ada", "instance not found"), (r"^dikirim$", "sent"),
    # operator notes written by hand on 25 Aug
    (r"START disetujui pemilik \(25 Agu 18:25 WIB\) setelah keputusan L1 kedaluwarsa; dijalankan operator via gcloud compute start \(human:inyongkhafid → operator\)",
     "START approved by the owner (25 Aug 17:25 WIB) after the L1 decision expired; executed by the operator via gcloud compute start"),
    (r"ditutup operator: run ulang (\S+) VERIFIED (\d+) artefak \(wrun tak lagi mendeklarasikan \.corrupt\); mesin di-STOP",
     r"closed by operator: re-run \1 VERIFIED \2 artifacts (wrun no longer declares .corrupt); machine STOPPED"),
    (r"izin pemilik; eksekusi manual di luar executor", "owner approval; executed manually outside the executor"),
]


def tr(s):
    if not isinstance(s, str) or not s:
        return s
    out = s
    for pat, rep in RULES:
        out = re.sub(pat, rep, out)
    return out


def main(dry: bool):
    n = 0
    for inc in db.incidents.list(limit=1000):
        before = inc.model_dump_json()
        inc.summary = tr(inc.summary)
        for t in inc.timeline:
            t["note"] = tr(t.get("note", ""))
        if inc.model_dump_json() != before:
            n += 1
            if not dry: db.incidents.put(inc)
    for dec in db.decisions.list(limit=1000):
        new = [tr(e) for e in dec.explain]
        res = dict(dec.result); res["error"] = tr(res.get("error", "")); res["observed"] = tr(res.get("observed", ""))
        if new != dec.explain or res != dec.result:
            dec.explain, dec.result = new, res; n += 1
            if not dry: db.decisions.put(dec)
    for ev in db.evidence.list(limit=2000):
        before = ev.model_dump_json()
        ev.summary = tr(ev.summary)
        for r in ev.payload.get("results", []) if isinstance(ev.payload, dict) else []:
            if isinstance(r, dict): r["reason"] = tr(r.get("reason", ""))
        if ev.model_dump_json() != before:
            n += 1
            if not dry: db.evidence.put(ev)
    for d in db.client().collection("audit").limit(2000).stream():
        x = d.to_dict(); det = (x.get("after") or {}).get("note") or (x.get("before") or {}).get("note")
        err = tr(x.get("error", "")); obs = tr((x.get("after") or {}).get("observed", "")) if isinstance(x.get("after"), dict) else None
        upd = {}
        if err != x.get("error", ""): upd["error"] = err
        if obs is not None and obs != x["after"].get("observed", ""): upd["after.observed"] = obs
        if isinstance((x.get("after") or {}).get("note"), str) and tr(x["after"]["note"]) != x["after"]["note"]: upd["after.note"] = tr(x["after"]["note"])
        if upd:
            n += 1
            if not dry: d.reference.update(upd)
    print(("DRY " if dry else "") + f"updated documents: {n}")


if __name__ == "__main__":
    main("--dry" in sys.argv)
