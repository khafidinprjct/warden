"""Fit the take inside four minutes without cutting anything out of it.

The rules ask for unedited live execution, and only the first four minutes are judged. A real run does not oblige: the
job takes a minute to reach the step where it dies, verification takes another, and a human answering from a phone
takes as long as it takes. Removing those stretches would remove execution. So nothing is removed — the waiting is
time-compressed, and every compressed stretch carries a badge saying by how much and what is being waited for. Frame
order, frame content and the sequence of events are exactly as recorded.

    python -m chaos.film_cut demo --target 236
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/video"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def probe(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "").replace(",", "")


def plan(dur: float, ramps: list[dict], target: float) -> tuple[list[dict], float]:
    """Choose one factor per waiting stretch so the whole take lands under target, and say so if it cannot."""
    ramps = sorted((r for r in ramps if r["end"] - r["start"] > 6.0), key=lambda r: r["start"])
    slow = sum(r["end"] - r["start"] for r in ramps)
    fixed = dur - slow
    if fixed >= target:
        raise SystemExit(f"cannot fit: {fixed:.0f}s of the take is not waiting, target is {target:.0f}s. "
                         f"Shorten the scene, do not compress what is not a wait.")
    for r in ramps:
        r["factor"] = max(1.0, (r["end"] - r["start"]) / 8.0)      # every wait reads as about eight seconds
    got = sum((r["end"] - r["start"]) / r["factor"] for r in ramps)
    room = target - fixed
    if got > room:
        k = got / room
        for r in ramps:
            r["factor"] *= k
    return ramps, fixed + sum((r["end"] - r["start"]) / r["factor"] for r in ramps)


def build(name: str, target: float) -> None:
    raw, marks_path = OUT / f"{name}-raw.mp4", OUT / f"{name}-marks.json"
    meta = json.loads(marks_path.read_text())
    dur = probe(raw)
    ramps, total = plan(dur, meta.get("ramps", []), target)

    segs, filt, cuts = [], [], []
    t = 0.0
    for r in ramps:
        if r["start"] > t + 0.05:
            cuts.append({"a": t, "b": r["start"], "f": 1.0, "label": ""})
        cuts.append({"a": r["start"], "b": r["end"], "f": r["factor"], "label": r.get("label", "waiting")})
        t = r["end"]
    if dur > t + 0.05:
        cuts.append({"a": t, "b": dur, "f": 1.0, "label": ""})

    for i, c in enumerate(cuts):
        f = c["f"]
        chain = f"[0:v]trim=start={c['a']:.3f}:end={c['b']:.3f},setpts=(PTS-STARTPTS)/{f:.4f}"
        if f > 1.2:
            txt = esc(f"x{f:.0f} speed - {c['label']}")
            chain += (f",drawtext=fontfile={FONT}:text='{txt}':fontcolor=white:fontsize=26:box=1:"
                      f"boxcolor=0x111827@0.88:boxborderw=12:x=w-tw-34:y=34")
        filt.append(chain + f"[v{i}]")
        segs.append(f"[v{i}]")
    filt.append("".join(segs) + f"concat=n={len(cuts)}:v=1:a=0[out]")

    final = OUT / f"warden-demo.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-filter_complex", ";".join(filt),
                    "-map", "[out]", "-c:v", "libx264", "-preset", "slow", "-crf", "21", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", "-r", "24", str(final)], check=True)

    # captions have to move with the timeline they describe
    def remap(x: float) -> float:
        out, prev = 0.0, 0.0
        for c in cuts:
            if x <= c["a"]:
                break
            out += (min(x, c["b"]) - c["a"]) / c["f"]
            prev = c["b"]
        return out + max(0.0, x - prev) if x > prev else out

    def ts(s: float) -> str:
        h, r = divmod(s, 3600); m, sec = divmod(r, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(sec):02d},{int(round((sec % 1) * 1000)):03d}"

    lines, marks = [], meta["marks"]
    for i, (start, text) in enumerate(marks, 1):
        if not text:
            continue
        stop = marks[i][0] if i < len(marks) else dur
        a, b = remap(start), remap(stop)
        if b - a < 0.4:
            continue
        lines.append(f"{len(lines) + 1}\n{ts(a)} --> {ts(b)}\n{text}\n")
    (OUT / "warden-demo.srt").write_text("\n".join(lines))

    real = probe(final)
    print(f"raw {dur:.1f}s → {real:.1f}s (planned {total:.1f}s), {len(ramps)} waits compressed")
    for r in ramps:
        print(f"  x{r['factor']:.0f}  {r['end'] - r['start']:6.1f}s → {(r['end'] - r['start']) / r['factor']:4.1f}s  {r.get('label', '')}")
    print(f"video → {final}\ncaptions → {OUT / 'warden-demo.srt'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="demo")
    ap.add_argument("--target", type=float, default=236.0)
    ns = ap.parse_args()
    build(ns.name, ns.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
