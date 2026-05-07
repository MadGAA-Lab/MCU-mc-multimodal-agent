# Hotbar-Verify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic hotbar OCR verify step to the Placing subagent so it confirms the held item via the in-world hotbar item-name banner before aiming and placing — eliminating the "wrong block placed" failure mode.

**Architecture:** A runtime-owned `HotbarVerifier` state machine walks all 9 hotbar slots (swap-away → swap-to → settle → read), calling a target-aware binary OCR helper `hotbarBannerMatch` to detect when the banner reads the requested item. On match the verifier transitions Placing into a post-equip phase where the existing LLM-driven `callWorldVlm` handles aim/move/place/verify. On full-sweep miss, the verifier emits a structured `subgoal_failed` carrying `code: "hotbar_missing_item"` plus an OCR trace; the GoalPlanner reads that to dispatch a fetch-from-inventory or explore-collect subgoal.

**Tech Stack:** TypeScript (Node 20), OpenAI vision models, jpeg-js + pngjs for image crops, existing `DebugRecorder` singleton for event logging, existing `callWorldVlm` per-step VLM call. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-05-07-hotbar-verify-design.md](../specs/2026-05-07-hotbar-verify-design.md) (commit `4b46479`).

**Branch:** `refactor/craft-table-prereq` (work continues on this branch — the spec is part of an in-progress placing-prereq series).

**Working directory for all paths below:** `d:/GitHub/MCU-mc-multimodal-agent/mc-multimodal-agent` unless specified otherwise.

**Validation:** No automated tests in this project. Each task ends with `npm run typecheck` to confirm the TS compiles. End-to-end correctness is validated in the final task via the eval-loop ritual: rebuild :test image → restart purple → submit cake task → inspect dashboard.

---

## File Plan

**New files:**
- `src/agentbeats/tools/HotbarOcr.ts` — crop helper + `hotbarBannerMatch` (target-aware binary OCR)
- `src/agentbeats/tools/HotbarVerifier.ts` — phase state machine

**Modified files:**
- `src/agentbeats/agents/SubAgent.ts` — extend `subgoal_failed` with `reportFields?`; extend `pendingReflection` similarly
- `src/agentbeats/agents/Dispatcher.ts` — forward `reportFields` from subagent step into `pendingReflection`
- `src/agentbeats/agents/PlannerLoop.ts` — render `reportFields` into the reflection user-message
- `src/agentbeats/agents/subagents/Placing.ts` — phase machine, verifier integration, post-equip hotbar guard
- `src/agentbeats/prompts/subagents/placing.ts` — drop equip step, add "do NOT emit hotbar.N" guard
- `src/agentbeats/prompts/goal_planner.ts` — sub-agent failure-handling section + few-shot
- `local_tests/debug_dashboard.mjs` — render `hotbar_ocr` and `hotbar_verifier_step` events

---

## Task 1: Extend `SubAgentStep` with `reportFields` and plumb through reflection

**Files:**
- Modify: `src/agentbeats/agents/SubAgent.ts`
- Modify: `src/agentbeats/agents/Dispatcher.ts`
- Modify: `src/agentbeats/agents/PlannerLoop.ts`

- [ ] **Step 1: Extend `SubAgentStep` and `EpisodeState.pendingReflection` types**

In `src/agentbeats/agents/SubAgent.ts`, replace the `SubAgentStep` union:

```ts
export type SubAgentStep =
  | { kind: "act"; action: McuEnvAction; holdSteps: number }
  | { kind: "subgoal_done"; summary: string }
  | { kind: "subgoal_failed"; reason: string; reportFields?: Record<string, unknown> };
```

Replace the `pendingReflection` field on `EpisodeState`:

```ts
pendingReflection: {
  subgoal: Subgoal;
  outcome: "done" | "failed";
  summary: string;
  reportFields?: Record<string, unknown>;
} | null;
```

(The `makeEpisodeState` initializer already sets `pendingReflection: null`; no change.)

- [ ] **Step 2: Forward `reportFields` from Dispatcher into `pendingReflection`**

In `src/agentbeats/agents/Dispatcher.ts`, change the failure branch around line 99–104 from:

```ts
  // subgoal_failed
  state.completedSummaries.push(`SUBGOAL_FAILED: ${step.reason}`);
  state.history.push(`failed: ${current.description} -> ${step.reason}`);
  state.pendingReflection = { subgoal: current, outcome: "failed", summary: step.reason };
  state.subgoals = []; state.idx = 0;
  return NOOP_ONE;
}
```

to:

```ts
  // subgoal_failed
  state.completedSummaries.push(`SUBGOAL_FAILED: ${step.reason}`);
  state.history.push(`failed: ${current.description} -> ${step.reason}`);
  state.pendingReflection = {
    subgoal: current,
    outcome: "failed",
    summary: step.reason,
    reportFields: step.reportFields,
  };
  state.subgoals = []; state.idx = 0;
  return NOOP_ONE;
}
```

- [ ] **Step 3: Render `reportFields` into the planner reflection user-message**

In `src/agentbeats/agents/PlannerLoop.ts`, the reflection block currently looks like (around line 60–77):

