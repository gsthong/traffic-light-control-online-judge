import sys
import json
import traceback

# ======== CONTESTANT CODE START ========
def control(queues, current_phase, phase_timer):
    if current_phase == 'NS':
        if phase_timer >= 30:
            return 'yellow'
    elif current_phase == 'EW':
        if phase_timer >= 20:
            return 'yellow'
    return current_phase
# ======== CONTESTANT CODE END ========

def _run_sandbox():
    while True:
        line = sys.stdin.readline()
        if not line: break
        line = line.strip()
        if not line: continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
            
        try:
            current_phase = data.get("phase", "NS")
            phase_timer = data.get("phase_timer", 0)
            queues = data.get("queues", {})
            
            res = control(queues, current_phase, phase_timer)
            
            if res == 'yellow' and current_phase != 'yellow':
                out = {"action": "CHUYEN_PHA", "duration": 3}
            elif res == 'NS' and current_phase != 'NS':
                out = {"action": "CHUYEN_PHA", "duration": 30}
            elif res == 'EW' and current_phase != 'EW':
                out = {"action": "CHUYEN_PHA", "duration": 20}
            else:
                out = {"action": "GIU_NGUYEN"}
        except Exception as e:
            out = {"action": "GIU_NGUYEN", "error": str(e)}
            
        print(json.dumps(out))
        sys.stdout.flush()

if __name__ == "__main__":
    _run_sandbox()
