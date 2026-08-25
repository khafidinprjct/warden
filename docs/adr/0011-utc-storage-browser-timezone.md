# ADR-0011 · Store UTC, render in the viewer's time zone
**Status:** accepted · **Phase:** 8

## Context
The first dashboard hard-coded the owner's zone (WIB). A product for an international audience must not.

## Decision
Every timestamp is stored and logged in UTC. The dashboard emits `<time data-utc>` elements that the browser formats in its own zone with an explicit offset label (`GMT+7`, `UTC`). A fixed zone is a future user setting, not a server default.

## Evidence
`warden/ui2/static/app.js`; verified with three simulated zones (Jakarta, Los Angeles, UTC).
