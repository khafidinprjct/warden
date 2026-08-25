#!/usr/bin/env python3
"""toy_train — job training kecil (numpy) yang MEMENUHI kontrak harness penuh:
  • warden.beat() tiap 50 step (phase/step/loss/grad_norm/step_per_s)
  • checkpoint .npz atomik tiap 200 step + sidecar meta; resume dari checkpoint terakhir (sadar fase)
  • SIGUSR1 (tanda preempt dari warden-agent) → checkpoint darurat ≤ 1 dtk
  • fase: train → eval (menulis eval.jsonl) → export (pred.csv); evidence.json untuk RUN_FIN
Dipakai sebagai job kedua (Fase 14) dan adegan preempt demo: kerugian ≤ 200 step."""
from __future__ import annotations

import argparse, glob, json, os, signal, sys, time
import numpy as np

sys.path.insert(0, "/opt/warden")
try:
    from warden_beat import beat
except ImportError:
    def beat(**kw): pass

ap = argparse.ArgumentParser(); ap.add_argument("--steps", type=int, default=3000); ap.add_argument("--out", default=os.environ.get("WARDEN_DIR", "/var/lib/warden") + "/" + os.environ.get("WARDEN_JOB", "toy") + "/artifacts")
ap.add_argument("--sleep", type=float, default=0.05); ap.add_argument("--nan-at", type=int, default=0); a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
rng = np.random.default_rng(0); X = rng.normal(size=(2000, 20)); w_true = rng.normal(size=20); y = (X @ w_true + rng.normal(scale=0.5, size=2000) > 0).astype(float)
w = np.zeros(20); step = 0; lr = 0.1
ck = sorted(glob.glob(os.path.join(a.out, "ckpt_*.npz")))
# Resume dari checkpoint UTUH terakhir, bukan yang terbaru (katalog #7/#8): preempt nyata 25 Agu memotong
# ckpt_001700.npz → np.load EOFError → run gagal. Yang rusak dikarantina (.corrupt), lalu mundur satu.
resumed = False
for c in reversed(ck):
    try:
        z = np.load(c); w, step = z["w"], int(z["step"]); print(f"=== [resume] dari {os.path.basename(c)} step {step} ===", flush=True); resumed = True; break
    except Exception as e:  # noqa: BLE001 — checkpoint rusak/terpotong
        os.replace(c, c + ".corrupt"); print(f"=== [resume] {os.path.basename(c)} RUSAK ({type(e).__name__}) → dikarantina, mundur ===", flush=True)
if not resumed:
    print("=== [train] mulai dari nol ===", flush=True)


def save(tag: str = ""):
    p = os.path.join(a.out, f"ckpt_{step:06d}.npz"); tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        np.savez(f, w=w, step=step, loss=float(last_loss))
    os.replace(tmp, p)
    json.dump({"step": step, "loss": float(last_loss), "expect_size": os.path.getsize(p)}, open(p + ".meta.json", "w"))
    print(f"checkpoint {os.path.basename(p)} {tag}", flush=True)


last_loss = 1.0
# Handler sinyal hanya MENANDAI; simpan dilakukan di loop utama (anti re-entrancy: dua SIGUSR1 beruntun
# saat save() sedang menulis bisa merusak file — dugaan kuat penyebab ckpt_001700 terpotong).
preempt = {"flag": False, "saved": False}
signal.signal(signal.SIGUSR1, lambda *_: preempt.__setitem__("flag", True))
t0 = time.time()
while step < a.steps:
    if preempt["flag"] and not preempt["saved"]:
        save("(darurat: tanda preempt)"); preempt["saved"] = True
    i = rng.integers(0, 2000, 64); xb, yb = X[i], y[i]
    p = 1 / (1 + np.exp(-xb @ w)); g = xb.T @ (p - yb) / 64; w -= lr * g
    last_loss = float(-np.mean(yb * np.log(p + 1e-9) + (1 - yb) * np.log(1 - p + 1e-9)))
    if a.nan_at and step == a.nan_at:
        w[:] = np.nan; last_loss = float("nan")
    step += 1
    if step % 50 == 0:
        beat(phase="train", step=step, loss=last_loss, lr=lr, grad_norm=float(np.linalg.norm(g)))
        print(f"step {step} loss {last_loss:.4f} grad_norm {np.linalg.norm(g):.4f}", flush=True)
    if step % 200 == 0:
        save()
    time.sleep(a.sleep)
print("=== [eval] ===", flush=True); beat(phase="eval", step=step, loss=last_loss)
with open(os.path.join(a.out, "eval.jsonl"), "w") as f:
    for k in range(10):
        j = rng.integers(0, 2000, 200); pk = 1 / (1 + np.exp(-X[j] @ w)); acc = float(np.mean((pk > 0.5) == y[j]))
        f.write(json.dumps({"fold": k, "acc": acc}) + "\n"); time.sleep(0.2)
print("=== [export] ===", flush=True); beat(phase="export", step=step, loss=last_loss)
pk = 1 / (1 + np.exp(-X @ w))
with open(os.path.join(a.out, "pred.csv"), "w") as f:
    f.write("ID,TargetF1,TargetRAUC\n"); [f.write(f"{k},{int(pk[k] > 0.5)},{pk[k]:.6f}\n") for k in range(2000)]
json.dump({"rows": {"pred.csv": 2000, "eval.jsonl": 10}, "metrics": {"final_loss": last_loss, "acc": acc}}, open(os.path.join(os.path.dirname(a.out), "evidence.json"), "w"))
print(f"SELESAI step {step} loss {last_loss:.4f} acc {acc:.3f} dalam {time.time()-t0:.0f}s", flush=True)