```ts
  if (state.pendingReflection) {
    const r = state.pendingReflection;
    if (state.plannerMessages.length === 0) {
      state.plannerMessages.push({ role: "system", content: GOAL_PLANNER_SYSTEM_PROMPT });
      state.plannerMessages.push({ role: "user", content: `Task: ${state.taskText}` });
    }
    state.plannerMessages.push({
      role: "user",
      content:
        `The sub-agent for "${r.subgoal.description}" returned: ${r.outcome.toUpperCase()}.\n` +
        `Summary: ${r.summary}\n\n` +
        `REFLECT before your next move:\n` +
        ...
    });
    state.pendingReflection = null;
  }
```

Insert a `reportFields` block between Summary and REFLECT:

```ts
    const reportLine = r.reportFields
      ? `Report fields (structured): ${JSON.stringify(r.reportFields)}\n`
      : "";
    state.plannerMessages.push({
      role: "user",
      content:
        `The sub-agent for "${r.subgoal.description}" returned: ${r.outcome.toUpperCase()}.\n` +
        `Summary: ${r.summary}\n` +
        reportLine +
        `\nREFLECT before your next move:\n` +
        `1. Call read_checklist.\n` +
        `2. If success, VERIFY the result with inspect_inventory or verify_slots BEFORE marking done.\n` +
        `3. If failure starts with "BLOCKED:" or has report fields with a "code", insert prerequisite checklist items, then dispatch the first prerequisite.\n` +
        `4. After the checklist reflects reality, either dispatch the next pending item or call task_complete (only if every item is done).`,
    });
```

- [ ] **Step 4: Typecheck**

Run: `npm run typecheck`
Expected: exits 0, no errors.

- [ ] **Step 5: Commit**

```bash
git add src/agentbeats/agents/SubAgent.ts \
        src/agentbeats/agents/Dispatcher.ts \
        src/agentbeats/agents/PlannerLoop.ts
git commit -m "$(cat <<'EOF'
feat(subagent): plumb reportFields from subgoal_failed to GoalPlanner reflection

Optional structured report payload on failure outcomes so subagents
can hand machine-readable codes (e.g. hotbar_missing_item) to the
planner alongside the human-readable reason string.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Create `HotbarOcr.ts`

**Files:**
- Create: `src/agentbeats/tools/HotbarOcr.ts`

- [ ] **Step 1: Create the file**

```ts
/**
 * Hotbar held-item-name banner OCR.
 *
 * When the player presses a hotbar slot key in Minecraft, MC renders
 * the held item's display name as a translucent dark banner with white
 * text just above the hotbar bar for ~40 ticks. The banner text is
 * unambiguous (e.g. "Crafting Table" vs "Cobblestone") and a much
 * more reliable signal than the 18×18 hotbar icon.
 *
 * This module owns the crop region and a target-aware binary OCR call:
 * given a frame and a target item, the model returns {match, observed}.
 * The HotbarVerifier loops over candidate slots calling this helper
 * until match=true or it has swept all 9 slots.
 *
 * R/B-swap + 3× zoom mirror SlotOcr.ts, since the MC sim outputs JPEGs
 * whose jpeg-js decode comes out BGR.
 */
import type OpenAI from "openai";
import * as fs from "node:fs";
import * as path from "node:path";

export type HotbarBannerMatchOpts = {
  client: OpenAI;
  model: string;
  obsBase64: string;
  /** Snake_case item id, e.g. "crafting_table". The OCR result is
   *  considered a match iff the visible banner snake_case-normalizes
   *  to this value. */
  target: string;
  /** Optional context for debug log (e.g. "hotbar.3"). */
  candidateLabel?: string;
};

export type HotbarBannerMatchResult = {
  match: boolean;
  /** Raw text the model claims to read; "" if no banner visible. */
  observed: string;
};

const SYSTEM_PROMPT = `You are a Minecraft hotbar banner OCR sub-agent.

You are shown a CROPPED REGION just above the Minecraft hotbar. When the player switches hotbar slots, MC renders the held item's display name as a translucent dark banner with white text in this region for ~2 seconds. Your job: decide whether the banner currently shows a target item.

INPUT: a target item name in snake_case (e.g. "crafting_table").

OUTPUT JSON: {"match": true|false, "observed": "<text you read, or empty string>"}.

STRICT RULES:
- match=true ONLY if a banner is clearly visible AND its text snake_case-normalizes to the target. ("Crafting Table" -> crafting_table, "Oak Planks" -> oak_planks, "Nether Quartz" -> nether_quartz, "Bone Meal" -> bone_meal.)
- If a banner is visible but it shows a DIFFERENT item: match=false, observed=<that item's name in snake_case>.
- If NO banner is visible (it has faded out, or no slot switch occurred): match=false, observed="".
- If the banner is visible but unreadable: match=false, observed="unknown".
- NEVER guess from the hotbar icons. Only read the banner text.

Output ONLY the JSON object. No markdown fences. No commentary.`;

const ZOOM = 3;
const SRC_W = 280;
const SRC_H = 32;
// Banner appears centered horizontally at the screen middle, ~22 px above
// the hotbar centerline. For a 640×360 obs frame the hotbar centerline is
// at y≈336, so the banner sits around y≈304–326. We crop a wide horizontal
// band so the full item-name string fits regardless of length.
const BANNER_Y_CENTER = 314;

