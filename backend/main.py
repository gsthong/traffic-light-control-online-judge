import os
import json
import subprocess
import tempfile
import math
import shutil
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Traffic Light Control Online Judge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Models ────────────────────────────────────────────────────────────────────


class Submission(BaseModel):
    code: str
    username: str = "anonymous"
    engine_type: str = "cityflow"


# ── Constants ────────────────────────────────────────────────────────────────

ROAD_LENGTH = 500.0
INTERSECTION_WIDTH = 20.0
MAX_SPEED = 16.67
CANVAS_SIZE = 520
CX = CANVAS_SIZE / 2
CY = CANVAS_SIZE / 2
SCALE = CANVAS_SIZE / (2 * ROAD_LENGTH)
YELLOW_DUR = 3
LANE_WIDTH = 4.0

SCENARIOS = [
    {"name": "Balanced", "ns_flow": 500, "ew_flow": 500, "duration": 300},
    {"name": "N/S heavy", "ns_flow": 800, "ew_flow": 200, "duration": 300},
    {"name": "E/W heavy", "ns_flow": 200, "ew_flow": 800, "duration": 300},
    {"name": "Rush hour", "ns_flow": 900, "ew_flow": 900, "duration": 300},
    {"name": "Low traffic", "ns_flow": 150, "ew_flow": 150, "duration": 300},
]

# ── Road Network Definition ──────────────────────────────────────────────────
#
# 4-way intersection, 1 lane per direction (right-hand traffic).
# Roads go FROM the edge TO the intersection center (0,0).
#
#   road_N: (0, -500) -> (0, 0)   vehicles go SOUTH
#   road_S: (0,  500) -> (0, 0)   vehicles go NORTH
#   road_E: (500, 0)  -> (0, 0)   vehicles go WEST
#   road_W: (-500, 0) -> (0, 0)   vehicles go EAST

ROADS = {
    "road_N": {
        "sx": 0,
        "sy": -ROAD_LENGTH,
        "ex": 0,
        "ey": 0,
        "angle": math.pi / 2,
        "lane_offset": LANE_WIDTH / 2,
    },
    "road_S": {
        "sx": 0,
        "sy": ROAD_LENGTH,
        "ex": 0,
        "ey": 0,
        "angle": -math.pi / 2,
        "lane_offset": LANE_WIDTH / 2,
    },
    "road_E": {
        "sx": ROAD_LENGTH,
        "sy": 0,
        "ex": 0,
        "ey": 0,
        "angle": math.pi,
        "lane_offset": LANE_WIDTH / 2,
    },
    "road_W": {
        "sx": -ROAD_LENGTH,
        "sy": 0,
        "ex": 0,
        "ey": 0,
        "angle": 0.0,
        "lane_offset": LANE_WIDTH / 2,
    },
}

ROAD_TO_DIR = {"road_N": "S", "road_S": "N", "road_E": "W", "road_W": "E"}
GREEN_ROADS = {"NS": ["road_N", "road_S"], "EW": ["road_E", "road_W"]}


def road_pos_2d(road_id: str, distance: float) -> tuple:
    """
    Convert 1D (road_id, distance) -> 2D (world_x, world_y, angle).

    Uses CW perpendicular (dy/rl, -dx/rl) for RIGHT-hand traffic lane offset.
    """
    r = ROADS.get(road_id)
    if not r:
        return (0.0, 0.0, 0.0)

    sx, sy = r["sx"], r["sy"]
    ex, ey = r["ex"], r["ey"]

    dx = ex - sx
    dy = ey - sy
    road_len = math.sqrt(dx * dx + dy * dy)

    if road_len == 0:
        return (sx, sy, r["angle"])

    dist = min(distance, road_len)
    t = dist / road_len

    # Position on road centerline
    cx = sx + dx * t
    cy = sy + dy * t

    # CW perpendicular unit vector for right-hand traffic
    perp_x = dy / road_len
    perp_y = -dx / road_len

    offset = r["lane_offset"]
    lx = cx + perp_x * offset
    ly = cy + perp_y * offset

    return (lx, ly, r["angle"])


def world_to_canvas(wx: float, wy: float) -> tuple:
    """World meters -> canvas pixels. Y is flipped (canvas origin is top-left)."""
    return (CX + wx * SCALE, CY - wy * SCALE)


# ── Sandbox: Run user controller code safely ─────────────────────────────────

SANDBOX_TIMEOUT = 2.0


