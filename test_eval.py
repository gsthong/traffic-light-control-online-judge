import requests

code = """def control(queues, current_phase, phase_timer):
    if current_phase == 'NS':
        if phase_timer >= 30:
            return 'yellow'
    elif current_phase == 'EW':
        if phase_timer >= 20:
            return 'yellow'
    return current_phase
"""

data = {"code": code, "username": "tester"}
r = requests.post("http://localhost:8000/evaluate", json=data)
print(r.json())
