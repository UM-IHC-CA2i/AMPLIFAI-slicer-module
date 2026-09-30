import json
import os

from data.config import get_config_value
from state.case_state import CaseState


class CaseRegistry:
    def __init__(self, base_dir=None):
        default_dir = os.path.expanduser("~/.amlifai/v2")
        base_dir = base_dir or get_config_value("v2_state_dir") or default_dir
        self.base_dir = os.path.abspath(os.path.expanduser(str(base_dir)))
        self.cases_dir = os.path.join(self.base_dir, "cases")
        os.makedirs(self.cases_dir, exist_ok=True)

    def _case_path(self, case_id):
        safe = str(case_id).strip() or "unknown"
        return os.path.join(self.cases_dir, f"{safe}.json")

    def list_case_ids(self):
        if not os.path.isdir(self.cases_dir):
            return []
        ids = []
        for name in os.listdir(self.cases_dir):
            if name.endswith(".json"):
                ids.append(name[:-5])
        return sorted(ids)

    def get(self, case_id):
        path = self._case_path(case_id)
        if not os.path.exists(path):
            return CaseState(case_id=str(case_id))
        try:
            with open(path, "r") as handle:
                data = json.load(handle)
            return CaseState.from_dict(data)
        except Exception:
            return CaseState(case_id=str(case_id))

    def save(self, state):
        if not isinstance(state, CaseState):
            raise ValueError("state must be a CaseState instance.")
        path = self._case_path(state.case_id)
        with open(path, "w") as handle:
            json.dump(state.to_dict(), handle, indent=2, sort_keys=True)