def run_user_controller(code: str, queues: dict, phase: str, timer: float) -> str:
    """Execute user's control() in an isolated subprocess."""
    wrapper = f"""
import sys, json
_DANGEROUS = {{'os','subprocess','socket','sys','ctypes','pickle','shutil',
              'multiprocessing','threading','http','urllib','requests',
              'pty','signal','resource','fcntl'}}
_original_import = __builtins__.__import__
def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    base = name.split('.')[0]
    if base in _DANGEROUS:
        raise ImportError(f"Import of '{{'{{'}}base{{'}}'}}' is not allowed")
    return _original_import(name, globals, locals, fromlist, level)
__builtins__.__import__ = _safe_import
{code}
try:
    queues = json.loads(sys.argv[1])
    current_phase = sys.argv[2]
    phase_timer = float(sys.argv[3])
    result = control(queues, current_phase, phase_timer)
    print(str(result))
except Exception:
    print("error")
    sys.exit(1)
"""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        delete=False,
        dir=tempfile.gettempdir(),
        encoding="utf-8",
    ) as f:
        f.write(wrapper)
        temp_path = f.name
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        res = subprocess.run(
            ["python", temp_path, json.dumps(queues), phase, str(timer)],
            capture_output=True,
            text=True,
            timeout=SANDBOX_TIMEOUT,
            env=env,
        )
        return res.stdout.strip()
    except subprocess.TimeoutExpired:
        return "error"
    except Exception:
        return "error"
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


# ── Single-Subprocess Scenario Runner (Pure Python fallback) ─────────────────

