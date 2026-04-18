"""
evaluator.py — Traffic Light Control Online Judge Platform (SUMO Engine)
 =========================================================================
Securely executes an untrusted contestant script (solution.py),
steps through a 3600-tick simulation loop via stdin/stdout pipes,
enforces strict hardware limits, and calculates final metrics.

Uses REAL SUMO physics via TraCI. No dummy data.
"""

import argparse
import json
import subprocess
import sys
import time
import os

import traci
import tempfile
import traceback
import math
import shutil
from typing import List, Dict, Any

# ── Constants ─────────────────────────────────────────────────────────────────

TOTAL_TICKS = 3600
WALL_CLOCK_LIMIT = 1200.0  # 20 minutes for SUMO
RAM_LIMIT_MB = 256
RAM_LIMIT_BYTES = RAM_LIMIT_MB * 1024 * 1024

PHASE_NS_GREEN = 0
PHASE_EW_GREEN = 1
PHASE_YELLOW = 2

DEFAULT_NS_DURATION = 30
DEFAULT_EW_DURATION = 20
YELLOW_DURATION = 3

# Replay: subsample ticks to cut JSON size / serialization time (sim still runs every tick).
REPLAY_TICK_STRIDE = 4
# Drop vehicles outside plausible road bbox around origin (meters).
REPLAY_CLIP_RADIUS_M = 520.0
# Must match src/App.tsx canvas logical size + SCALE (single source for replay pixels).
REPLAY_CANVAS_W = 520.0
REPLAY_CANVAS_H = 520.0
REPLAY_SCALE_PX_PER_M = 0.52
# Node file: outer endpoints ±500 m from junction C (must match App.tsx WORLD_HALF_M).
WORLD_HALF_M = 500.0

# ── Scenario Configs ─────────────────────────────────────────────────────────

SCENARIO_CONFIGS = {
    1: {"spawn_rate": 0.1, "bus_ratio": 0.05, "label": "Low Traffic"},
    2: {"spawn_rate": 0.25, "bus_ratio": 0.075, "label": "Light Traffic"},
    3: {"spawn_rate": 0.4, "bus_ratio": 0.1, "label": "Balanced"},
    4: {"spawn_rate": 0.6, "bus_ratio": 0.15, "label": "Heavy Traffic"},
    5: {"spawn_rate": 0.8, "bus_ratio": 0.2, "label": "Rush Hour"},
}

DEFAULT_LEVEL = 3


def generate_rou_xml(level_config: Dict[str, Any]) -> str:
    spawn_rate = level_config["spawn_rate"]
    bus_ratio = level_config["bus_ratio"]

    vehs_per_hour = int(spawn_rate * 5000)
    bus_vehs_per_hour = int(vehs_per_hour * bus_ratio)
    car_vehs_per_hour = vehs_per_hour - bus_vehs_per_hour

    routes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '        xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/routes_file.xsd">',
        "",
        '    <vType id="car" length="5.0" width="2.0" maxSpeed="16.67"',
        '           accel="2.0" decel="4.5" minGap="2.5" color="255,214,0"/>',
        '    <vType id="bus" length="12.0" width="2.5" maxSpeed="11.11"',
        '           accel="1.2" decel="3.0" minGap="5.0" color="200,100,0"/>',
        "",
    ]

    directions = [
        ("NS", "N_to_C", "C_to_S"),
        ("SN", "S_to_C", "C_to_N"),
        ("EW", "E_to_C", "C_to_W"),
        ("WE", "W_to_C", "C_to_E"),
    ]

    for dir_id, from_edge, to_edge in directions:
        for lane_idx in range(2):
            route_id = f"{dir_id}_{lane_idx}"
            if car_vehs_per_hour > 0:
                routes.append(
                    f'    <flow id="car_{route_id}" type="car" from="{from_edge}" to="{to_edge}"'
                    f' begin="0" end="3600" vehsPerHour="{car_vehs_per_hour}" departLane="{lane_idx}"/>'
                )
            if bus_vehs_per_hour > 0:
                routes.append(
                    f'    <flow id="bus_{route_id}" type="bus" from="{from_edge}" to="{to_edge}"'
                    f' begin="0" end="3600" vehsPerHour="{bus_vehs_per_hour}" departLane="{lane_idx}"/>'
                )

    routes.append("</routes>")
    return "\n".join(routes)


