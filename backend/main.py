import asyncio
import json
import os
import tempfile
from typing import Dict, Any
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

# ── Global concurrency limiter ───────────────────────────────────────────────

_evaluator_semaphore = asyncio.Semaphore(10)

# ── Models ────────────────────────────────────────────────────────────────────


class Submission(BaseModel):
    code: str
    username: str = "anonymous"


# ── Helpers ───────────────────────────────────────────────────────────────────

EVALUATOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "evaluator.py"
)


async def _run_level(level: int, code: str) -> Dict[str, Any]:
    """Spawn evaluator.py for a single level via async subprocess."""
    
    wrapped_code = f"""import sys
import json
import traceback

# ======== CONTESTANT CODE START ========
{code}
# ======== CONTESTANT CODE END ========

def _run_sandbox():
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
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
            
        print(json.dumps(out))
        sys.stdout.flush()

if __name__ == "__main__":
    _run_sandbox()
"""

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        delete=False,
        dir=tempfile.gettempdir(),
        encoding="utf-8",
    ) as f:
        f.write(wrapped_code)
        temp_path = f.name

    try:
        async with _evaluator_semaphore:
            proc = await asyncio.create_subprocess_exec(
                "python",
                EVALUATOR_PATH,
                temp_path,
                "--level",
                str(level),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()[:500]
            return {
                "level": level,
                "score": 0.0,
                "status": "ERROR",
                "error": err,
            }

        output = stdout.decode("utf-8", errors="replace").strip()
        lines = output.splitlines()
        last_json = None
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{"):
                try:
                    last_json = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        if last_json is None:
            return {
                "level": level,
                "score": 0.0,
                "status": "ERROR",
                "error": "No JSON output from evaluator",
            }

        return {
            "level": level,
            "score": last_json.get("score", 0.0),
            "status": last_json.get("status", "OK"),
        }

    except Exception as e:
        return {
            "level": level,
            "score": 0.0,
            "status": "ERROR",
            "error": str(e),
        }
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


# ── API Endpoints ────────────────────────────────────────────────────────────


@app.post("/evaluate")
async def evaluate_submission(submission: Submission):
    if not submission.code.strip():
        return JSONResponse(status_code=400, content={"error": "No code provided"})
    if "def control" not in submission.code:
        return JSONResponse(
            status_code=400,
            content={"error": "Code must define a 'control' function"},
        )

    coros = [_run_level(level, submission.code) for level in range(1, 6)]
    details = await asyncio.gather(*coros)

    scores = [d["score"] for d in details]
    final_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    return {
        "final_score": final_score,
        "details": list(details),
    }


@app.get("/health")
async def health():
    try:
        import traci  # noqa: F401

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
        import traci  # noqa: F401

        engine_name = "SUMO"
    except ImportError:
        engine_name = "pure Python"
    print(f"Starting backend with {engine_name} engine...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
