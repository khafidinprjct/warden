// Time zone: server sends UTC ISO in <time data-utc>; the browser renders it in the viewer's zone.
(function () {
  const fmtShort = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
  const fmtClock = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const fmtLong = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  function zone(d) {
    const m = -d.getTimezoneOffset(); if (m === 0) return "UTC";
    const s = m > 0 ? "+" : "−", a = Math.abs(m); return "GMT" + s + Math.floor(a / 60) + (a % 60 ? ":" + String(a % 60).padStart(2, "0") : "");
  }
  function rel(d) {
    let s = (Date.now() - d.getTime()) / 1000, f = s < 0; s = Math.abs(s);
    const t = s < 60 ? Math.round(s) + " s" : s < 3600 ? Math.round(s / 60) + " min" : s < 172800 ? String(Math.round(s / 360) / 10) + " h" : Math.round(s / 86400) + " d";
    return f ? "in " + t : t + " ago";
  }
  function render() {
    document.querySelectorAll("time[data-utc]").forEach(el => {
      const d = new Date(el.dataset.utc); if (isNaN(d)) return;
      const mode = el.dataset.fmt || "short";
      let out = mode === "long" ? fmtLong.format(d) + " " + zone(d) : mode === "clock" ? fmtClock.format(d) + " " + zone(d) : mode === "rel" ? rel(d) : fmtShort.format(d) + " " + zone(d);
      if (el.dataset.rel === "1" && mode !== "rel") out += " · " + rel(d);
      el.textContent = out;
    });
  }
  render(); setInterval(render, 30000);
  // Actions: POST to the UI server, which signs the request to warden-core.
  document.addEventListener("click", async e => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    e.preventDefault(); b.disabled = true;
    try {
      const r = await fetch("/act", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: b.dataset.act, key: b.dataset.key || "" }) });
      const j = await r.json(); toast(j.ok ? (j.observed || j.override || j.verdict || "Done") : (j.error || "Failed"), j.ok);
      setTimeout(() => location.reload(), 900);
    } catch (err) { toast(String(err), false); b.disabled = false; }
  });
  function toast(msg, ok) {
    const t = document.createElement("div"); t.textContent = msg;
    t.style.cssText = "position:fixed;right:20px;bottom:20px;padding:10px 14px;border-radius:6px;font-weight:500;color:#fff;background:" + (ok ? "#1f7a3d" : "#b42323") + ";z-index:99";
    document.body.appendChild(t); setTimeout(() => t.remove(), 2500);
  }
  const rf = Number(document.body.dataset.refresh || 0); if (rf > 0) setTimeout(() => location.reload(), rf * 1000);
})();
