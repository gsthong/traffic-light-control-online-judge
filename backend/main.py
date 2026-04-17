import asyncio
import json
import os
import sys
import tempfile
import traceback
import uuid
from typing import Dict, Any
from fastapi import FastAPI
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

_evaluator_semaphore = asyncio.Semaphore(5)


class Submission(BaseModel):
    code: str
    username: str = "anonymous"


EVALUATOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "evaluator.py"
)


async def _run_level(level: int, code: str) -> Dict[str, Any]:
    wrapped_code = f"""import sys
import json
import traceback

# ======== CONTESTANT CODE START ========
{code}
# ======== CONTESTANT CODE END ========

def _run_sandbox():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        
        try:
            current_phase = data.get("phase", "NS")
            phase_timer = data.get("phase_timer", 0)
            queues = data.get("queues", {{}})
            
            res = control(queues, current_phase, phase_timer)
            
            if res == 'yellow' and current_phase != 'yellow':
                out = {{"action": "CHUYEN_PHA", "duration": 3}}
            elif res == 'NS' and current_phase != 'NS':
                out = {{"action": "CHUYEN_PHA", "duration": 30}}
            elif res == 'EW' and current_phase != 'EW':
                out = {{"action": "CHUYEN_PHA", "duration": 20}}
            else:
                out = {{"action": "GIU_NGUYEN"}}
        except Exception as e:
            out = {{"action": "GIU_NGUYEN", "error": str(e)}}
        
        sys.stdout.write(json.dumps(out) + "\\n")
        sys.stdout.flush()

if __name__ == "__main__":
    _run_sandbox()
"""

    unique_id = str(uuid.uuid4())
    temp_path = os.path.join(tempfile.gettempdir(), f"temp_solution_{unique_id}.py")

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(wrapped_code)

        async with _evaluator_semaphore:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                EVALUATOR_PATH,
                temp_path,
                "--level",
                str(level),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=1200.0
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "level": level,
                    "score": 0.0,
                    "status": "TLE",
                    "error": "Evaluation timeout (1200s)",
                    "error_log": "asyncio.wait_for exceeded 1200.0s",
                }

        if proc.returncode != 0:
            raw = stderr.decode("utf-8", errors="replace").strip()
            err = raw[:500] if len(raw) > 500 else raw
            return {
                "level": level,
                "score": 0.0,
                "status": "ERROR",
                "error": err or f"Evaluator exited with code {proc.returncode}",
                "error_log": raw,
            }

        output = stdout.decode("utf-8", errors="replace").strip()
        if not output:
            return {
                "level": level,
                "score": 0.0,
                "status": "ERROR",
                "error": "No output from evaluator",
                "error_log": "(empty stdout)",
            }

        try:
            result = json.loads(output.strip().split("\n")[-1])
        except json.JSONDecodeError as e:
            return {
                "level": level,
                "score": 0.0,
                "status": "ERROR",
                "error": f"Invalid JSON: {str(e)}",
                "error_log": output[:2000] if len(output) > 2000 else output,
            }

        out: Dict[str, Any] = {"level": level}
        for key in (
            "status",
            "score",
            "total_delay",
            "max_queue_length",
            "throughput",
            "error",
            "error_log",
            "replay_data",
            "ticks_completed",
            "level_label",
            "spawn_rate",
            "bus_ratio",
        ):
            if key not in result:
                continue
            val = result[key]
            if key == "score":
                out[key] = float(val) if val is not None else 0.0
            elif key in ("total_delay", "spawn_rate", "bus_ratio"):
                out[key] = float(val) if val is not None else 0.0
            elif key in ("max_queue_length", "throughput", "ticks_completed"):
                out[key] = int(val) if val is not None else 0
            else:
                out[key] = val

        if "status" not in out:
            out["status"] = "OK"
        if "score" not in out:
            out["score"] = 0.0
        if "total_delay" not in out:
            out["total_delay"] = 0.0
        if "max_queue_length" not in out:
            out["max_queue_length"] = 0
        if "throughput" not in out:
            out["throughput"] = 0

        return out

    except Exception as e:
        return {
            "level": level,
            "score": 0.0,
            "status": "ERROR",
            "error": str(e),
            "error_log": traceback.format_exc(),
        }
    finally:
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except OSError:
            pass


@app.post("/evaluate")
async def evaluate_submission(submission: Submission):
    if not submission.code.strip():
        return JSONResponse(status_code=400, content={"error": "No code provided"})
    if "def control" not in submission.code:
        return JSONResponse(
            status_code=400, content={"error": "Code must define a 'control' function"}
        )

    coros = [_run_level(level, submission.code) for level in range(1, 6)]
    details = await asyncio.gather(*coros)

    scores = [d["score"] for d in details]
    final_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    return {"final_score": final_score, "details": details}


@app.get("/health")
async def health():
    try:
        import traci

        return {"status": "ok", "engine": "sumo"}
    except ImportError:
        return {"status": "error", "engine": "none", "error": "SUMO not installed"}


if __name__ == "__main__":
    import uvicorn

    print("Starting backend with SUMO TraCI engine...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