SCENARIO_RUNNER_TEMPLATE = """
import json, sys, math, random

{user_code}

ROAD_LENGTH = 500.0
CANVAS_SIZE = 520
CX = CANVAS_SIZE / 2
CY = CANVAS_SIZE / 2
SCALE = CANVAS_SIZE / (2 * ROAD_LENGTH)
MIN_GAP = 8.0
VEHICLE_LEN = 5.0
YELLOW_DUR = 3
ACCEL = 2.0
DECEL = 4.0
MAX_SPEED = 12.0
LANE_OFFSET = 2.0

ROADS = {{
    "road_N": {{"sx": 0, "sy": -ROAD_LENGTH, "ex": 0, "ey": 0, "angle": math.pi / 2, "lane_offset": LANE_OFFSET}},
    "road_S": {{"sx": 0, "sy": ROAD_LENGTH, "ex": 0, "ey": 0, "angle": -math.pi / 2, "lane_offset": -LANE_OFFSET}},
    "road_E": {{"sx": ROAD_LENGTH, "sy": 0, "ex": 0, "ey": 0, "angle": math.pi, "lane_offset": LANE_OFFSET}},
    "road_W": {{"sx": -ROAD_LENGTH, "sy": 0, "ex": 0, "ey": 0, "angle": 0.0, "lane_offset": -LANE_OFFSET}},
}}
ROAD_TO_DIR = {{"road_N": "S", "road_S": "N", "road_E": "W", "road_W": "E"}}
GREEN_ROADS = {{"NS": ["road_N", "road_S"], "EW": ["road_E", "road_W"]}}

def pos_on_road(road_id, distance):
    r = ROADS.get(road_id)
    if not r: return (0, 0, 0)
    sx, sy, ex, ey = r["sx"], r["sy"], r["ex"], r["ey"]
    dx = ex - sx; dy = ey - sy
    rl = math.sqrt(dx*dx + dy*dy)
    if rl == 0: return (sx, sy, r["angle"])
    t = min(distance / rl, 1.0)
    px = sx + dx * t; py = sy + dy * t
    # CW perpendicular for right-hand traffic
    perp_x = dy / rl; perp_y = -dx / rl
    px += perp_x * r["lane_offset"]; py += perp_y * r["lane_offset"]
    return (px, py, r["angle"])

def world_to_canvas(wx, wy):
    return (CX + wx * SCALE, CY - wy * SCALE)

class Veh:
    __slots__ = ('id','road_id','distance','speed','done','angle','lx','ly')
    def __init__(self, vid, road_id, distance, speed):
        self.id = vid; self.road_id = road_id; self.distance = distance
        self.speed = speed; self.done = False; self.angle = 0.0; self.lx = 0.0; self.ly = 0.0
    def update_pos(self):
        wx, wy, angle = pos_on_road(self.road_id, self.distance)
        self.lx = wx; self.ly = wy; self.angle = angle
    def to_replay(self):
        canvas_x, canvas_y = world_to_canvas(self.lx, self.ly)
        return {{"id": self.id, "x": round(canvas_x, 2), "y": round(canvas_y, 2), "road": self.road_id, "angle": round(self.angle, 4)}}

def run_scenario(scenario):
    ns_flow = scenario["ns_flow"]; ew_flow = scenario["ew_flow"]; duration = scenario["duration"]
    ns_interval = 3600.0 / ns_flow if ns_flow > 0 else 999
    ew_interval = 3600.0 / ew_flow if ew_flow > 0 else 999
    vehicles = []; vid_counter = 0; ns_acc = 0.0; ew_acc = 0.0
    replay_data = []; current_phase = "NS"; phase_timer = 0
    in_yellow = False; yellow_timer = 0; total_wait = 0.0; max_queue = 0; throughput = 0
    for step in range(duration):
        ns_acc += 1.0 / ns_interval if ns_interval < 999 else 0
        ew_acc += 1.0 / ew_interval if ew_interval < 999 else 0
        while ns_acc >= 1:
            ns_acc -= 1
            for road in ["road_N", "road_S"]:
                safe_dist = VEHICLE_LEN + MIN_GAP + 2.0
                if not any(v.road_id == road and v.distance < safe_dist and not v.done for v in vehicles):
                    v = Veh(f"v{{vid_counter}}", road, 0.0, 0.0); v.update_pos(); vehicles.append(v); vid_counter += 1
        while ew_acc >= 1:
            ew_acc -= 1
            for road in ["road_E", "road_W"]:
                safe_dist = VEHICLE_LEN + MIN_GAP + 2.0
                if not any(v.road_id == road and v.distance < safe_dist and not v.done for v in vehicles):
                    v = Veh(f"v{{vid_counter}}", road, 0.0, 0.0); v.update_pos(); vehicles.append(v); vid_counter += 1
        queues = {{"N": 0, "S": 0, "E": 0, "W": 0}}
        for v in vehicles:
            if not v.done and v.distance > ROAD_LENGTH - 80:
                d = ROAD_TO_DIR.get(v.road_id)
                if d: queues[d] += 1
        max_queue = max(max_queue, max(queues.values()))
        if not in_yellow:
            try:
                decision = control(queues, current_phase, phase_timer)
                if not isinstance(decision, str): decision = str(decision)
                decision = decision.strip().strip("'\"")
            except Exception: decision = "error"
            if decision == "yellow": in_yellow, yellow_timer = True, 0
            elif decision in ("NS", "EW") and decision != current_phase: in_yellow, yellow_timer = True, 0
        if in_yellow:
            yellow_timer += 1
            if yellow_timer >= YELLOW_DUR: in_yellow = False; current_phase = "EW" if current_phase == "NS" else "NS"; phase_timer = 0
        else: phase_timer += 1
        green_roads = GREEN_ROADS[current_phase] if not in_yellow else []
        for v in vehicles:
            if v.done: continue
            if v.distance >= ROAD_LENGTH: v.done = True; throughput += 1; continue
            min_gap = float("inf")
            for u in vehicles:
                if u.id == v.id or u.road_id != v.road_id or u.done: continue
                gap = u.distance - v.distance
                if 0 < gap < min_gap: min_gap = gap
            at_stop_line = v.distance > ROAD_LENGTH - 10
            red_light = v.road_id not in green_roads
            safe_brake_dist = VEHICLE_LEN + MIN_GAP
            desired_gap = safe_brake_dist + v.speed * 1.5
            if min_gap < safe_brake_dist: v.speed = max(0, v.speed - DECEL)
            elif min_gap < desired_gap:
                decel = DECEL * (1 - (min_gap - safe_brake_dist) / (desired_gap - safe_brake_dist + 0.01))
                v.speed = max(0, v.speed - decel)
            elif at_stop_line and red_light: v.speed = max(0, v.speed - DECEL)
            else:
                v.speed = min(v.speed + ACCEL, MAX_SPEED)
                if at_stop_line and red_light: v.speed = max(0, v.speed - DECEL)
            v.distance += v.speed; v.update_pos()
            if v.speed < 0.5 and not v.done: total_wait += 1
        active = [v for v in vehicles if not v.done]
        replay_data.append({{"tick": step, "vehicles": [v.to_replay() for v in active], "phase": current_phase, "in_yellow": in_yellow, "queues": queues}})
        total_wait += sum(queues.values())
    avg_wait = total_wait / max(duration, 1)
    score = max(0.0, min(100.0, 100.0 - avg_wait * 1.5))
    return {{"name": scenario["name"], "score": round(score, 1), "avg_wait": round(avg_wait, 1), "throughput": throughput, "max_queue": max_queue, "replay_data": replay_data}}

if __name__ == "__main__":
    scenario = json.loads(sys.argv[1])
    try:
        result = run_scenario(scenario)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))
        sys.exit(1)
"""

