"""
solution.py — Contestant template for Traffic Light Control Online Judge
=========================================================================
Reads JSON from stdin (platform -> contestant) each tick.
Prints JSON to stdout (contestant -> platform) with decision.

I/O Protocol:
  Platform sends:
    {"tick": int, "lights": {"N": "GREEN", ...}, "sensor_events": [...]}

  Contestant replies:
    {"action": "GIU_NGUYEN"}
    OR
    {"action": "CHUYEN_PHA", "duration": 30}
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
        lights = data["lights"]
        sensor_events = data["sensor_events"]

        # ── Your logic here ──────────────────────────────────────────────
        # Example: fixed-time control
        #   NS green for 30s, then yellow 3s, then EW green for 20s

        countdown = lights.get("countdown", 0)
        n_state = lights.get("N", "RED")

        if n_state == "GREEN" and countdown <= 0:
            # Switch to yellow (platform handles phase transitions)
            decision = {"action": "CHUYEN_PHA", "duration": 30}
        elif n_state == "RED" and countdown <= 0:
            decision = {"action": "CHUYEN_PHA", "duration": 20}
        else:
            decision = {"action": "GIU_NGUYEN"}

        # ── Output decision ──────────────────────────────────────────────
        print(json.dumps(decision), flush=True)


if __name__ == "__main__":
    solve()
