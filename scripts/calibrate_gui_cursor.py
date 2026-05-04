"""GUI cursor calibration for MCU action space.

Runs inside the green agent container (mcu-green-agent:local) which has
minestudio + Minecraft pre-installed. Spawns a sim with the same task
config the benchmark uses for craft_oak_planks (so inventory has items),
opens the inventory, then issues known camera deltas and records cursor
pixel positions to compute px-per-camera-unit.
"""
from __future__ import annotations

import os
import json
import numpy as np
import cv2
from pathlib import Path

from minestudio.simulator import MinecraftSim
from minestudio.simulator.callbacks import (
    CommandsCallback,
    FastResetCallback,
    RecordCallback,
    TaskCallback,
)
from minestudio.utils.vpt_lib.actions import Buttons


OBS_W, OBS_H = 640, 360
OUT_DIR = Path("/home/agent/output/calibration")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def make_action(buttons_dict: dict, camera=(0.0, 0.0)):
    """Build env-format action dict.

    env_action_to_agent_action expects every button key as a length-T ndarray
    (we use T=1) and camera as shape (T, 2). Scalars/lists fail downstream.
    """
    action = {btn: np.array([int(buttons_dict.get(btn, 0))], dtype=np.int64) for btn in Buttons.ALL}
    action["camera"] = np.array([[camera[0], camera[1]]], dtype=np.float32)
    return action


def find_cursor(frame: np.ndarray) -> tuple[int, int] | None:
    """Locate the white GUI cursor in a frame.

    Strategy: cursor is a small bright white arrow (~16x16 px). We mask near-
    white pixels in the inventory background region and find the centroid of
    the largest connected component.
    """
    # Restrict to inventory region (center 60% of screen) to avoid hotbar
    # text and other white UI elements at the edges.
    h, w = frame.shape[:2]
    x0, y0 = int(w * 0.15), int(h * 0.15)
    x1, y1 = int(w * 0.85), int(h * 0.85)
    roi = frame[y0:y1, x0:x1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Cursor is near-pure white; slot backgrounds are grey ~140-180
    _, mask = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY)
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    best_area = 0
    # Cursor area ~30-120 px depending on scale
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if 15 < area < 250 and area > best_area:
            best = centroids[i]
            best_area = area
    if best is None:
        return None
    return int(best[0] + x0), int(best[1] + y0)


def calibrate():
    # Use craft_oak_planks task config so the bot has stuff in inventory and
    # the inventory GUI shows the 2x2 craft grid.
    commands = [
        "/give @s minecraft:oak_log 10",
        "/give @s minecraft:crafting_table",
        "/give @s minecraft:apple 5",
    ]
    callbacks = [
        CommandsCallback(commands),
        TaskCallback([{"name": "calibration", "text": "calibration"}]),
        FastResetCallback(biomes=["plains", "forest"], random_tp_range=1000),
        RecordCallback(record_path=str(OUT_DIR), fps=20, frame_type="pov"),
    ]
    env = MinecraftSim(obs_size=(OBS_W, OBS_H), callbacks=callbacks)
    obs, info = env.reset()

    # Sequence: settle, open inventory, then apply a series of known deltas
    # and record cursor (px) after each.
    plan: list[tuple[str, dict, tuple[float, float], int]] = []
    # name, button-dict, camera-delta, hold_steps

    # Settle for a few frames
    for i in range(6):
        plan.append((f"settle{i}", {}, (0.0, 0.0), 1))
    # Open inventory
    plan.append(("open_inv", {"inventory": 1}, (0.0, 0.0), 1))
    plan.append(("post_open", {}, (0.0, 0.0), 4))
    # Calibration sweeps: vary one axis, hold the other at 0
    sweeps = [
        # (label, dyaw, dpitch)
        ("yaw+5", 5.0, 0.0),
        ("yaw+10", 10.0, 0.0),
        ("yaw-15", -15.0, 0.0),  # net -15 from starting +15
        ("pitch+5", 0.0, 5.0),
        ("pitch+10", 0.0, 10.0),
        ("pitch-15", 0.0, -15.0),
        ("diag+5+5", 5.0, 5.0),
        ("yaw+1", 1.0, 0.0),
        ("yaw+1b", 1.0, 0.0),
        ("yaw+1c", 1.0, 0.0),
    ]
    for label, dy, dp in sweeps:
        # Use env-format camera = [pitch, yaw] per minestudio convention
        plan.append((label, {}, (float(dp), float(dy)), 1))
        plan.append((f"{label}_settle", {}, (0.0, 0.0), 2))

    samples: list[dict] = []
    step = 0
    for label, btns, cam, hold in plan:
        for h in range(hold):
            action_dict = make_action(btns if h == 0 else {}, cam if h == 0 else (0.0, 0.0))
            agent_action = env.env_action_to_agent_action(action_dict)
            obs, reward, terminated, truncated, info = env.step(agent_action)
            frame = obs["image"]
            cur = find_cursor(frame)
            samples.append(
                {
                    "step": step,
                    "label": label if h == 0 else f"{label}_h{h}",
                    "cmd_pitch": cam[0] if h == 0 else 0.0,
                    "cmd_yaw": cam[1] if h == 0 else 0.0,
                    "cursor": cur,
                }
            )
            # Save every 4th frame for inspection
            if step % 4 == 0 or label.startswith("yaw") or label.startswith("pitch"):
                cv2.imwrite(str(OUT_DIR / f"step_{step:03d}_{label}.png"), frame)
            step += 1

    env.close()

    # Compute deltas relative to first detected cursor after inventory open
    # Walk samples; for each sweep step, compare cursor to the last settled cursor
    last_cursor = None
    deltas = []
    for s in samples:
        if s["cursor"] is None:
            continue
        if last_cursor is not None and (s["cmd_pitch"] != 0 or s["cmd_yaw"] != 0):
            dx = s["cursor"][0] - last_cursor[0]
            dy = s["cursor"][1] - last_cursor[1]
            deltas.append(
                {
                    "label": s["label"],
                    "cmd_pitch": s["cmd_pitch"],
                    "cmd_yaw": s["cmd_yaw"],
                    "px_dx": dx,
                    "px_dy": dy,
                }
            )
        last_cursor = s["cursor"]

    out = {
        "obs_size": [OBS_W, OBS_H],
        "samples": samples,
        "deltas": deltas,
    }
    with open(OUT_DIR / "calibration.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps({"deltas": deltas, "n_samples": len(samples)}, indent=2))


if __name__ == "__main__":
    calibrate()