# ── SUMO Simulation Engine ───────────────────────────────────────────────────


class SumoSimulationEngine:
    """
    Real SUMO physics engine via TraCI.
    Manages a 4-way intersection with 2 lanes per road.
    """

    EDGE_TO_DIR = {
        "N_to_C": "N",
        "S_to_C": "S",
        "E_to_C": "E",
        "W_to_C": "W",
    }

    SENSOR_THRESHOLD = 150.0

    def __init__(self, tmpdir: str, level_config: Dict[str, Any]):
        import uuid
        import sumolib

        self.tmpdir = tmpdir
        self.level_config = level_config

        self._write_files()

        self.label = f"sim_{uuid.uuid4().hex}"

        try:
            self.port = sumolib.miscutils.getFreeSocketPort()

            cfg_path = os.path.join(tmpdir, "sumo.sumocfg")
            cmd = [
                "sumo",
                "-c",
                cfg_path,
                "--no-step-log",
                "true",
                "--no-warnings",
                "true",
            ]

            traci.start(cmd, port=self.port, label=self.label)
            self.traci = traci.getConnection(self.label)

        except Exception as e:
            print(f"FATAL TRACI INIT ERROR: {e}", file=sys.stderr)
            raise

        self.current_phase = "NS"
        self.last_green_phase = "NS"
        self.in_yellow = False
        self.phase_timer = 0

    def _write_files(self):
        nod_xml = """<?xml version="1.0" encoding="UTF-8"?>
<nodes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/nodes_file.xsd">
    <node id="C" x="0.0" y="0.0" type="traffic_light"/>
    <node id="N" x="0.0" y="500.0" type="priority"/>
    <node id="S" x="0.0" y="-500.0" type="priority"/>
    <node id="E" x="500.0" y="0.0" type="priority"/>
    <node id="W" x="-500.0" y="0.0" type="priority"/>
</nodes>"""
        edg_xml = """<?xml version="1.0" encoding="UTF-8"?>
<edges xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/edges_file.xsd">
    <edge id="N_to_C" from="N" to="C" priority="2" numLanes="2" speed="16.67"/>
    <edge id="C_to_N" from="C" to="N" priority="2" numLanes="2" speed="16.67"/>
    <edge id="S_to_C" from="S" to="C" priority="2" numLanes="2" speed="16.67"/>
    <edge id="C_to_S" from="C" to="S" priority="2" numLanes="2" speed="16.67"/>
    <edge id="E_to_C" from="E" to="C" priority="2" numLanes="2" speed="16.67"/>
    <edge id="C_to_E" from="C" to="E" priority="2" numLanes="2" speed="16.67"/>
    <edge id="W_to_C" from="W" to="C" priority="2" numLanes="2" speed="16.67"/>
    <edge id="C_to_W" from="C" to="W" priority="2" numLanes="2" speed="16.67"/>
</edges>"""
        det_xml = """<?xml version="1.0" encoding="UTF-8"?>
<additional xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/additional_file.xsd">
    <inductionLoop id="sensor_N" lane="N_to_C_0" pos="100" freq="1" file="NUL"/>
    <inductionLoop id="sensor_S" lane="S_to_C_0" pos="100" freq="1" file="NUL"/>
    <inductionLoop id="sensor_E" lane="E_to_C_0" pos="100" freq="1" file="NUL"/>
    <inductionLoop id="sensor_W" lane="W_to_C_0" pos="100" freq="1" file="NUL"/>
</additional>"""

        nod_path = os.path.join(self.tmpdir, "nodes.nod.xml")
        edg_path = os.path.join(self.tmpdir, "edges.edg.xml")
        net_path = os.path.join(self.tmpdir, "net.net.xml")

        with open(nod_path, "w") as f:
            f.write(nod_xml)
        with open(edg_path, "w") as f:
            f.write(edg_xml)

        netconvert = shutil.which("netconvert")
        if not netconvert:
            raise RuntimeError("netconvert binary not found in PATH")
        result = subprocess.run(
            [
                netconvert,
                "--node-files=" + nod_path,
                "--edge-files=" + edg_path,
                "--output-file=" + net_path,
                "--no-internal-links",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"netconvert failed: {result.stderr}")

        with open(os.path.join(self.tmpdir, "routes.rou.xml"), "w") as f:
            f.write(generate_rou_xml(self.level_config))
        with open(os.path.join(self.tmpdir, "det.xml"), "w") as f:
            f.write(det_xml)

        sumocfg = """<?xml version="1.0" encoding="UTF-8"?>
<configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/sumoConfiguration.xsd">
    <input>
        <net-file value="net.net.xml"/>
        <route-files value="routes.rou.xml"/>
        <additional-files value="det.xml"/>
    </input>
    <time>
        <begin value="0"/>
        <end value="3600"/>
        <step-length value="1.0"/>
    </time>
    <report>
        <no-warnings value="true"/>
        <no-step-log value="true"/>
    </report>
    <random_number>
        <random value="true"/>
        <seed value="42"/>
    </random_number>
</configuration>"""
        with open(os.path.join(self.tmpdir, "sumo.sumocfg"), "w") as f:
            f.write(sumocfg)

    def set_tls_state(self, ns_green: bool, ew_green: bool):
        try:
            default_state = self.traci.trafficlight.getRedYellowGreenState("C")
            link_count = len(default_state)
        except self.traci.TraCIException:
            return

        half = link_count // 2

        if ns_green:
            state = "G" * half + "r" * (link_count - half)
        elif ew_green:
            state = "r" * half + "G" * (link_count - half)
        else:
            state = "y" * link_count

        try:
            self.traci.trafficlight.setRedYellowGreenState("C", state)
        except self.traci.TraCIException:
            pass

    def step(self, collect_sensors: bool = False) -> List[Dict]:
        self.traci.simulationStep()

        sensor_events: List[Dict] = []
        if not collect_sensors:
            return sensor_events

        detector_map = {
            "sensor_N": "N",
            "sensor_S": "S",
            "sensor_E": "E",
            "sensor_W": "W",
        }
        for det_id, direction in detector_map.items():
            try:
                vids = self.traci.inductionloop.getLastStepVehicleIDs(det_id)
                for vid in vids:
                    speed = self.traci.vehicle.getSpeed(vid)
                    length = self.traci.vehicle.getLength(vid)
                    lane = self.traci.vehicle.getLaneID(vid)
                    sensor_events.append(
                        {
                            "id": vid,
                            "dir": direction,
                            "lane": lane.split("_")[-1] if "_" in lane else "0",
                            "speed": round(speed, 1),
                            "length": round(length, 1),
                        }
                    )
            except self.traci.TraCIException:
                pass

        return sensor_events

    def get_light_states(self) -> Dict[str, Any]:
        try:
            state = self.traci.trafficlight.getRedYellowGreenState("C")
        except self.traci.TraCIException:
            state = "GGgg"

        if state.startswith("G"):
            lights = {"N": "GREEN", "S": "GREEN", "E": "RED", "W": "RED"}
        elif state.startswith("g"):
            lights = {"N": "RED", "S": "RED", "E": "GREEN", "W": "GREEN"}
        else:
            lights = {"N": "YELLOW", "S": "YELLOW", "E": "YELLOW", "W": "YELLOW"}

        lights["countdown"] = 0
        return lights

    def _edge_last_step_waiting_time(self, edge_id: str) -> float:
        """Per-tick waiting (seconds) on this edge; avoids inflated totals."""
        e = self.traci.edge
        try:
            if hasattr(e, "getLastStepWaitingTime"):
                return float(e.getLastStepWaitingTime(edge_id))
        except Exception:
            pass
        try:
            return float(e.getWaitingTime(edge_id))
        except Exception:
            return 0.0

    def get_metrics(self) -> Dict[str, Any]:
        total_delay = 0.0
        for edge_id in ["N_to_C", "S_to_C", "E_to_C", "W_to_C"]:
            total_delay += self._edge_last_step_waiting_time(edge_id)

        max_queue = 0
        for edge_id in ["N_to_C", "S_to_C", "E_to_C", "W_to_C"]:
            try:
                max_queue += int(self.traci.edge.getLastStepHaltingNumber(edge_id))
            except (self.traci.TraCIException, TypeError, ValueError):
                pass

        try:
            throughput = int(self.traci.simulation.getArrivedNumber())
        except (self.traci.TraCIException, TypeError, ValueError):
            throughput = 0

        try:
            spawned = int(self.traci.simulation.getDepartedNumber())
        except (self.traci.TraCIException, TypeError, ValueError):
            spawned = 0

        return {
            "total_delay": float(round(total_delay, 4)),
            "max_queue_length": int(max_queue),
            "throughput": int(throughput),
            "spawned": int(spawned),
        }

    def get_replay_frame(
        self, tick: int, phase: str, in_yellow: bool, queues: Dict[str, int]
    ) -> Dict[str, Any]:
        try:
            state = self.traci.trafficlight.getRedYellowGreenState("C")
        except self.traci.TraCIException:
            state = "GGgg"

        if state.startswith("G"):
            lights = {"N": "G", "S": "G", "E": "r", "W": "r"}
        elif "G" in state:  # EW green: state = "r...G..."
            lights = {"N": "r", "S": "r", "E": "G", "W": "G"}
        else:
            lights = {"N": "y", "S": "y", "E": "y", "W": "y"}

        vehicles: List[Dict] = []
        cx0 = REPLAY_CANVAS_W * 0.5
        cy0 = REPLAY_CANVAS_H * 0.5
        try:
            jp = self.traci.junction.getPosition("C")
            jx, jy = float(jp[0]), float(jp[1])
        except Exception:
            jx, jy = 0.0, 0.0
        try:
            # getPosition() in net CRS; subtract junction C so coords match centered canvas.
            for vid in self.traci.vehicle.getIDList():
                try:
                    pos = self.traci.vehicle.getPosition(vid)
                    ang_deg = float(self.traci.vehicle.getAngle(vid))
                except self.traci.TraCIException:
                    continue
                x, y = float(pos[0]), float(pos[1])
                x -= jx
                y -= jy
                if math.hypot(x, y) > REPLAY_CLIP_RADIUS_M:
                    continue
                try:
                    color = self.traci.vehicle.getColor(vid)
                except Exception:
                    color = None
                try:
                    vtype = self.traci.vehicle.getTypeID(vid)  # "car" or "bus"
                except Exception:
                    vtype = "car"
                # SUMO angle: 0=North, clockwise. Canvas: y-down, rotate clockwise.
                # With Y-flip (py = cy0 - y*scale), angle maps directly: radians(ang_deg)
                angle_rad = float(math.radians(ang_deg))
                color_str = (
                    f"rgb({int(color[0])},{int(color[1])},{int(color[2])})"
                    if color
                    else "rgb(255,214,0)"
                )
                px = cx0 + x * REPLAY_SCALE_PX_PER_M
                py = cy0 - y * REPLAY_SCALE_PX_PER_M  # flip Y: SUMO y-up → canvas y-down
                vehicles.append(
                    {
                        "id": str(vid),
                        # px/py are canvas coords; raw x/y omitted to reduce payload size.
                        "px": round(px, 1),
                        "py": round(py, 1),
                        "angle": round(angle_rad, 3),
                        "color": color_str,
                        "vtype": vtype,
                    }
                )
        except self.traci.TraCIException:
            pass

        q = queues or {}
        return {
            "tick": int(tick),
            "lights": lights,
            "vehicles": vehicles,
            "phase": phase,
            "in_yellow": bool(in_yellow),
            "queues": {
                "N": int(q.get("N", 0)),
                "S": int(q.get("S", 0)),
                "E": int(q.get("E", 0)),
                "W": int(q.get("W", 0)),
            },
        }

    def close(self):
        try:
            self.traci.close()
        except Exception:
            pass


# ── Sandbox Launcher ─────────────────────────────────────────────────────────


def _set_resource_limits():
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (RAM_LIMIT_BYTES, RAM_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def launch_sandbox(solution_path: str) -> subprocess.Popen:
    if not os.path.isfile(solution_path):
        raise FileNotFoundError(f"Solution not found: {solution_path}")

    cmd = [sys.executable, "-u", solution_path]
    preexec = _set_resource_limits if sys.platform != "win32" else None

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(solution_path)),
            preexec_fn=preexec,
        )
        return proc
    except Exception as e:
        raise RuntimeError(f"Failed to launch sandbox: {e}")


