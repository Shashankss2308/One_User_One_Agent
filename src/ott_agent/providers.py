"""Model providers. Model output is an untrusted suggestion, never evidence."""
import json


class ModelUnavailable(Exception):
    pass


class MockProvider:
    """Deterministic. Replays replay/model_faults.jsonl; no API key needed."""
    def __init__(self, faults_path=None):
        self.faults, self.down, self.calls = {}, False, 0
        if faults_path:
            for line in open(faults_path, encoding="utf-8"):
                if line.strip():
                    f = json.loads(line)
                    self.faults[f["trigger_event_id"]] = f

    def advise(self, trigger_id, eligible_ids, rules_choice):
        self.calls += 1
        if self.down:
            raise ModelUnavailable("HTTP 503")
        f = self.faults.get(trigger_id)
        if f:
            if f.get("fault") == "timeout" or "model_output" not in f:
                raise TimeoutError("model timed out")
            return f["model_output"]
        return {"goal_id": rules_choice, "eligible": True, "confidence": 0.7,
                "reason": "highest-priority eligible goal"}