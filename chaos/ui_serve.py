"""Serve the dashboard locally on a full board of failures, so a human can click through it.

Same seeded board as the recorded tour — 23 failure modes opened by the real rule engine, all live at once — but instead
of driving a browser it just stays up until you stop it. Nothing here touches the real project: emulator, fake fleet, no
Gemini call. The diagnoses in the reasoning panels are seeded by this script, not model output.

    python -m chaos.ui_serve            # http://127.0.0.1:8099  (Ctrl-C to stop)
"""
from __future__ import annotations

import signal
import sys
import time

from chaos.ui_tour import CORE_PORT, UI_PORT, enrich, seed, serve


def main() -> int:
    print("seeding the board …", flush=True)
    info = seed()
    enrich()
    procs = serve()
    print(f"\n  dashboard   http://127.0.0.1:{UI_PORT}")
    print(f"  core API    http://127.0.0.1:{CORE_PORT}")
    print(f"  board       {len(info['rules'])} failure modes live at once")
    print("\n  From another machine, tunnel it — the data is a simulation but the controls are real:")
    print(f"    ssh -L {UI_PORT}:127.0.0.1:{UI_PORT} <this-host>")
    print("\n  Ctrl-C to stop.", flush=True)

    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("now", True))
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("now", True))
    try:
        while not stop["now"]:
            time.sleep(1)
            for p in procs:
                if p.poll() is not None:
                    print(f"a server exited with {p.returncode} — see docs/video/tour/servers.log", flush=True)
                    return 1
    finally:
        for p in procs:
            p.terminate()
        print("stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