SCENARIO_TIMEOUT = 120


def run_py_scenario(scenario: dict, user_code: str) -> dict:
    """Run entire scenario in a single subprocess."""
    runner_code = SCENARIO_RUNNER_TEMPLATE.format(user_code=user_code)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        delete=False,
        dir=tempfile.gettempdir(),
        encoding="utf-8",
    ) as f:
        f.write(runner_code)
        temp_path = f.name
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        res = subprocess.run(
            ["python", temp_path, json.dumps(scenario)],
            capture_output=True,
            text=True,
            timeout=SCENARIO_TIMEOUT,
            env=env,
        )
        if res.returncode != 0:
            err_msg = res.stderr.strip()[:500] if res.stderr else "Unknown error"
            return {
                "name": scenario["name"],
                "score": 0.0,
                "avg_wait": 0.0,
                "throughput": 0,
                "max_queue": 0,
                "replay_data": [],
                "error": f"Simulation error: {err_msg}",
            }
        result = json.loads(res.stdout)
        if "error" in result:
            result.setdefault("replay_data", [])
            result.setdefault("score", 0.0)
            result.setdefault("avg_wait", 0.0)
            result.setdefault("throughput", 0)
            result.setdefault("max_queue", 0)
        return result
    except subprocess.TimeoutExpired:
        return {
            "name": scenario["name"],
            "score": 0.0,
            "avg_wait": 0.0,
            "throughput": 0,
            "max_queue": 0,
            "replay_data": [],
            "error": f"Scenario timed out after {SCENARIO_TIMEOUT}s",
        }
    except json.JSONDecodeError as e:
        return {
            "name": scenario["name"],
            "score": 0.0,
            "avg_wait": 0.0,
            "throughput": 0,
            "max_queue": 0,
            "replay_data": [],
            "error": f"Failed to parse result: {str(e)}",
        }
    except Exception as e:
        return {
            "name": scenario["name"],
            "score": 0.0,
            "avg_wait": 0.0,
            "throughput": 0,
            "max_queue": 0,
            "replay_data": [],
            "error": str(e),
        }
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


# ── Engine 1: CityFlow (PRIMARY) ─────────────────────────────────────────────


