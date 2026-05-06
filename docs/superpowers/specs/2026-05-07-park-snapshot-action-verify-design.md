# Park-Snapshot Action Verification — Design

## Goal

Replace the per-click slot-center stddev verify (cycling-prone, false-positive-prone, fragile under cursor-sprite + animated-bg pollution) with a **before/after park-state pixel-snapshot** of the entire layout. All measurements are taken when the cursor is at park (outside the GUI), so cursor-sprite pixels never pollute slot patches.

## Invariants

1. Cursor is parked between every primitive click. Every primitive click is sandwiched: park → servo → click → park.
2. The post-park snapshot of action N is reused as the pre-park snapshot of action N+1 — **net cost: 1 extra park trip + 1 snapshot per click**.
3. Identity tracking is updated only on **confirmed** or **drifted** outcomes.

## Snapshot

At park, capture for the active GUI layout:
- For each slot in `layoutForProbe.slots`: `samplePatchPixels(obs, cx, cy, 14)` → raw RGBA + the empty-BG mask.
- Cursor-area patch: existing `parkEmptyCursorPatch` baseline + a live patch via `samplePatchPixels(obs, cursor.x + 8, cursor.y + 8, 14)`.

Store as `LayoutSnapshot { slots: Map<rasterIndex, PatchPixels>, cursor: PatchPixels, takenAt: step }`. Keep the most recent on `plan` as `plan.lastParkSnapshot`.

## Diff metric

Pixel-level only. Mean+stddev fingerprints are unreliable for items with similar texture (cobblestone vs nether quartz).

- **Slot change**: per-pixel RMS distance between the old and new RGBA at the same slot center. Threshold: `rms > 25` → changed.
- **Cursor change**: same RMS over the cursor-area patch. Threshold: `rms > 25` → changed.
- **Identity match** (only when a slot changed and we need to name the new content): `patchSimilarity` (with empty-slot BG mask) against every `e.patch` in `slotMemory`. Confidence threshold: `sim > 0.85`. Below threshold → `unknown`.

## Identity propagation hierarchy

When a slot changed from empty/unknown → filled, decide its new item by, in order:
1. **Logical**: cursor was holding X (per `plan.cursorItemSignature.item`) and is now empty/decremented → new slot item is X. Authoritative.
2. **Pixel match**: `patchSimilarity` against known items in `slotMemory` ≥ 0.85 → that item.
3. **Fallback**: mark `unknown`. Planner can dispatch verify_slots to OCR.

When a slot changed from filled → empty: drop the entry from `slotMemory`. The held identity moves onto the cursor (if pickup intent and cursor was empty pre-action).

## State machine per primitive click

Inputs: action intent (`pickup`/`place_one`/`place_all`, target slot N), pre-snapshot S₀, post-snapshot S₁.

Compute: `slotChanges = { rasterIdx → "filled→empty" | "empty→filled" | "swapped" }`, `cursorChange = "empty→holding" | "holding→empty" | "unchanged"`.

Outcomes:
- **confirmed**: target slot N has the expected change AND cursor delta matches the action's expected cursor transition.
  - `pickup`: N: filled→empty, cursor: empty→holding.
  - `place_all`: N: empty→filled, cursor: holding→empty.
  - `place_one`: N: empty→filled, cursor: unchanged (still holding) OR cursor: holding→empty (placed last item).
  - Action: update `slotMemory[N]` and `cursorItemSignature` per propagation hierarchy. Record success in history.
- **drifted**: target N unchanged, but a *different* slot M shows the expected change pattern.
  - Action: update `slotMemory[M]` instead of N. Log `DRIFTED N→M`. Do NOT retry; the click happened.
- **no-op**: no slot changed AND cursor unchanged.
  - Action: retry the same primitive (re-park, re-servo, re-click). Up to 3 retries. After 3 → abort, re-prompt FastUI Action with `previous click had no observable effect`.
- **anomaly**: cursor changed without any slot change, OR 2+ unrelated slots changed, OR slot changed but cursor delta is wrong direction.
  - Action: log warning, accept observed changes (update `slotMemory` for what we see, leave `cursorItemSignature` to next park's cursor-area diff), advance. Planner will reconcile.

## Removed

- Per-click slot-center stddev verify gate (`pickupConfirmed`, `placeConfirmed`, `matched`, `MAX_RETRIES` retry-on-stddev-mismatch).
- The `verify` and `moveAway` phases as currently implemented (their work merges into the new `parkAndDiff` phase).

## New phases

Click pipeline becomes: `servo → fired → parkAndDiff`. The `parkAndDiff` phase:
1. Servo cursor to park (existing logic — same code path as held-icon detection park).
2. When cursor stable at park (2 consecutive frames at same x,y): take post-snapshot S₁.
3. Run diff vs S₀, classify outcome, update context.
4. If retry needed: re-set click to phase `servo`, decrement retry budget.
5. Else: clear `pendingClick`, return to FastUI Action loop.

## Files to change

- `src/agentbeats/tools/UiFastControl.ts`: add `LayoutSnapshot` type, add `lastParkSnapshot` field on `plan`. New phase value `"parkAndDiff"` on `PendingClick.phase`.
- `src/agentbeats/tools/SlotDetector.ts`: add `patchPixelRms(a, b)` helper for raw-RGBA RMS distance. (`samplePatchPixels` and `patchSimilarity` already exist.)
- `src/agentbeats/agents/runClosedLoopStep.ts`: replace verify/moveAway phases with parkAndDiff. Remove old stddev-based gate. Snapshot capture happens at every park-arrival (initial setup, post-action, post-OCR).
- New file `src/agentbeats/tools/SnapshotDiff.ts`: `takeLayoutSnapshot(obs, layout)`, `diffSnapshots(s0, s1)`, `classifyOutcome(intent, diff)`. Keeps the closed-loop step file focused on flow.

## Out of scope

- World-action subagents (mining/exploring/combat/placing): unaffected.
- GoalPlanner reflection: unaffected, except it now sees richer subgoal-completion reports because FastUI tracks state more reliably.
- `verify_slots` OCR: still the source-of-truth for *naming* items the runtime can't pixel-match. Snapshot diff handles *change detection*; OCR handles *first-time identification*.
