# Dashboard UX audit — 28 Aug 2026

Checklist item K1. Audited against the `frontend-design` plugin's criteria (Claude Code, `claude-plugins-official`),
applied to what this product actually is: an operations console read under pressure, not a marketing page. Where the
plugin asks for a distinctive visual identity, the binding constraint here is the opposite — standard SRE vocabulary and
a layout an on-call engineer can read without learning it. The audit therefore judges legibility, information rank,
action vocabulary, and whether the interface tells the truth about the system.

## How it was audited

| Instrument | What it establishes |
|---|---|
| `python -m chaos.ui_tour` | A recorded walkthrough with **23 failure modes live at once**, opened by the real rule engine. Every control is clicked and the effect checked in Firestore, not in the toast. Videos: `docs/video/tour/warden-{desktop,phone}.mp4`; per-control results: `tour_report.json`. |
| `python -m chaos.ui_overflow` | Element-level measurement of what exceeds a 390 px viewport, on 16 surfaces including all four incident tabs. Names the offending element rather than guessing at CSS. |
| `python -m chaos.ui_shots` | Full-page screenshots of 13 pages at 1440×900 and 390×844, in `docs/screenshots/ui2/`. |

Nothing here runs against the real project: the emulator and the fake fleet are used throughout, and the diagnoses shown
in the reasoning panels are seeded by the tour, not model output.

## Fixed in this pass

1. **Five surfaces scrolled sideways on a phone** (`/budget`, `/policies`, `/audit`, `/ask`, and the incident timeline);
   `/approvals` and `/jobs/<id>` did too and had never been measured. Causes were layout rules, not content: `.main` and
   its children kept `min-width: auto`, so a wide table widened the page instead of scrolling inside itself; `.page-head`
   could not wrap; and **seven column definitions lived in inline styles, which no media query can override**. All 16
   surfaces now report `scrollWidth == innerWidth` at 390 px.

2. **Sideways scrolling hid the columns that mattered.** The audit log on a phone showed Time, Actor and Phase while
   Action, Target and Result sat off-screen; the policy table clipped the autonomy level mid-word. Dense tables now
   stack into labelled lines. The audit log — densest and longest — puts action, target and result on a line each and
   shares one line between when, who and phase: 8,716 px of phone page down to 6,008 px with nothing hidden.

3. **Policy screens printed configuration identifiers at the operator**: "Auto spend daily cap usd 10.0", "Approval ttl
   minutes 30", "per hour 3, per day 8, max cost usd 2.0". Labels now say what the limit means and the unit travels with
   the value: "Automatic spend, per day $10.00", "Approval expires after 30 min", "3/hour · 8/day · max $2.00".

4. **The overview listed all 23 jobs alphabetically**, burying the incidents. It now shows the ones a human would look
   at first (no heartbeat, then stale) with a link to the rest.
   *Incident order was briefly changed to severity-first and then reverted on the owner's instruction (30 Aug): incidents
   read newest first on every page, so the order never changes between the overview and the list. Severity stays visible
   as a dot and a word on each row.*

5. **Phone legibility.** Property labels sat in a 110 px column that wrapped log excerpts mid-token
   (`torch.cuda.OutOfMe` / `moryError`); they now sit above their values. Chart axis labels were drawn at 10 units inside
   a 640-unit viewBox scaled to ~330 px — about 5 px on screen. A decision waiting on the operator now comes first on the
   incident page instead of below the chart.

6. **Keyboard focus had no styling anywhere in the stylesheet.** `:focus-visible` now draws an accent outline.

7. **Copy.** The empty approvals state read "Warden lists decisions here when a policy requires you" — not a sentence.
   It is now "Nothing waiting on you. Warden asks here when a policy requires your approval," and the empty card no
   longer holds the top of the page open.

## Open — ranked, not fixed

1. **A proposed action with no way to take it.** The incident summary states "Proposed action — Resume with smaller
   batch", but when no decision exists the page offers no control; the operator has to find the job page and re-request
   the same action by hand. The page names the next step and then refuses to take it.
2. **`Freeze` sits in the reading path on a phone.** A global, irreversible-feeling control lands between the incident
   metadata and the tabs on every page. On desktop it is safely top-right.
3. **The heartbeat chart carries no unit and little signal.** "Training heartbeat · last 60" plots a step counter, which
   only ever rises; the y-axis is bare numbers with no label. Loss or step-rate would say something a reader can act on.
4. **Status is stated twice, in conflict.** A job row can read `Running · F3` next to `No heartbeat` in red — the pill
   reports the machine, the red text reports the harness. One status line should reconcile them.
5. **Tables are `div` grids with no table semantics.** No `role="table"`/`row`/`cell` anywhere; a screen reader gets a
   pile of text. The two charts do carry `role="img"` with a real `aria-label`.
6. **Light theme only.** Deliberate for the competition scope, but an on-call console is read at night.

## Checked and sound

Timestamps carry both absolute and relative form in the viewer's own zone. Severity is encoded by dot *and* word, not
colour alone. The incident page states evidence, then diagnosis, then the decision rail, and names the cost of doing
nothing ("If left as is — $0.80 per day") — the strongest screen in the product. Cross-check results and "How to
disprove" are shown to the operator rather than kept in a log. Action vocabulary is consistent between the button, the
audit entry and the timeline. There are no transitions or animations at all, so there is nothing for
`prefers-reduced-motion` to suppress. No JavaScript errors were raised on any page in either viewport.
