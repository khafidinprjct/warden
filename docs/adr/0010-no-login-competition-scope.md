# ADR-0010 · No login in front of the dashboard (competition scope)
**Status:** accepted with known risk (25 Aug 2026) · **Phase:** 12

## Context
Cloud Run IAP requires an OAuth consent screen; Google's brand-creation API is discontinued and this project has no organisation, so it can only be configured in the Console. Judges would also need per-e-mail allow-listing to open an IAP-protected URL.

## Decision
The dashboard stays unauthenticated for the judged, time-boxed deployment; the URL is shared only with judges. All actions remain audited with the acting channel. An operator-key gate (view for everyone, act only with a key) was prototyped and dropped to keep the surface simple.

## Consequences
− Anyone holding the URL can approve, deny or freeze. + The fleet is empty outside demo windows, and every action is reversible (STOP, not DELETE) and logged. Revisit before any deployment that outlives the competition.

## Evidence
`docs/SECURITY-REVIEW.md`; journal 25 Aug 22:05.