class CityFlowEngine:
    def name(self) -> str:
        return "cityflow"

    def _build_roadnet(self) -> dict:
        return {
            "intersections": [
                {
                    "id": "intersection_0",
                    "point": {"x": 0, "y": 0},
                    "width": INTERSECTION_WIDTH,
                    "roads": ["road_N", "road_E", "road_S", "road_W"],
                    "roadLinks": [
                        {
                            "type": "go_straight",
                            "startRoad": "road_N",
                            "endRoad": "road_S",
                            "laneLinks": [
                                {
                                    "startLaneIndex": 0,
                                    "endLaneIndex": 0,
                                    "points": [
                                        {"x": 0, "y": -INTERSECTION_WIDTH / 2},
                                        {"x": 0, "y": INTERSECTION_WIDTH / 2},
                                    ],
                                }
                            ],
                        },
                        {
                            "type": "go_straight",
                            "startRoad": "road_E",
                            "endRoad": "road_W",
                            "laneLinks": [
                                {
                                    "startLaneIndex": 0,
                                    "endLaneIndex": 0,
                                    "points": [
                                        {"x": INTERSECTION_WIDTH / 2, "y": 0},
                                        {"x": -INTERSECTION_WIDTH / 2, "y": 0},
                                    ],
                                }
                            ],
                        },
                        {
                            "type": "go_straight",
                            "startRoad": "road_S",
                            "endRoad": "road_N",
                            "laneLinks": [
                                {
                                    "startLaneIndex": 0,
                                    "endLaneIndex": 0,
                                    "points": [
                                        {"x": 0, "y": INTERSECTION_WIDTH / 2},
                                        {"x": 0, "y": -INTERSECTION_WIDTH / 2},
                                    ],
                                }
                            ],
                        },
                        {
                            "type": "go_straight",
                            "startRoad": "road_W",
                            "endRoad": "road_E",
                            "laneLinks": [
                                {
                                    "startLaneIndex": 0,
                                    "endLaneIndex": 0,
                                    "points": [
                                        {"x": -INTERSECTION_WIDTH / 2, "y": 0},
                                        {"x": INTERSECTION_WIDTH / 2, "y": 0},
                                    ],
                                }
                            ],
                        },
                    ],
                    "trafficLight": {
                        "lightphases": [
                            {"time": 30, "availableRoadLinks": [0, 2]},
                            {"time": 3, "availableRoadLinks": []},
                            {"time": 20, "availableRoadLinks": [1, 3]},
                            {"time": 3, "availableRoadLinks": []},
                        ]
                    },
                }
            ],
            "roads": [
                {
                    "id": "road_N",
                    "points": [{"x": 0, "y": -ROAD_LENGTH}, {"x": 0, "y": 0}],
                    "lanes": [{"width": LANE_WIDTH, "maxSpeed": MAX_SPEED}],
                },
                {
                    "id": "road_E",
                    "points": [{"x": ROAD_LENGTH, "y": 0}, {"x": 0, "y": 0}],
                    "lanes": [{"width": LANE_WIDTH, "maxSpeed": MAX_SPEED}],
                },
                {
                    "id": "road_S",
                    "points": [{"x": 0, "y": ROAD_LENGTH}, {"x": 0, "y": 0}],
                    "lanes": [{"width": LANE_WIDTH, "maxSpeed": MAX_SPEED}],
                },
                {
                    "id": "road_W",
                    "points": [{"x": -ROAD_LENGTH, "y": 0}, {"x": 0, "y": 0}],
                    "lanes": [{"width": LANE_WIDTH, "maxSpeed": MAX_SPEED}],
                },
            ],
        }

    def _build_flow(self, ns_flow: int, ew_flow: int, duration: int) -> list:
        vt = {
            "length": 5.0,
            "width": 2.0,
            "maxPosAcc": 2.0,
            "maxNegAcc": 4.5,
            "usualPosAcc": 2.0,
            "usualNegAcc": 4.5,
            "minGap": 2.5,
            "maxSpeed": MAX_SPEED,
            "headwayTime": 1.5,
        }
        return [
            {
                "vehicle": vt,
                "route": ["road_N", "road_S"],
                "interval": 3600.0 / ns_flow,
                "startTime": 0,
                "endTime": duration,
            },
            {
                "vehicle": vt,
                "route": ["road_S", "road_N"],
                "interval": 3600.0 / ns_flow,
                "startTime": 0,
                "endTime": duration,
            },
            {
                "vehicle": vt,
                "route": ["road_E", "road_W"],
                "interval": 3600.0 / ew_flow,
                "startTime": 0,
                "endTime": duration,
            },
            {
                "vehicle": vt,
                "route": ["road_W", "road_E"],
                "interval": 3600.0 / ew_flow,
                "startTime": 0,
                "endTime": duration,
            },
        ]

    def run_scenario(self, scenario: dict, user_code: str) -> dict:
        import cityflow

        with tempfile.TemporaryDirectory() as tmpdir:
            roadnet = self._build_roadnet()
            flow = self._build_flow(
                scenario["ns_flow"], scenario["ew_flow"], scenario["duration"]
            )
            with open(os.path.join(tmpdir, "roadnet.json"), "w") as f:
                json.dump(roadnet, f)
            with open(os.path.join(tmpdir, "flow.json"), "w") as f:
                json.dump(flow, f)
            config = {
                "interval": 1.0,
                "seed": 42,
                "dir": tmpdir,
                "roadnetFile": "roadnet.json",
                "flowFile": "flow.json",
                "rlTrafficLight": True,
                "saveReplay": False,
            }
            with open(os.path.join(tmpdir, "config.json"), "w") as f:
                json.dump(config, f)

            eng = cityflow.Engine(os.path.join(tmpdir, "config.json"), thread_num=1)
            replay_data = []
            current_phase = "NS"
            phase_timer = 0
            in_yellow = False
            yellow_timer = 0
            total_wait = 0.0
            max_queue = 0
            throughput = 0
            prev_count = 0

            for step in range(scenario["duration"]):
                lane_counts = eng.get_lane_vehicle_count()
                queues = {
                    "N": lane_counts.get("road_N_0", 0),
                    "S": lane_counts.get("road_S_0", 0),
                    "E": lane_counts.get("road_E_0", 0),
                    "W": lane_counts.get("road_W_0", 0),
                }
                max_queue = max(max_queue, max(queues.values()))

                if not in_yellow:
                    decision = run_user_controller(
                        user_code, queues, current_phase, phase_timer
                    )
                    decision = decision.strip().strip("'\"")
                    if decision == "yellow":
                        in_yellow, yellow_timer = True, 0
                    elif decision in ("NS", "EW") and decision != current_phase:
                        in_yellow, yellow_timer = True, 0

                if in_yellow:
                    eng.set_tl_phase(
                        "intersection_0", 1 if current_phase == "NS" else 3
                    )
                    yellow_timer += 1
                    if yellow_timer >= 3:
                        in_yellow = False
                        current_phase = "EW" if current_phase == "NS" else "NS"
                        phase_timer = 0
                else:
                    eng.set_tl_phase(
                        "intersection_0", 0 if current_phase == "NS" else 2
                    )
                    phase_timer += 1

                eng.next_step()

                vids = eng.get_vehicles(include_waiting=True)
                vlist = []
                for vid in vids:
                    info = eng.get_vehicle_info(vid)
                    if not info or info.get("running") != "True":
                        continue
                    drivable = info.get("drivable", info.get("road", ""))
                    road_id = (
                        "_".join(drivable.split("_")[:-1])
                        if "_" in drivable
                        else drivable
                    )
                    distance = float(info.get("distance", 0))
                    wx, wy, angle = road_pos_2d(road_id, distance)
                    canvas_x, canvas_y = world_to_canvas(wx, wy)
                    vlist.append(
                        {
                            "id": vid,
                            "x": round(canvas_x, 2),
                            "y": round(canvas_y, 2),
                            "angle": round(angle, 4),
                        }
                    )

                replay_data.append(
                    {
                        "tick": step,
                        "vehicles": vlist,
                        "phase": current_phase,
                        "in_yellow": in_yellow,
                        "queues": queues,
                    }
                )

                total_wait += sum(queues.values())
                cur = eng.get_vehicle_count()
                if cur < prev_count:
                    throughput += prev_count - cur
                prev_count = cur

            eng.stop()
            avg_wait = total_wait / max(scenario["duration"], 1)
            score = max(0.0, min(100.0, 100.0 - avg_wait * 1.5))
            return {
                "name": scenario["name"],
                "score": round(score, 1),
                "avg_wait": round(avg_wait, 1),
                "throughput": throughput,
                "max_queue": max_queue,
                "replay_data": replay_data,
            }


