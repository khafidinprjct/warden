# ADR-0009 · Dashboard: server-rendered templates over one design-system stylesheet
**Status:** accepted (25 Aug 2026, supersedes the NiceGUI dashboard) · **Phase:** 8

## Context
The first dashboard was built on a component framework whose defaults fought every design decision; the owner judged it "cluttered". A design system and information architecture were produced first (inbox-first Overview; incident page as narrative + decision rail; one status scale; three actors) and approved.

## Decision
FastAPI + Jinja2 templates, a single stylesheet holding the design-system tokens, ~90 lines of JavaScript (time-zone rendering, actions, refresh). Timestamps are stored in UTC and rendered in the viewer's browser zone. Fidelity to the approved mockup is measured automatically (`chaos/ui2_pixel.py`, 0.40 % differing pixels) and every page is rendered against production data before deploy.

## Consequences
+ Pixel control and a repeatable fidelity test. + No websocket state on Cloud Run. − No live updates (30-second reload). − Actions are gated only by the dashboard being unlisted (ADR-0010).

## Evidence
`warden/ui2/`, `docs/mockup-v2/`, `docs/screenshots/ui2/pixel_*.png`.
