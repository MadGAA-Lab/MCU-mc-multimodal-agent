# Hotbar-Verify for the Placing Subagent

Date: 2026-05-07
Branch focus: `refactor/craft-table-prereq` (current)
Status: design approved, awaiting spec review.

## Problem

The Placing subagent picks a hotbar slot from icon recognition alone ("brown wooden block with grid texture = crafting_table"). Icon-vibes are unreliable at 18×18 native resolution — similar items (cobblestone vs andesite, oak_planks vs birch_planks) get confused, and the agent ends up holding the wrong block when it emits `use=1`. Placing then "succeeds" mechanically but deposits the wrong item into the world, which cascades into failed crafting prerequisites (cake, furnace, etc.).

## Goal

Add a deterministic verify step to Placing so that, before aiming and using, the agent confirms via the hotbar held-item-name banner that it actually selected the requested block. Inspired by FastUI's split: LLM owns intent, runtime owns precise verification.

## Non-goals

- Reusing the verifier for Mining or Combat (YAGNI; only Placing has reported the failure mode). Code is organized so it can be lifted later.
- Cross-dispatch caching of "slot N held item X" (player or eval framework can mutate inventory between dispatches; cache would go stale).
- Optimizing the verify-frame budget below ~3 s worst case (correctness first; throughput is fine).

## High-level shape

The Placing subagent grows a per-subgoal phase machine:

```
phase: "equip"      ← runtime-owned, hotbar swaps + OCR only, no LLM call
       ↓ (verified)
phase: "post_equip" ← LLM-owned (callWorldVlm), full action vocab: camera, forward/back, jump, use, verify
```

`HotbarVerifier` is a runtime helper in `src/agentbeats/tools/` that returns one MCU action per `Placing.step()` call during the equip phase. When verification succeeds, control transfers to the existing `callWorldVlm` path with a prompt that knows the equip is complete. On failure, Placing returns `subgoal_failed` with structured fields the GoalPlanner can react to.

Movement (camera tilt, walking to clear obstructions, jumping, looking for a clean ground tile) lives entirely in `post_equip` and is LLM-driven. The verifier never moves the camera or the player.

## `HotbarVerifier` state machine

State (per Placing dispatch):

```
{
  target: string,                   // e.g. "crafting_table"
  candidateOrder: number[],         // 1..9, all slots, in walk order
  cursor: number,                   // index into candidateOrder
  innerPhase: "init" | "swap_away" | "swap_to" | "settle" | "read",
  settleCounter: number,
  ocrTrace: Array<{ slot: number; observed: string }>,
  activeSlot: number | null,        // last hotbar.N we emitted
}
```

Per-step transitions (one MCU action per call):

```
init:
  candidateOrder = [1, 2, ..., 9]   // fixed order; active-first optimization is a follow-up
  cursor = 0
  innerPhase = "swap_away"          → emit hotbar.<(candidate % 9) + 1>
                                       (forces banner to render even if first candidate
                                        happens to already be active at entry)

swap_away:
  innerPhase = "swap_to"            → emit hotbar.<candidateOrder[cursor]>
  activeSlot = candidateOrder[cursor]

swap_to:
  innerPhase = "settle"             → emit noop, settleCounter = 0

settle:
  if settleCounter < SETTLE_FRAMES (default 1):
    settleCounter++                  → emit noop
  else:
    innerPhase = "read"              → emit noop; OCR runs on next obs the runtime sees

read (consumes the obs frame the verifier just received):
  result = hotbarBannerMatch(frame, target)
  push { slot: candidateOrder[cursor], observed: result.observed } to ocrTrace
  if result.match:
    return DONE(equippedSlot = candidateOrder[cursor])
  else:
    cursor++
    if cursor >= candidateOrder.length:
      return FAIL(code: "hotbar_missing_item",
                  reportFields: { item: target, ocrTrace })
    else:
      innerPhase = "swap_to"         → emit hotbar.<candidateOrder[cursor]>
      activeSlot = candidateOrder[cursor]
      // No "swap_away" needed for cursor>0 — active just moved to the previous candidate,
      // so swapping to a different slot guarantees a banner re-render.
```

### Frame budget

- Per candidate after the first: 1 swap-to + 1 settle + 1 read = 3 frames.
- First candidate adds 1 swap-away = 4 frames.
- Best case (target on slot 1): 4 frames (~0.4 s at 10 fps).
- Worst case (target absent or on slot 9): 4 + 8 × 3 = 28 frames (~2.8 s).
- Average case (uniform distribution): ~14 frames (~1.4 s).

### `SETTLE_FRAMES = 1`

The banner appears on the frame immediately after a hotbar switch and persists ~40 ticks. One settle frame is conservative — can be tuned to 0 once the loop is observed working in practice.