# ── Engine 2: SUMO (FALLBACK) ────────────────────────────────────────────────


class SumoEngine:
    def name(self) -> str:
        return "sumo"

    def _write_net_xml(self, path: str):
        nod_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<nodes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/nodes_file.xsd">
    <node id="C" x="0.0" y="0.0" type="traffic_light"/>
    <node id="N" x="0.0" y="-{ROAD_LENGTH}" type="priority"/>
    <node id="S" x="0.0" y="{ROAD_LENGTH}" type="priority"/>
    <node id="E" x="{ROAD_LENGTH}" y="0.0" type="priority"/>
    <node id="W" x="-{ROAD_LENGTH}" y="0.0" type="priority"/>
</nodes>"""
        edg_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<edges xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/edges_file.xsd">
    <edge id="N_to_C" from="N" to="C" priority="2" numLanes="1" speed="{MAX_SPEED}"/>
    <edge id="C_to_N" from="C" to="N" priority="2" numLanes="1" speed="{MAX_SPEED}"/>
    <edge id="S_to_C" from="S" to="C" priority="2" numLanes="1" speed="{MAX_SPEED}"/>
    <edge id="C_to_S" from="C" to="S" priority="2" numLanes="1" speed="{MAX_SPEED}"/>
    <edge id="E_to_C" from="E" to="C" priority="2" numLanes="1" speed="{MAX_SPEED}"/>
    <edge id="C_to_E" from="C" to="E" priority="2" numLanes="1" speed="{MAX_SPEED}"/>
    <edge id="W_to_C" from="W" to="C" priority="2" numLanes="1" speed="{MAX_SPEED}"/>
    <edge id="C_to_W" from="C" to="W" priority="2" numLanes="1" speed="{MAX_SPEED}"/>
</edges>"""
        tmpdir = os.path.dirname(path)
        nod_path = os.path.join(tmpdir, "nodes.nod.xml")
        edg_path = os.path.join(tmpdir, "edges.edg.xml")
        with open(nod_path, "w") as f:
            f.write(nod_xml)
        with open(edg_path, "w") as f:
            f.write(edg_xml)
        netconvert = shutil.which("netconvert")
        if not netconvert:
            raise RuntimeError("netconvert binary not found in PATH")
        subprocess.run(
            [
                netconvert,
                "--node-files=" + nod_path,
                "--edge-files=" + edg_path,
                "--output-file=" + path,
                "--no-internal-links",
            ],
            check=True,
            capture_output=True,
        )

    def _write_rou_xml(self, path: str, ns_flow: int, ew_flow: int, duration: int):
        rou_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/routes_file.xsd">
    <vType id="default" length="5" width="2" maxSpeed="{MAX_SPEED}" accel="2" decel="4" minGap="2.5" color="0,150,200"/>
    <flow id="NS" type="default" from="N_to_C" to="C_to_S" begin="0" end="{duration}" vehsPerHour="{ns_flow}"/>
    <flow id="SN" type="default" from="S_to_C" to="C_to_N" begin="0" end="{duration}" vehsPerHour="{ns_flow}"/>
    <flow id="EW" type="default" from="E_to_C" to="C_to_W" begin="0" end="{duration}" vehsPerHour="{ew_flow}"/>
    <flow id="WE" type="default" from="W_to_C" to="C_to_E" begin="0" end="{duration}" vehsPerHour="{ew_flow}"/>
