"""Rekam tur dashboard headless (cadangan Fase 11) + SRT Inggris. Stopwatch dari awal rekaman; waktu caption = saat halaman termuat."""
import time, shutil, subprocess
from playwright.sync_api import sync_playwright
BASE="https://warden-ui-hfgre6y7ta-uc.a.run.app"
SCENES=[("/",8,"Warden — an SRE agent for long-running compute jobs on Google Cloud.\nRUNNING is not the same as training correctly; DONE is not the same as intact."),
("/incidents",8,"Every incident carries evidence, cost and an audit trail — no LLM ever holds the button."),
("/incidents/inc_01M0VWDHXXJ5WFPH9Y05QERCYP",14,"Scene 1 — a real Spot preemption today: TERMINATED without RUN_FIN\n→ policy level L2 → START → resumed from the last phase. Loss: 348 seconds."),
("/incidents/inc_01M0VY4CQ7SMHKECX0JM47K90X",12,"Scene 2 — finished is not intact: the verifier OPENS pred.csv\n(1030 rows, sha256 match) before the job becomes COMPLETE."),
("/incidents/inc_01M0VY816WG3ECGT0SG245N4D6",10,"Scene 3 — an idle machine after its job completed: orphan detected,\nSTOP proposed with the projected cost, a human is asked."),
("/budget",10,"Ledger, projection, ETTR (effective training time ratio) and the budget kill-switch."),
("/policies",8,"Graduated autonomy per action type.\nDelete does not exist as an action — not even with approval."),
("/audit",8,"Intent before, result after: requested-vs-actual for every action."),
("/health",8,"Success leaves a trace: Warden's own heartbeat is watched by an external dead-man service.")]
f=lambda x: time.strftime("%H:%M:%S",time.gmtime(x))+",%03d"%int((x%1)*1000)
srt=[]; marks=[]
with sync_playwright() as p:
    b=p.chromium.launch(); ctx=b.new_context(viewport={"width":1280,"height":720},record_video_dir="docs/video/raw",record_video_size={"width":1280,"height":720})
    t0=time.time(); pg=ctx.new_page()
    for i,(path,dur,cap) in enumerate(SCENES,1):
        pg.goto(BASE+path,wait_until="load",timeout=120000)
        try: pg.wait_for_selector("text=Connection lost",state="detached",timeout=5000)
        except Exception: pass
        time.sleep(1.5); s=time.time()-t0; time.sleep(dur); e=time.time()-t0
        srt.append(f"{i}\n{f(s)} --> {f(e)}\n{cap}\n"); marks.append(round((s+e)/2,1)); print(f"adegan {i} {path}: {s:.1f}-{e:.1f}s", flush=True)
    v=pg.video.path(); ctx.close(); b.close()
shutil.move(v,"docs/video/tour.webm"); open("docs/video/tour.en.srt","w").write("\n".join(srt)); open("docs/video/marks.txt","w").write(" ".join(map(str,marks)))
subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-i","docs/video/tour.webm","-i","docs/video/tour.en.srt","-map","0:v","-map","1","-c:v","libx264","-preset","fast","-crf","23","-pix_fmt","yuv420p","-c:s","mov_text","-metadata:s:s:0","language=eng","docs/video/tour.mp4"],check=True)
subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-i","docs/video/tour.webm","-vf","subtitles=docs/video/tour.en.srt:force_style='FontSize=16,Outline=1,MarginV=24'","-c:v","libx264","-preset","fast","-crf","23","-pix_fmt","yuv420p","docs/video/tour_cc.mp4"],check=True)
print("SELESAI-REKAM", flush=True)