## OCR contract: `hotbarBannerMatch`

New file `src/agentbeats/tools/HotbarOcr.ts`. Mirrors `SlotOcr.ts` structure (own crop helper, own debug-event emission, R/B-swap + 3× zoom), but target-aware and binary.

```ts
export type HotbarBannerMatchOpts = {
  client: OpenAI;
  model: string;
  obsBase64: string;
  target: string;             // snake_case, e.g. "crafting_table"
};

export type HotbarBannerMatchResult = {
  match: boolean;
  observed: string;           // raw text the model claims to have read; "" if banner empty/unreadable
};

export async function hotbarBannerMatch(opts: HotbarBannerMatchOpts): Promise<HotbarBannerMatchResult>;
```

Prompt (concise):

> You are shown a cropped strip just above the Minecraft hotbar. When the player switches hotbar slots, MC renders the held item's display name as a translucent dark banner with white text in this region for ~2 seconds.
>
> The target item is `<target>`.
>
> Return JSON: `{"match": true|false, "observed": "<text you read, or empty string>"}`.
>
> Rules:
> - `match=true` ONLY if the visible banner clearly displays a name that snake_case-normalizes to `<target>`. ("Crafting Table" → `crafting_table`, "Oak Planks" → `oak_planks`.)
> - If you see a banner but it's a different item: `match=false`, `observed="<that name>"`.
> - If no banner is visible (faded/empty slot): `match=false`, `observed=""`.
> - NEVER guess from icon. Read banner text only.

### Crop region

`cropHotbarBannerRegion(obsBase64)` returns a centered ~280×32 px crop anchored above the hotbar centerline, R/B-swapped, 3× zoomed. Hotbar centerline is at the standard MC HUD location for the configured 640×360 frame size.

### Debug emission

On every call, append a `hotbar_ocr` event to `events.jsonl` with the cropped image saved as a PNG, mirroring how `SlotOcr` records `slot_ocr` events. Dashboard renderer (`local_tests/debug_dashboard.mjs`) gains a case for this event type.

## Placing subagent integration

`src/agentbeats/agents/subagents/Placing.ts` grows from a thin wrapper around `callWorldVlm` to a phase-aware step function:

```ts
type PlacingState = {
  subgoalId: string;             // resets state when subgoal changes
  phase: "equip" | "post_equip";
  verifier: HotbarVerifier | null;
  equippedSlot: number | null;
};

function step(input) {
  if (state.subgoalId !== input.subgoal.id) resetState(input.subgoal);

  if (state.phase === "equip") {
    const result = state.verifier!.nextAction(input.obs);
    if (result.kind === "act") {
      return { kind: "act", action: result.action, holdSteps: 1 };
    }
    if (result.kind === "done") {
      state.phase = "post_equip";
      state.equippedSlot = result.equippedSlot;
      // Fall through to post_equip on next step; this step returns a noop
      // so the state transition is observable in the debug log.
      return { kind: "act", action: noop(), holdSteps: 1 };
    }
    if (result.kind === "fail") {
      return {
        kind: "subgoal_failed",
        reason: `hotbar_missing_item: ${state.target}`,
        reportFields: result.reportFields,
      };
    }
  }

  // post_equip: LLM-driven, with a guard against hotbar.N
  const llmStep = await callWorldVlm(deps, PLACING_SYSTEM_PROMPT, input, "placing");
  if (llmStep.kind === "act" && isHotbarSwitch(llmStep.action)) {
    return {
      kind: "subgoal_failed",
      reason: "post_equip_hotbar_switch",
      reportFields: { code: "post_equip_hotbar_switch", attemptedSlot: hotbarSlotOf(llmStep.action) },
    };
  }
  return llmStep;
}
```

Subgoal-id resolution uses whatever identifier the runtime already passes (subgoal description string is fine if no id is plumbed; the reset condition is "different subgoal text → fresh state").

### Subtarget extraction

The verifier needs `target` (the block name). Today the Placing prompt parses it from `subgoal.description` ("place crafting_table on the ground"). Add a small helper `extractPlacingTarget(subgoal)` used by both the runtime (to seed the verifier) and the prompt (no change needed if it already parses). If extraction fails, fall straight to `subgoal_failed("placing_target_unparseable")`.

## Prompt changes

### `src/agentbeats/prompts/subagents/placing.ts`

Strip step 1 (icon-based hotbar identification + `hotbar.N` emission). New procedure starts at aiming:

> You arrive in this subgoal already equipped with the requested block. The runtime has verified the active hotbar slot via OCR before handing control to you.
>
> 1. AIM. Tilt the camera DOWN: emit `camera=[0, +30]` for one step. If you see your own body / sky in the crosshair, tilt more (+10 to +20 increments). If the ground tile directly in front is occupied (a placed block, the player's feet), step BACK once (`back=1` for one step) before tilting.
>
> 2. PLACE. When the crosshair is clearly on a ground tile face, emit `use=1` (with no other buttons) for ONE step.
>
> 3. VERIFY. Look at the next frame. If the placed block is visible in front: emit `task_done=true` with a noop action. If not: retry from step 1 with adjusted camera.
>
> HARD CONSTRAINT: Do NOT emit `hotbar.N`. The runtime has already selected the correct slot. Switching slots will cause subgoal failure.

### `src/agentbeats/prompts/goal_planner.ts`

Add a "Sub-agent failure handling" section:

> When a sub-agent returns `subgoal_failed` with structured `reportFields`:
>
> - `code: "hotbar_missing_item"` (with `item`, `ocrTrace`): the requested block is not on any hotbar slot. Dispatch `ui_inventory` to move `<item>` from the main inventory into the hotbar, then re-dispatch `placing(<item>)`. If `ocrTrace` shows main inventory does not contain it either (you'll see this when the subsequent `ui_inventory` also fails), dispatch `world_explore` or `mining` to collect.
> - `code: "post_equip_hotbar_switch"`: re-dispatch `placing(<item>)` once. If it recurs, fall back to `placing(<item>)` with a different verbiage in the subgoal description (the LLM may be misinterpreting the prompt).
> - `code: "placing_target_unparseable"`: the subgoal description was malformed; re-author with format `"place <snake_case_block>"`.

Also add one few-shot example showing the recovery flow:

```
1. add_checklist_item("place crafting_table") → dispatch placing(crafting_table)
2. (placing fails: hotbar_missing_item, ocrTrace shows hotbar holds [cobblestone, dirt, ...])
3. dispatch ui_inventory("move crafting_table from main inventory to hotbar")
4. dispatch placing(crafting_table)  ← will re-run hotbar verify, should succeed
```

## SubAgentStep type extension

`src/agentbeats/agents/subagents/SubAgent.ts` (or wherever `SubAgentStep` lives):

```ts
export type SubAgentStep =
  | { kind: "act"; action: McuAction; holdSteps?: number }
  | { kind: "subgoal_done"; summary: string }
  | { kind: "subgoal_failed"; reason: string; reportFields?: Record<string, unknown> };
```

`reportFields` is optional and free-form (string-keyed). The GoalPlanner prompt is responsible for reading well-known keys (`code`, `item`, `ocrTrace`).

## Debug dashboard

`local_tests/debug_dashboard.mjs` renderer additions:

- `hotbar_ocr` event: render the saved crop image + observed/match/target line.
- Phase transitions in Placing: rendered via existing `placing_call`/`placing_response` events; the verifier emits its own `hotbar_verifier_step` event (small JSON, no image) describing `{innerPhase, cursor, candidateSlot, action}` so the loop is traceable in the dashboard timeline.

## Tests / validation

No automated tests added (consistent with project pattern). Validation is the eval-loop ritual:

1. Build :test image, restart purple, submit cake task (current branch's golden bug).
2. Confirm in dashboard: Placing dispatch for `crafting_table` shows `hotbar_verifier_step` events sweeping slots, `hotbar_ocr` events with cropped banners, eventual `match=true` for the correct slot, then `placing_call` events for aim/place/verify.
3. Confirm: a deliberately misconfigured eval (crafting_table absent from hotbar) produces `subgoal_failed` with `code: hotbar_missing_item` and the GoalPlanner dispatches `ui_inventory` next.

## Risks

- **Banner crop region wrong for non-default HUD scale.** MC HUD scales with window size; eval framework should use a fixed 640×360. If a future eval changes resolution, crop constants need rederivation. Acceptable risk — single magic constant in `HotbarOcr.ts`.
- **Banner OCR false negatives on item names with unusual rendering** (italics for renamed items, color codes). The verifier treats any `match=false` as "next candidate", so a false negative on the correct slot causes the verifier to walk past it and eventually escalate. Mitigation: the OCR prompt explicitly accepts any name that snake_case-normalizes to target. False positives (claiming match when banner says different item) are the dangerous case — mitigated by `match` being binary and the model temperature being 0.
- **Banner faded between settle and read frames** if game lags. `SETTLE_FRAMES=1` keeps the read close to the switch. If observed, bump to 0 settle (read immediately on the post-swap frame) — banner is most opaque on tick 1 after switch.
- **`subgoal.id` may not exist in the runtime today**, in which case state-reset condition keys on subgoal description string. If two consecutive Placing subgoals have identical description (e.g., place two crafting_tables back-to-back), state must still reset between them — caller's responsibility to vary the description or signal subgoal boundary explicitly.