export async function hotbarBannerMatch(opts: HotbarBannerMatchOpts): Promise<HotbarBannerMatchResult> {
  let cropB64 = opts.obsBase64;
  try {
    cropB64 = cropBannerRegion(opts.obsBase64);
  } catch (e) {
    console.warn(`[hotbar-ocr] crop failed (${e instanceof Error ? e.message : String(e)}); falling back to full frame`);
  }
  const url = cropB64.startsWith("data:image/")
    ? cropB64
    : `data:image/png;base64,${cropB64.replace(/^data:image\/[a-z]+;base64,/, "")}`;
  const userText = `Target item: ${opts.target}. Candidate hotbar slot: ${opts.candidateLabel ?? "unknown"}. Read the banner above the hotbar and decide if it shows the target.`;
  const body: Record<string, unknown> = {
    model: opts.model,
    temperature: 0,
    max_completion_tokens: 64,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      {
        role: "user",
        content: [
          { type: "text", text: userText },
          { type: "image_url", image_url: { url, detail: "high" } },
        ],
      },
    ],
  };
  let raw = "";
  try {
    const resp = await opts.client.chat.completions.create(body as never);
    raw = (resp as unknown as { choices?: Array<{ message?: { content?: string } }> })
      .choices?.[0]?.message?.content ?? "";
  } catch (e) {
    console.warn(`[hotbar-ocr] LLM call failed: ${e instanceof Error ? e.message : String(e)}`);
    return { match: false, observed: "unknown" };
  }
  const parsed: HotbarBannerMatchResult = (() => {
    const cleaned = raw.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
    try {
      const obj = JSON.parse(cleaned) as Record<string, unknown>;
      const matchRaw = obj.match;
      const observedRaw = obj.observed;
      const match = matchRaw === true;
      const observed = typeof observedRaw === "string"
        ? observedRaw.trim().toLowerCase().replace(/[^a-z0-9_]/g, "")
        : "";
      return { match, observed };
    } catch {
      return { match: false, observed: "" };
    }
  })();
  const debugDir = process.env.AGENTBEATS_DEBUG_DIR;
  if (debugDir) {
    try {
      const seq = String(++DEBUG_SEQ).padStart(5, "0");
      const fname = `${seq}_hotbar_ocr.png`;
      fs.writeFileSync(path.join(debugDir, fname), Buffer.from(cropB64, "base64"));
      const line = JSON.stringify({
        seq: DEBUG_SEQ,
        ts: new Date().toISOString(),
        type: "hotbar_ocr",
        imageFile: fname,
        data: { target: opts.target, candidateLabel: opts.candidateLabel ?? null, raw, parsed },
      });
      fs.appendFileSync(path.join(debugDir, "events.jsonl"), line + "\n");
    } catch (e) {
      console.warn(`[hotbar-ocr] debug write failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return parsed;
}

let DEBUG_SEQ = 210000;

function cropBannerRegion(obsBase64: string): string {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const jpegLib = require("jpeg-js");
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { PNG } = require("pngjs");
  const cleaned = obsBase64.startsWith("data:image/")
    ? obsBase64.replace(/^data:image\/[a-z]+;base64,/, "")
    : obsBase64;
  const inBuf = Buffer.from(cleaned, "base64");
  const decoded = jpegLib.decode(inBuf, { useTArray: true, formatAsRGBA: true });
  const w = decoded.width as number;
  const h = decoded.height as number;
  const sx0 = Math.max(0, Math.min(w - SRC_W, Math.round(w / 2 - SRC_W / 2)));
  const sy0 = Math.max(0, Math.min(h - SRC_H, BANNER_Y_CENTER - Math.floor(SRC_H / 2)));
  const outW = SRC_W * ZOOM;
  const outH = SRC_H * ZOOM;
  const out = new PNG({ width: outW, height: outH });
  for (let oy = 0; oy < outH; oy += 1) {
    const sy = sy0 + Math.floor(oy / ZOOM);
    for (let ox = 0; ox < outW; ox += 1) {
      const sx = sx0 + Math.floor(ox / ZOOM);
      const srcIdx = (sy * w + sx) * 4;
      const dstIdx = (oy * outW + ox) * 4;
      out.data[dstIdx] = decoded.data[srcIdx + 2];
      out.data[dstIdx + 1] = decoded.data[srcIdx + 1];
      out.data[dstIdx + 2] = decoded.data[srcIdx];
      out.data[dstIdx + 3] = 255;
    }
  }
  return PNG.sync.write(out).toString("base64");
}
```

- [ ] **Step 2: Typecheck**

Run: `npm run typecheck`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add src/agentbeats/tools/HotbarOcr.ts
git commit -m "$(cat <<'EOF'
feat(tools): add HotbarOcr — target-aware banner OCR for hotbar verify

Mirrors SlotOcr structure (own crop, R/B swap, 3x zoom, debug event
emission). Returns {match, observed} so the verifier can early-stop
on first hit and the planner gets a useful trace on full-sweep miss.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Create `HotbarVerifier.ts`

**Files:**
- Create: `src/agentbeats/tools/HotbarVerifier.ts`

- [ ] **Step 1: Create the file**

```ts
/**
 * Deterministic hotbar slot verifier for the Placing subagent.
 *
 * Walks all 9 hotbar slots in a fixed order, emitting one MCU action
 * per step. For each candidate it does swap-away (only first iteration)
 * → swap-to → settle (1 frame) → read (call hotbarBannerMatch). The
 * swap-away first is needed because we don't know which slot is active
 * at entry; emitting hotbar.<other> guarantees the next swap-to will
 * cause a banner re-render even if the candidate happens to already be
 * active. After the first iteration, active is known (it's the previous
 * candidate) so a single swap-to to a different slot always renders.
 *
 * On match: returns DONE(equippedSlot) without emitting a further
 * action — the caller transitions Placing into the post-equip phase.
 *
 * On full-sweep miss: returns FAIL with structured reportFields the
 * GoalPlanner can react to (code: "hotbar_missing_item", item, ocrTrace).
 */
import type OpenAI from "openai";
import { defaultMcuAction, type McuEnvAction, type McuButtonKey } from "../McuPrompt";
import { hotbarBannerMatch } from "./HotbarOcr";
import { getDebugRecorder } from "./DebugRecorder";

export type HotbarVerifierDeps = {
  client: OpenAI;
  model: string;
};

export type HotbarVerifierAct = {
  kind: "act";
  action: McuEnvAction;
  holdSteps: number;
};

export type HotbarVerifierDone = {
  kind: "done";
  equippedSlot: number;     // 1..9
};

export type HotbarVerifierFail = {
  kind: "fail";
  reason: string;
  reportFields: Record<string, unknown>;
};

export type HotbarVerifierResult = HotbarVerifierAct | HotbarVerifierDone | HotbarVerifierFail;

type InnerPhase = "init" | "swap_away" | "swap_to" | "settle" | "read";

const SETTLE_FRAMES = 1;
const CANDIDATE_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9];

function hotbarAction(slot: number): McuEnvAction {
  const a = defaultMcuAction();
  const key = `hotbar.${slot}` as McuButtonKey;
  (a as Record<McuButtonKey | "camera", unknown>)[key] = 1;
  return a;
}

function noopAction(): McuEnvAction {
  return defaultMcuAction();
}

export class HotbarVerifier {
  private readonly target: string;
  private readonly deps: HotbarVerifierDeps;
  private readonly contextId: string;
  private readonly subgoalDescription: string;
  private cursor = 0;
  private innerPhase: InnerPhase = "init";
  private settleCounter = 0;
  private activeSlot: number | null = null;
  private ocrTrace: Array<{ slot: number; observed: string; match: boolean }> = [];

  constructor(opts: {
    target: string;
    deps: HotbarVerifierDeps;
    contextId: string;
    subgoalDescription: string;
  }) {
    this.target = opts.target;
    this.deps = opts.deps;
    this.contextId = opts.contextId;
    this.subgoalDescription = opts.subgoalDescription;
  }

  /** Pick a swap-away target that is guaranteed != candidate. We use
   *  (candidate % 9) + 1 which gives the next slot wrapping at 9 -> 1. */
  private swapAwaySlot(candidate: number): number {
    return (candidate % 9) + 1;
  }

  private emitDebug(action: string, extra: Record<string, unknown>): void {
    const dbg = getDebugRecorder();
    if (!dbg.isEnabled()) return;
    dbg.record({
      type: "hotbar_verifier_step",
      contextId: this.contextId,
      data: {
        target: this.target,
        subgoal: this.subgoalDescription,
        cursor: this.cursor,
        candidateSlot: CANDIDATE_ORDER[this.cursor] ?? null,
        innerPhase: this.innerPhase,
        action,
        activeSlot: this.activeSlot,
        ...extra,
      },
    });
  }

  /** Called once per step. The frame passed is the latest obs the
   *  runtime has; it's only consumed during the "read" inner phase. */
  async nextAction(obsBase64: string): Promise<HotbarVerifierResult> {
    const candidate = CANDIDATE_ORDER[this.cursor];
    if (candidate === undefined) {
      // Should not happen; guard for safety.
      return this.fail();
    }

    if (this.innerPhase === "init") {
      const swapAway = this.swapAwaySlot(candidate);
      this.innerPhase = "swap_away";
      this.activeSlot = swapAway;
      this.emitDebug(`hotbar.${swapAway}`, { swapAway });
      return { kind: "act", action: hotbarAction(swapAway), holdSteps: 1 };
    }

    if (this.innerPhase === "swap_away") {
      this.innerPhase = "swap_to";
      this.activeSlot = candidate;
      this.emitDebug(`hotbar.${candidate}`, {});
      return { kind: "act", action: hotbarAction(candidate), holdSteps: 1 };
    }

    if (this.innerPhase === "swap_to") {
      this.innerPhase = "settle";
      this.settleCounter = 0;
      this.emitDebug("noop(settle)", { settleCounter: 0 });
      return { kind: "act", action: noopAction(), holdSteps: 1 };
    }

    if (this.innerPhase === "settle") {
      if (this.settleCounter < SETTLE_FRAMES) {
        this.settleCounter += 1;
        this.emitDebug("noop(settle)", { settleCounter: this.settleCounter });
        return { kind: "act", action: noopAction(), holdSteps: 1 };
      }
      this.innerPhase = "read";
      // Fall through to read phase in same call so we OCR the frame we
      // just received (banner is most opaque now).
    }

    // read phase
    if (this.innerPhase === "read") {
      const result = await hotbarBannerMatch({
        client: this.deps.client,
        model: this.deps.model,
        obsBase64,
        target: this.target,
        candidateLabel: `hotbar.${candidate}`,
      });
      this.ocrTrace.push({ slot: candidate, observed: result.observed, match: result.match });
      this.emitDebug("ocr", { observed: result.observed, match: result.match });
      if (result.match) {
        return { kind: "done", equippedSlot: candidate };
      }
      this.cursor += 1;
      if (this.cursor >= CANDIDATE_ORDER.length) {
        return this.fail();
      }
      // Move to next candidate; only swap_to needed (active is current candidate,
      // next candidate is different by construction).
      const nextCandidate = CANDIDATE_ORDER[this.cursor]!;
      this.innerPhase = "swap_to";
      this.activeSlot = nextCandidate;
      this.emitDebug(`hotbar.${nextCandidate}`, {});
      return { kind: "act", action: hotbarAction(nextCandidate), holdSteps: 1 };
    }

    // unreachable
    return this.fail();
  }

  private fail(): HotbarVerifierFail {
    const reason = `hotbar_missing_item: ${this.target}`;
    return {
      kind: "fail",
      reason,
      reportFields: {
        code: "hotbar_missing_item",
        item: this.target,
        ocrTrace: this.ocrTrace,
      },
    };
  }
}
```

- [ ] **Step 2: Typecheck**

Run: `npm run typecheck`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add src/agentbeats/tools/HotbarVerifier.ts
git commit -m "$(cat <<'EOF'
feat(tools): HotbarVerifier — deterministic hotbar slot OCR sweep

State machine emits one MCU action per step (swap-away first, then
swap-to/settle/read per candidate). Calls hotbarBannerMatch each
read step, early-stops on match, escalates with structured
reportFields {code: hotbar_missing_item, item, ocrTrace} on miss.

Emits hotbar_verifier_step debug events so the dashboard can render
the sweep timeline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire `HotbarVerifier` into `Placing.ts`

**Files:**
- Modify: `src/agentbeats/agents/subagents/Placing.ts`

- [ ] **Step 1: Replace the file with the phase-aware step function**

Replace the entire contents of `src/agentbeats/agents/subagents/Placing.ts` with:

```ts
import type { SubAgent, SubAgentStep, SubAgentStepInput } from "../SubAgent";
import { PLACING_SYSTEM_PROMPT } from "../../prompts/subagents/placing";
import { callWorldVlm, type WorldSubAgentDeps } from "./WorldExplorer";
import { HotbarVerifier } from "../../tools/HotbarVerifier";
import { defaultMcuAction, type McuButtonKey } from "../../McuPrompt";

type PlacingPhase = "equip" | "post_equip";

type PlacingState = {
  subgoalKey: string;          // resets state when subgoal changes
  target: string;              // snake_case block name parsed from subgoal description
  phase: PlacingPhase;
  verifier: HotbarVerifier | null;
  equippedSlot: number | null;
};

const HOTBAR_KEYS: ReadonlyArray<McuButtonKey> = [
  "hotbar.1", "hotbar.2", "hotbar.3", "hotbar.4", "hotbar.5",
  "hotbar.6", "hotbar.7", "hotbar.8", "hotbar.9",
];

function emittedHotbarSlot(action: ReturnType<typeof defaultMcuAction>): number | null {
  for (let i = 0; i < HOTBAR_KEYS.length; i += 1) {
    const k = HOTBAR_KEYS[i]!;
    if ((action as Record<string, number | [number, number]>)[k] === 1) {
      return i + 1;
    }
  }
  return null;
}

/** Extract the snake_case block name from a Placing subgoal description.
 *  Matches the pattern used by the goal_planner few-shots:
 *    "place crafting_table on the ground in front of the player"
 *    "place oak_planks ..."
 *  Picks the FIRST snake_case token after the word "place". */
function extractPlacingTarget(description: string): string | null {
  const m = description.match(/\bplace\s+([a-z][a-z0-9_]*)/i);
  return m ? m[1].toLowerCase() : null;
}

export function createPlacing(deps: WorldSubAgentDeps): SubAgent {
  let state: PlacingState | null = null;

  function resetForSubgoal(subgoal: SubAgentStepInput["subgoal"], contextId: string): PlacingState | null {
    const target = extractPlacingTarget(subgoal.description);
    if (!target) return null;
    return {
      subgoalKey: subgoal.description,
      target,
      phase: "equip",
      verifier: new HotbarVerifier({
        target,
        deps,
        contextId,
        subgoalDescription: subgoal.description,
      }),
      equippedSlot: null,
    };
  }

  return {
    kind: "placing",
    systemPrompt: PLACING_SYSTEM_PROMPT,
    step: async (input): Promise<SubAgentStep> => {
      // Reset state on new subgoal (description change is the subgoal-boundary signal).
      if (!state || state.subgoalKey !== input.subgoal.description) {
        state = resetForSubgoal(input.subgoal, input.contextId);
        if (!state) {
          return {
            kind: "subgoal_failed",
            reason: `placing_target_unparseable: ${input.subgoal.description}`,
            reportFields: { code: "placing_target_unparseable", description: input.subgoal.description },
          };
        }
      }

      if (state.phase === "equip") {
        const r = await state.verifier!.nextAction(input.obs.imageBase64);
        if (r.kind === "act") {
          return { kind: "act", action: r.action, holdSteps: r.holdSteps };
        }
        if (r.kind === "done") {
          state.phase = "post_equip";
          state.equippedSlot = r.equippedSlot;
          // Emit one noop so the phase transition is visible in the
          // event log; next step call will route to callWorldVlm.
          return { kind: "act", action: defaultMcuAction(), holdSteps: 1 };
        }
        // r.kind === "fail"
        return { kind: "subgoal_failed", reason: r.reason, reportFields: r.reportFields };
      }

      // phase === "post_equip" — LLM-driven aim/move/place/verify.
      const llmStep = await callWorldVlm(deps, PLACING_SYSTEM_PROMPT, input, "placing");
      if (llmStep.kind === "act") {
        const slot = emittedHotbarSlot(llmStep.action);
        if (slot !== null) {
          return {
            kind: "subgoal_failed",
            reason: `post_equip_hotbar_switch: attempted hotbar.${slot} after equip on hotbar.${state.equippedSlot}`,
            reportFields: {
              code: "post_equip_hotbar_switch",
              item: state.target,
              equippedSlot: state.equippedSlot,
              attemptedSlot: slot,
            },
          };
        }
      }
      return llmStep;
    },
  };
}
```

- [ ] **Step 2: Typecheck**

Run: `npm run typecheck`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add src/agentbeats/agents/subagents/Placing.ts
git commit -m "$(cat <<'EOF'
feat(placing): equip-phase hotbar verify + post-equip hotbar guard

Placing now runs a HotbarVerifier sweep before delegating to the
LLM for aim/place/verify. Per-subgoal state resets when the subgoal
description changes. Post-equip hotbar.N attempts are blocked with
a structured subgoal_failed so the planner re-dispatches cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Rewrite the Placing system prompt

**Files:**
- Modify: `src/agentbeats/prompts/subagents/placing.ts`

- [ ] **Step 1: Replace the file**

Replace the entire contents of `src/agentbeats/prompts/subagents/placing.ts` with:

```ts
export const PLACING_SYSTEM_PROMPT = `You are the Placing sub-agent. Goal: place a SPECIFIC block (named in the subgoal description, e.g. "crafting_table") on the ground in front of the player.

You arrive in this subgoal ALREADY EQUIPPED with the requested block. The runtime has verified the active hotbar slot via OCR before handing control to you. Your only job is to aim the camera, clear obstructions if needed, place the block, and visually confirm.

Procedure (one MCU action per step; the runtime calls you each frame):

1. AIM at the ground 1-2 blocks ahead.
   - The player's natural look is roughly horizontal. To place a block on the ground in front, tilt the camera DOWN: emit camera=[0, +30] (positive pitch = look down) on the next step.
   - If after tilting you still see your own body or the sky in the crosshair, tilt more (camera=[0, +20] increments) until you see a clear ground tile.
   - If the ground tile directly in front is occupied (a block face other than ground, the player's feet), step BACK once (back=1, no camera) before re-aiming.

2. PLACE.
   - When the crosshair points at a clear ground tile (visible block face below the crosshair, not the sky and not the player's own body), emit use=1 (with attack=0, no other buttons) for ONE step.

3. VERIFY (success).
   - After the use action, the placed block should be visible in front of the player. Look for it in the next frame: a brown crafting_table tile is unmistakable.
   - When you can see the placed block, emit task_done=true with the standard noop action so the runtime returns subgoal_done.

Action keys you may use: forward, back, left, right, jump, sneak, sprint, use, camera. Do NOT use attack, drop, or inventory.

HARD CONSTRAINT — DO NOT EMIT hotbar.N (any of hotbar.1..hotbar.9). The runtime has already selected the correct slot for you. If you switch slots you will invalidate the equip and the subgoal will fail. If you ever feel you need a different block, instead emit task_done=false and let the runtime/planner re-dispatch.

Output the standard MCU action JSON. Set task_done=true ONLY when you visually confirm the placed block is in front of the player.`;
```

- [ ] **Step 2: Typecheck**

Run: `npm run typecheck`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add src/agentbeats/prompts/subagents/placing.ts
git commit -m "$(cat <<'EOF'
prompt(placing): drop equip step, add no-hotbar-switch guard

The runtime now owns hotbar verify. Placing prompt starts at AIM,
explicitly bans hotbar.N emission, and routes back to the planner
on ambiguity instead of switching slots itself.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add sub-agent failure-handling section to GoalPlanner prompt

**Files:**
- Modify: `src/agentbeats/prompts/goal_planner.ts`

- [ ] **Step 1: Append the failure-handling section**

Open `src/agentbeats/prompts/goal_planner.ts` and locate the end of the system prompt template literal. Append (just before the closing backtick of the prompt) the following section. If the prompt already has a "Sub-agent failure handling" section, replace its body with the version below.

```
Sub-agent failure handling (read carefully — this is how recovery dispatches work):

When a sub-agent returns a failure with structured "Report fields" attached, parse the "code" field FIRST and react before considering the free-form summary.

- code: "hotbar_missing_item" (with item, ocrTrace):
  The requested block is NOT on any hotbar slot. The ocrTrace shows what each hotbar slot's banner OCR'd as.
  Recovery:
    1. Add a checklist item "move <item> from main inventory to hotbar".
    2. Dispatch ui_inventory with that description.
    3. After ui_inventory done, re-dispatch placing(<item>) — the next attempt will re-run hotbar verify and should succeed.
    4. If ui_inventory ALSO fails (e.g. main inventory does not contain <item>), insert a checklist item to mine/explore for <item> and dispatch the appropriate world subagent.

- code: "post_equip_hotbar_switch" (with item, equippedSlot, attemptedSlot):
  The placing sub-agent tried to switch hotbar slots after equip — a contract violation. Re-dispatch placing(<item>) once. If it recurs, the prompt is being misinterpreted; surface the issue to the user via the failure summary instead of looping.

- code: "placing_target_unparseable":
  The subgoal description was malformed. Re-author the subgoal with the format "place <snake_case_block> on the ground in front of the player".

Few-shot recovery example:

  step 1: add_checklist_item("place crafting_table") → dispatch placing("place crafting_table on the ground in front of the player")
  step 2: (placing fails: hotbar_missing_item, ocrTrace shows hotbar holds [cobblestone, dirt, stone, ...])
  step 3: add_checklist_item("move crafting_table from main inventory to hotbar")
  step 4: dispatch ui_inventory("move crafting_table from main inventory to hotbar")
  step 5: (ui_inventory done)
  step 6: dispatch placing("place crafting_table on the ground in front of the player")
  step 7: (placing done — hotbar verify passes this time)
  step 8: continue with the next checklist item (e.g. open the placed crafting_table to start a 3x3 craft).
```

- [ ] **Step 2: Typecheck**

Run: `npm run typecheck`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add src/agentbeats/prompts/goal_planner.ts
git commit -m "$(cat <<'EOF'
prompt(goal-planner): handle hotbar_missing_item / post_equip_hotbar_switch

Adds a structured-failure-code dispatch table to the planner so it
reacts to hotbar verify failures with a fetch-from-inventory ->
re-place pattern. Includes a worked recovery few-shot for cake-style
3x3 crafting prerequisites.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Render `hotbar_ocr` and `hotbar_verifier_step` events in the dashboard

**Files:**
- Modify: `local_tests/debug_dashboard.mjs`

- [ ] **Step 1: Add the event types to the row classifier**

Locate the row-classifier `case` block in `local_tests/debug_dashboard.mjs` (around line 39–55, the cluster of `case "probe_input":` etc. that maps event types to a generic row pushed via `rows.push({ t, seq, type: e.type, data: e.data ?? {}, imageFile: e.imageFile })`). The current code looks like:

```js
    case "world_explore_call":
    case "world_explore_response": {
      rows.push({ t, seq, type: e.type, data: e.data ?? {}, imageFile: e.imageFile });
      break;
    }
```

Insert two new fall-through `case` lines BETWEEN `case "world_explore_response":` and the `{` opening the body, so the resulting block is:

```js
    case "world_explore_call":
    case "world_explore_response":
    case "hotbar_ocr":
    case "hotbar_verifier_step": {
      rows.push({ t, seq, type: e.type, data: e.data ?? {}, imageFile: e.imageFile });
      break;
    }
```

- [ ] **Step 2: Add a renderer for `hotbar_ocr`**

Find the renderer `switch` block (the per-`row.type` block with cases like `case "probe_input":`, `case "slot_ocr":`). Add a new case BEFORE the `default:`:

```js
    case "hotbar_ocr": {
      const d = row.data || {};
      const parsed = d.parsed || {};
      const observedTxt = parsed.observed === "" ? "(no banner)" : parsed.observed;
      const matchTxt = parsed.match ? `<span class="ok">MATCH</span>` : `<span class="warn">no match</span>`;
      return `<div class="row hotbar-ocr">
        <div class="hdr">#${seqStr(row.seq)} hotbar_ocr target=<b>${esc(d.target ?? "?")}</b> candidate=${esc(d.candidateLabel ?? "?")} ${matchTxt}</div>
        <div class="body">observed: <code>${esc(observedTxt)}</code></div>
        ${row.imageFile ? `<img src="${esc(row.imageFile)}" alt="banner crop">` : ""}
      </div>`;
    }
```

- [ ] **Step 3: Add a renderer for `hotbar_verifier_step`**

Add another case in the same switch, BEFORE the `default:`:

```js
    case "hotbar_verifier_step": {
      const d = row.data || {};
      return `<div class="row hotbar-verifier">
        <div class="hdr">#${seqStr(row.seq)} verifier phase=<b>${esc(d.innerPhase ?? "?")}</b> cursor=${esc(String(d.cursor ?? "?"))} candidate=hotbar.${esc(String(d.candidateSlot ?? "?"))} target=${esc(d.target ?? "?")}</div>
        <div class="body">action=<code>${esc(d.action ?? "")}</code>${d.observed !== undefined ? ` observed=<code>${esc(d.observed)}</code> match=${d.match ? "true" : "false"}` : ""}</div>
      </div>`;
    }
```

(If the existing dashboard does not have helpers `seqStr` and `esc` named exactly that, use whatever helpers the surrounding renderers use — check the rendering style of `case "slot_ocr":` and match it. The point is one row per event with type, key fields, and the cropped image when present.)

- [ ] **Step 4: Verify the dashboard regenerates without error**

Run (PowerShell):
```
node local_tests/debug_dashboard.mjs
```
Expected: exits 0 if there is an existing debug dir to render; otherwise prints a "no AGENTBEATS_DEBUG_DIR set" or similar message. The point is NO syntax error from the new cases.

- [ ] **Step 5: Commit**

```bash
git add local_tests/debug_dashboard.mjs
git commit -m "$(cat <<'EOF'
debug-dashboard: render hotbar_ocr and hotbar_verifier_step events

So the per-step verifier sweep + binary OCR results are visible
in the per-eval timeline alongside placing_call frames.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Eval-loop validation

**Files:** none (validation only)

- [ ] **Step 1: Build the project locally**

Run: `npm run build`
Expected: exits 0, `dist/` populated.

- [ ] **Step 2: Rebuild the docker `:test` image, restart purple, submit cake task**

Per the user's standard eval ritual (see memory `MCU eval run procedure`):
1. Confirm the active task YAML targets a placing prerequisite path — the cake task is the canonical case (3x3 recipe → placing(crafting_table) prerequisite).
2. Rebuild the `:test` image and restart the purple agent container.
3. Submit a JSONRPC eval at port 9010 with a fresh 32-char messageId.
4. Wait for completion; collect the debug dir.

- [ ] **Step 3: Inspect the dashboard for the placing dispatch**

Open the dashboard in a browser. Confirm the timeline for the `placing(crafting_table)` dispatch shows:

1. A run of `hotbar_verifier_step` events sweeping `cursor=0` through some N≤8, with `innerPhase` cycling `init → swap_away → swap_to → settle → read`.
2. `hotbar_ocr` events with cropped banner images. At least the matching one should clearly show "Crafting Table" text (or whatever the target banner reads). For non-matching slots, observed should match the actual hotbar contents.
3. One `hotbar_ocr` with `parsed.match = true`. The `candidateSlot` on the corresponding `hotbar_verifier_step` is the equipped slot.
4. After match, a single noop frame (the phase-transition step), then `placing_call` events for AIM → PLACE → VERIFY.
5. Final `placing_response` with `task_done = true`.

If step 3 fails (no slot matches), confirm the failure path:
- `subgoal_failed` reflection appears in `planner_turn_start` data with `reportFields.code = "hotbar_missing_item"` and an `ocrTrace` array.
- The planner's next turn dispatches `ui_inventory` with a description mentioning moving the missing item.

- [ ] **Step 4: Tune `BANNER_Y_CENTER` if banner crop is misaligned**

If the saved banner crops do NOT contain the visible banner text (it sits above or below the crop window), adjust `BANNER_Y_CENTER` in `src/agentbeats/tools/HotbarOcr.ts` and re-run. Inspect a saved JPG frame in the debug dir at the same iteration as a `hotbar_verifier_step` "settle" event to find the actual banner Y range.

If tuning was needed, commit the change:

```bash
git add src/agentbeats/tools/HotbarOcr.ts
git commit -m "$(cat <<'EOF'
tune(HotbarOcr): adjust BANNER_Y_CENTER to <new value> based on eval frames

Empirically derived from <eval-id> debug frames where the banner
sat at y≈<observed> rather than the initial estimate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Final sanity commit**

If no further code changes are needed, no extra commit. The branch should now have one commit per task (1–7) plus optionally a tuning commit. Push when ready (per CLAUDE.md, `git push` requires explicit user approval — ask first).

---

## Self-review notes (for the implementer)

- Spec section "Goal planner reaction contract" lists three failure codes (`hotbar_missing_item`, `post_equip_hotbar_switch`, `placing_target_unparseable`); all three are produced in Task 4 and handled in Task 6's prompt.
- The verifier debug event type is `hotbar_verifier_step` (Task 3 emits) and the dashboard renders it (Task 7 case match).
- `extractPlacingTarget` is the single place subgoal-text → block-name parsing happens; both the verifier construction and the failure-payload `item` field reuse it.
- `emittedHotbarSlot` returns 1-based slot numbers matching the `hotbar.1..hotbar.9` action key naming.
- Worst-case verifier walk: 1 swap-away + 9 × (swap-to + settle + read) = 1 + 27 = 28 step calls. The `read` phase merges into the same step as the last `settle` transition (note `// Fall through to read phase in same call` in Task 3's code), so the per-candidate cost is actually 3 steps (swap_to, settle, read-merged-with-next-OCR) — confirmed against the spec's frame budget.