</routes>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(rou_xml)

    def _write_sumocfg(self, path: str, net_path: str, rou_path: str):
        cfg_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/sumoConfiguration.xsd">
    <input>
        <net-file value="{net_path}"/>
        <route-files value="{rou_path}"/>
    </input>
    <time>
        <begin value="0"/><end value="300"/><step-length value="1.0"/>
    </time>
    <report>
        <no-warnings value="true"/><no-step-log value="true"/>
    </report>
    <random_number>
        <random value="true"/><seed value="42"/>
    </random_number>
</configuration>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(cfg_xml)

    def run_scenario(self, scenario: dict, user_code: str) -> dict:
        import traci
        import sumolib

        with tempfile.TemporaryDirectory() as tmpdir:
            net_path = os.path.join(tmpdir, "net.net.xml")
            rou_path = os.path.join(tmpdir, "routes.rou.xml")
            cfg_path = os.path.join(tmpdir, "sim.sumocfg")
            self._write_net_xml(net_path)
            self._write_rou_xml(
                rou_path, scenario["ns_flow"], scenario["ew_flow"], scenario["duration"]
            )
            self._write_sumocfg(cfg_path, net_path, rou_path)
            sumo_binary = shutil.which("sumo") or shutil.which("sumo-gui")
            if not sumo_binary:
                raise RuntimeError("SUMO binary not found in PATH")
            traci.start(
                [
                    sumo_binary,
                    "-c",
                    cfg_path,
                    "--no-step-log",
                    "--no-warnings",
                    "--quit-on-end",
                ]
            )
            replay_data = []
            current_phase = "NS"
            phase_timer = 0
            in_yellow = False
            yellow_timer = 0
            total_wait = 0.0
            max_queue = 0
            throughput = 0
            edge_map = {
                "N_to_C": "road_N",
                "S_to_C": "road_S",
                "E_to_C": "road_E",
                "W_to_C": "road_W",
            }
            duration = scenario["duration"]
            for step in range(duration):
                queues = {"N": 0, "S": 0, "E": 0, "W": 0}
                for sumo_id, our_id in edge_map.items():
                    count = traci.edge.getLastStepVehicleNumber(sumo_id)
                    d = ROAD_TO_DIR.get(our_id)
                    if d:
                        queues[d] += count
                max_queue = max(max_queue, max(queues.values()))
                if not in_yellow:
                    try:
                        decision = user_code(queues, current_phase, phase_timer)
                        if not isinstance(decision, str):
                            decision = str(decision)
                        decision = decision.strip().strip("'\"")
                    except Exception:
                        decision = "error"
                    if decision == "yellow":
                        in_yellow, yellow_timer = True, 0
                    elif decision in ("NS", "EW") and decision != current_phase:
                        in_yellow, yellow_timer = True, 0
                if in_yellow:
                    yellow_timer += 1
                    if yellow_timer >= 3:
                        in_yellow = False
                        current_phase = "EW" if current_phase == "NS" else "NS"
                        phase_timer = 0
                else:
                    phase_timer += 1
                tls_id = "C"
                try:
                    if in_yellow:
                        traci.trafficlight.setPhase(tls_id, 1)
                    elif current_phase == "NS":
                        traci.trafficlight.setPhase(tls_id, 0)
                    else:
                        traci.trafficlight.setPhase(tls_id, 2)
                except traci.TraCIException:
                    pass
                traci.simulationStep()
                vids = traci.vehicle.getIDList()
                vlist = []
                for vid in vids:
                    try:
                        x_sumo, y_sumo = traci.vehicle.getPosition(vid)
                        angle_sumo = traci.vehicle.getAngle(vid)
                        angle_math = math.pi / 2 - math.radians(angle_sumo)
                        canvas_x, canvas_y = world_to_canvas(x_sumo, y_sumo)
                        vlist.append(
                            {
                                "id": vid,
                                "x": round(canvas_x, 2),
                                "y": round(canvas_y, 2),
                                "angle": round(angle_math, 4),
                            }
                        )
                    except traci.TraCIException:
                        continue
                replay_data.append(
                    {
                        "tick": step,
                        "vehicles": vlist,
                        "phase": current_phase,
                        "in_yellow": in_yellow,
                        "queues": queues,
                    }
                )
                total_wait += sum(queues.values())
                throughput += traci.simulation.getDepartedNumber()
            traci.close()
            avg_wait = total_wait / max(duration, 1)
            score = max(0.0, min(100.0, 100.0 - avg_wait * 1.5))
            return {
                "name": scenario["name"],
                "score": round(score, 1),
                "avg_wait": round(avg_wait, 1),
                "throughput": throughput,
                "max_queue": max_queue,
                "replay_data": replay_data,
            }


