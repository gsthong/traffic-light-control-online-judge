"""
dummy_solution.py — Contestant template for Traffic Light Control Online Judge
===============================================================================
Reads JSON from stdin (platform -> contestant) each tick.
Prints JSON to stdout (contestant -> platform) with decision.

I/O Protocol:
  Platform sends: {"tick": int, "phase": int, "phase_timer": int,
                   "phase_remaining": int, "sensor_events": [...], "queues": {...}}
  Contestant replies: {"command": "GIU_NGUYEN"}
                   OR {"command": "CHUYEN_PHA", "new_phase": int, "new_duration": int}
"""

import json
import sys


def solve():
    """Main loop — reads from stdin, writes to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        tick = data["tick"]
        phase = data["phase"]
        phase_timer = data["phase_timer"]
        phase_remaining = data["phase_remaining"]
        sensor_events = data["sensor_events"]
        queues = data["queues"]

        # ── Your logic here ──────────────────────────────────────────────
        # Example: fixed-time control
        #   Phase 0 = NS green (30s)
        #   Phase 1 = EW green (20s)
        #   Phase 2 = Yellow (3s)

        if phase == 0:  # NS Green
            if phase_timer >= 30:
                decision = {"command": "CHUYEN_PHA", "new_phase": 2, "new_duration": 3}
            else:
                decision = {"command": "GIU_NGUYEN"}
        elif phase == 1:  # EW Green
            if phase_timer >= 20:
                decision = {"command": "CHUYEN_PHA", "new_phase": 2, "new_duration": 3}
            else:
                decision = {"command": "GIU_NGUYEN"}
        else:  # Yellow
            # After yellow, switch to the other green phase
            decision = {"command": "GIU_NGUYEN"}

        # ── Output decision ──────────────────────────────────────────────
        print(json.dumps(decision), flush=True)


if __name__ == "__main__":
    solve()
