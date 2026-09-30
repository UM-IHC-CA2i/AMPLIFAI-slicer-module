import time
from dataclasses import dataclass, field


@dataclass
class CaseState:
    case_id: str
    current_step: int = 0
    flagged: bool = False
    notes: str = ""
    annotations: dict = field(default_factory=dict)
    selections: dict = field(default_factory=dict)
    completed_steps: list = field(default_factory=list)
    segmentation_meta: dict = field(default_factory=dict)
    window_level: dict = field(default_factory=dict)
    last_opened: float = 0.0
    reannotation: bool = False

    def touch(self):
        self.last_opened = time.time()

    def to_dict(self):
        return {
            "case_id": self.case_id,
            "current_step": int(self.current_step),
            "flagged": bool(self.flagged),
            "notes": self.notes,
            "annotations": dict(self.annotations or {}),
            "selections": dict(self.selections or {}),
            "completed_steps": list(self.completed_steps),
            "segmentation_meta": dict(self.segmentation_meta),
            "window_level": dict(self.window_level),
            "last_opened": float(self.last_opened),
            "reannotation": bool(self.reannotation),
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("CaseState data must be a dict.")
        flagged = bool(data.get("flagged", False))
        if not flagged:
            legacy_flags = data.get("flags") or []
            flagged = bool(legacy_flags)
        return cls(
            case_id=str(data.get("case_id") or ""),
            current_step=int(data.get("current_step") or 0),
            flagged=flagged,
            notes=str(data.get("notes") or ""),
            annotations=dict(data.get("annotations") or {}),
            selections=dict(data.get("selections") or {}),
            completed_steps=list(data.get("completed_steps") or []),
            segmentation_meta=dict(data.get("segmentation_meta") or {}),
            window_level=dict(data.get("window_level") or {}),
            last_opened=float(data.get("last_opened") or 0.0),
            reannotation=bool(data.get("reannotation", False)),
        )