# ── Evaluation Loop ──────────────────────────────────────────────────────────


def evaluate(solution_path: str, level: int = DEFAULT_LEVEL) -> Dict[str, Any]:
    if level not in SCENARIO_CONFIGS:
        raise ValueError(
            f"Invalid level {level}. Must be one of {sorted(SCENARIO_CONFIGS.keys())}"
        )

    level_config = SCENARIO_CONFIGS[level]

    start_time = time.time()
    engine = None
    proc = None

    try:
        tmpdir = tempfile.mkdtemp()

        try:
            engine = SumoSimulationEngine(tmpdir, level_config)
        except Exception as e:
            return {
                "status": "ENGINE_ERROR",
                "error_log": f"SUMO initialization failed: {str(e)}\n{traceback.format_exc()}",
                "score": 0,
            }

        try:
            proc = launch_sandbox(solution_path)
        except (FileNotFoundError, RuntimeError) as e:
            return {"status": "START_FAILED", "error": str(e), "score": 0}

        current_phase = PHASE_NS_GREEN
        last_green_phase = PHASE_NS_GREEN
        phase_timer = 0
        phase_duration = DEFAULT_NS_DURATION
        cumulative_delay = 0.0
        peak_queue = 0
        total_throughput = 0
        total_spawned = 0
        replay_frames: List[Dict[str, Any]] = []

        for tick in range(1, TOTAL_TICKS + 1):
            elapsed = time.time() - start_time
            if elapsed > WALL_CLOCK_LIMIT:
                proc.kill()
                return {
                    "status": "TLE",
                    "error": f"Time Limit Exceeded ({WALL_CLOCK_LIMIT}s)",
                    "ticks_completed": tick - 1,
                    "score": 0,
                }

            ns_green = current_phase == PHASE_NS_GREEN
            ew_green = current_phase == PHASE_EW_GREEN
            engine.set_tls_state(ns_green, ew_green)

            try:
                sensor_events = engine.step()
            except Exception as e:
                proc.kill()
                return {
                    "status": "ENGINE_ERROR",
                    "error_log": f"SUMO step failed at tick {tick}: {str(e)}\n{traceback.format_exc()}",
                    "ticks_completed": tick - 1,
                    "score": 0,
                }

            queues = {"N": 0, "S": 0, "E": 0, "W": 0}
            for edge, d in zip(
                ["N_to_C", "S_to_C", "E_to_C", "W_to_C"], ["N", "S", "E", "W"]
            ):
                try:
                    queues[d] = engine.traci.edge.getLastStepHaltingNumber(edge)
                except Exception:
                    pass

            if current_phase == PHASE_NS_GREEN:
                phase_str, in_yellow = "NS", False
            elif current_phase == PHASE_EW_GREEN:
                phase_str, in_yellow = "EW", False
            else:
                phase_str, in_yellow = (
                    ("NS" if last_green_phase == PHASE_NS_GREEN else "EW"),
                    True,
                )

            # Slim IPC: wrapper only needs queues + phase + timer (saves huge JSON per tick).
            payload = {
                "tick": tick,
                "queues": queues,
                "phase": phase_str,
                "phase_timer": phase_timer,
            }

            try:
                proc.stdin.write(
                    json.dumps(payload, separators=(",", ":")) + "\n"
                )
                proc.stdin.flush()
            except (BrokenPipeError, IOError, OSError):
                proc.kill()
                return {
                    "status": "RTE",
                    "error": f"Contestant crashed at tick {tick}",
                    "ticks_completed": tick - 1,
                    "score": 0,
                }

            try:
                response_line = proc.stdout.readline()
                if not response_line:
                    proc.kill()
                    return {
                        "status": "RTE",
                        "error": f"Contestant exited at tick {tick}",
                        "ticks_completed": tick - 1,
                        "score": 0,
                    }
                action = json.loads(response_line.strip())
            except json.JSONDecodeError:
                proc.kill()
                return {
                    "status": "RTE",
                    "error": f"Invalid JSON at tick {tick}",
                    "ticks_completed": tick - 1,
                    "score": 0,
                }

            cmd = action.get("action", "GIU_NGUYEN")
            if cmd == "CHUYEN_PHA":
                new_duration = action.get("duration")
                if new_duration is not None and isinstance(new_duration, (int, float)):
                    phase_duration = max(1, int(new_duration))
            elif cmd not in ("GIU_NGUYEN", "CHUYEN_PHA"):
                # Invalid action string from contestant → silently ignore, treat as GIU_NGUYEN
                pass

            phase_timer += 1
            if phase_timer >= phase_duration:
                if current_phase == PHASE_YELLOW:
                    current_phase = (
                        PHASE_EW_GREEN
                        if last_green_phase == PHASE_NS_GREEN
                        else PHASE_NS_GREEN
                    )
                    last_green_phase = current_phase
                    phase_duration = (
                        DEFAULT_EW_DURATION
                        if current_phase == PHASE_EW_GREEN
                        else DEFAULT_NS_DURATION
                    )
                else:
                    last_green_phase = current_phase
                    current_phase = PHASE_YELLOW
                    phase_duration = YELLOW_DURATION
                phase_timer = 0

            if current_phase == PHASE_NS_GREEN:
                rphase, ryellow = "NS", False
            elif current_phase == PHASE_EW_GREEN:
                rphase, ryellow = "EW", False
            else:
                rphase = "NS" if last_green_phase == PHASE_NS_GREEN else "EW"
                ryellow = True
            if tick % REPLAY_TICK_STRIDE == 0 or tick == TOTAL_TICKS:
                replay_frames.append(
                    engine.get_replay_frame(tick, rphase, ryellow, queues)
                )

            metrics = engine.get_metrics()
            cumulative_delay += metrics["total_delay"]
            peak_queue = max(peak_queue, metrics["max_queue_length"])
            total_throughput += metrics["throughput"]
            total_spawned += metrics["spawned"]

    except Exception as e:
        return {
            "status": "INTERNAL_ERROR",
            "error_log": f"Unexpected error: {str(e)}\n{traceback.format_exc()}",
            "score": 0,
        }

    finally:
        if engine:
            engine.close()
        if proc:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    # ── Multi-metric Scoring ─────────────────────────────────────────────────────

    # 1. Delay score (60%) — average delay per vehicle that completed the trip.
    #    ≤10 s  → ~93 pts (excellent),  ≥95 s → ~37 pts,  ≥150 s → 0 pts.
    avg_delay_per_vehicle = cumulative_delay / max(total_throughput, 1)
    delay_score = max(0.0, min(100.0, 100.0 - (avg_delay_per_vehicle / 1.5)))

    # 2. Throughput score (30%) — fraction of spawned vehicles that cleared the junction.
    throughput_score = min(100.0, (total_throughput / max(total_spawned, 1)) * 100.0)

    # 3. Queue score (10%) — penalise starvation / long jams.
    #    Peak queue of 0 → 100 pts,  ≥50 → 0 pts.
    queue_score = max(0.0, min(100.0, 100.0 - (peak_queue * 2.0)))

    score = round(
        (0.6 * delay_score) + (0.3 * throughput_score) + (0.1 * queue_score), 2
    )

    return {
        "level": level,
        "level_label": level_config["label"],
        "spawn_rate": level_config["spawn_rate"],
        "bus_ratio": level_config["bus_ratio"],
        "status": "OK",
        "ticks_completed": TOTAL_TICKS,
        "total_delay": float(round(cumulative_delay, 2)),
        "avg_delay_per_vehicle": float(round(avg_delay_per_vehicle, 2)),
        "max_queue_length": int(peak_queue),
        "throughput": int(total_throughput),
        "total_spawned": int(total_spawned),
        # Sub-scores for UI breakdown
        "delay_score":      float(round(delay_score, 1)),
        "throughput_score": float(round(throughput_score, 1)),
        "queue_score":      float(round(queue_score, 1)),
        "score": float(score),
        "replay_data": replay_frames,
    }


# ── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Traffic Light Control Online Judge — SUMO Evaluator"
    )
    parser.add_argument(
        "solution",
        nargs="?",
        default="solution.py",
        help="Path to contestant's solution script",
    )
    parser.add_argument(
        "--level",
        type=int,
        default=DEFAULT_LEVEL,
        choices=sorted(SCENARIO_CONFIGS.keys()),
        help="Difficulty level 1-5",
    )
    args = parser.parse_args()

    result = evaluate(args.solution, level=args.level)
    print(json.dumps(result, separators=(",", ":")))