# ── Engine Factory ───────────────────────────────────────────────────────────


def get_engine(engine_type: str):
    if engine_type == "cityflow":
        try:
            import cityflow

            return CityFlowEngine()
        except ImportError:
            try:
                import traci

                return SumoEngine()
            except ImportError:
                return None
    elif engine_type == "sumo":
        try:
            import traci

            return SumoEngine()
        except ImportError:
            return None
    return None


# ── API Endpoints ────────────────────────────────────────────────────────────


@app.post("/submit")
async def submit_code(submission: Submission):
    try:
        if not submission.code.strip():
            return JSONResponse(status_code=400, content={"error": "No code provided"})
        if "def control" not in submission.code:
            return JSONResponse(
                status_code=400,
                content={"error": "Code must define a 'control' function"},
            )
        engine = get_engine(submission.engine_type)
        if engine is None:
            engine_name = "python"
            runner = run_py_scenario
        else:
            engine_name = engine.name()
            runner = engine.run_scenario
        results = []
        total_score = 0.0
        for scenario in SCENARIOS:
            try:
                result = runner(scenario, submission.code)
                results.append(result)
                total_score += result.get("score", 0)
            except Exception as e:
                results.append(
                    {
                        "name": scenario["name"],
                        "score": 0.0,
                        "avg_wait": 0.0,
                        "throughput": 0,
                        "max_queue": 0,
                        "replay_data": [],
                        "error": str(e),
                    }
                )
        overall = round(total_score / len(SCENARIOS), 1) if SCENARIOS else 0.0
        return {"overall_score": overall, "scenarios": results, "engine": engine_name}
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Server error: {str(e)}",
                "overall_score": 0,
                "scenarios": [],
            },
        )


@app.get("/health")
async def health():
    try:
        import cityflow

        return {"status": "ok", "engine": "cityflow"}
    except ImportError:
        pass
    try:
        import traci

        return {"status": "ok", "engine": "sumo"}
    except ImportError:
        pass
    return {
        "status": "ok",
        "engine": "python",
        "message": "Using pure Python simulation",
    }


if __name__ == "__main__":
    import uvicorn

    try:
        import cityflow

        engine_name = "CityFlow"
    except ImportError:
        try:
            import traci

            engine_name = "SUMO"
        except ImportError:
            engine_name = "pure Python"
    print(f"Starting backend with {engine_name} engine...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
