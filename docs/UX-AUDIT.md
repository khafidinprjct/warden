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
| `python -m chaos.ui_lint` | Per-element measurement of clipping, viewport overflow, type size, WCAG AA contrast, touch targets, overlaps and unnamed controls, on 16 surfaces × 2 viewports. |

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

## Second pass — measured, then compared (30 Aug)

`chaos/ui_lint.py` walks all 16 surfaces at both viewports and measures every element: text clipped by its own box,
anything crossing the viewport edge, type below 11 px, WCAG AA contrast against the *resolved* background, phone touch
targets, overlapping controls, controls with no accessible name. **278 findings on the first run; 0 now.** The two rules
that were wrong were fixed rather than worked around — an input wrapped in a `<label>` has a name, and a link inline in a
sentence is exempt from the target-size rule (WCAG 2.5.8) — and a closed `<details>` still reports boxes in Chromium, so
its hidden children are skipped.

What the instrument caught that reading the screenshots did not:

- `--muted` (#6b7686) passed over a white card at 4.60 and **failed over the page canvas at 4.30** — 161 pieces of text
  below AA. Now #626b79 (5.03 / 5.39). The navigation badge was white on amber at 3.64; `--warn-line` is now #9c6410.
- chart axis labels and the budget x-axis were drawn at 10 px.
- job links were 19 px tall inside 44 px rows, so most of the row was dead to a thumb. A stretched `::after` makes the
  row itself the target without changing the layout.
- the "why" field on the propose form had a placeholder but no accessible name.

Compared against mature incident consoles (incident.io, PagerDuty/Rootly) and the human-in-the-loop literature:

- **System Health** printed `memory` as a bare storage key and dumped a Python dict at the operator
  (`{'approval': 0, 'findings': 20, ...}`). Components are named; tick counters are label-value pairs.
- **The approvals queue offered only Approve and Review.** A queue whose one-click action is always "yes" is a rubber
  stamp, which the HITL literature calls worse than no gate at all. **Deny** is now equally reachable, and every row
  carries its cost impact beside blast radius and autonomy.
- **The fleet rendered 23 machines as 23 tall cards** — 7,534 px of page. It is now a dense table (2,312 px), with the
  secondary columns dropped on a phone.
- **Neither list could be filtered.** `/incidents` has search, severity, status and job filters; `/fleet` has search and
  status. Both state how many of the total match and how to clear.
- **Repeats were not folded.** Three or more incidents of one rule now collapse into a single expandable line, the way an
  alert console folds a flapping signature.
- **A row said something was wrong but not what Warden intended to do.** Rows now carry "Warden proposes: …".
- **A proposed action with no way to take it** — the incident summary named the next step and offered no control. It now
  offers *Request this action*, through the same policy and approval path as Warden's own proposals (never for `notify`,
  where there is nothing to take).

## Open — ranked, not fixed

1. **`Freeze` sits in the reading path on a phone.** A global, irreversible-feeling control lands between the incident
   metadata and the tabs on every page. On desktop it is safely top-right.
2. **The heartbeat chart carries no unit and little signal.** "Training heartbeat · last 60" plots a step counter, which
   only ever rises; the y-axis is bare numbers with no label. Loss or step-rate would say something a reader can act on.
3. **Status is stated twice, in conflict.** A job row can read `Running · F3` next to `No heartbeat` in red — the pill
   reports the machine, the red text reports the harness. One status line should reconcile them.
4. **Tables are `div` grids with no table semantics.** No `role="table"`/`row`/`cell` anywhere; a screen reader gets a
   pile of text. The two charts do carry `role="img"` with a real `aria-label`.
5. **Light theme only.** Deliberate for the competition scope, but an on-call console is read at night.

## Checked and sound

Timestamps carry both absolute and relative form in the viewer's own zone. Severity is encoded by dot *and* word, not
colour alone. The incident page states evidence, then diagnosis, then the decision rail, and names the cost of doing
nothing ("If left as is — $0.80 per day") — the strongest screen in the product. Cross-check results and "How to
disprove" are shown to the operator rather than kept in a log. Action vocabulary is consistent between the button, the
audit entry and the timeline. There are no transitions or animations at all, so there is nothing for
`prefers-reduced-motion` to suppress. No JavaScript errors were raised on any page in either viewport.
