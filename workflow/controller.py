from dataclasses import dataclass, field
from typing import Callable, Optional

from workflow.steps import (
    WORKFLOW_STEPS,
    STEP_APHE,
    STEP_CAPSULE,
    STEP_CAPSULE_DELAYED,
    STEP_CAPSULE_VENOUS,
    STEP_FINISH,
    STEP_LESION,
    STEP_LIVER,
    STEP_TIV,
    STEP_WASHOUT,
    STEP_WASHOUT_DELAYED,
    STEP_WASHOUT_VENOUS,
)


@dataclass
class WorkflowContext:
    annotations: dict = field(default_factory=dict)
    selections: dict = field(default_factory=dict)
    seg_has_voxels: Callable[[str, Optional[int]], bool] = field(
        default_factory=lambda: (lambda _feature, _phase=None: False)
    )
    seg_has_saved: Callable[[str, Optional[int]], bool] = field(
        default_factory=lambda: (lambda _feature, _phase=None: False)
    )


class WorkflowController:
    def __init__(self, context: WorkflowContext):
        self.context = context

    def _annotation(self, key):
        return self.context.annotations.get(key)

    def _selected(self, key):
        return bool(self.context.selections.get(key))

    def _aphe_present(self):
        value = self._annotation("aphe")
        return bool(value) and value != "Absent"

    def _washout_present(self):
        return self._annotation("washout") == "Present"

    def _capsule_present(self):
        return self._annotation("capsule") == "Present"

    def is_step_applicable(self, step_idx):
        if step_idx == STEP_WASHOUT_VENOUS:
            return self._washout_present() and self._selected("washout_venous")
        if step_idx == STEP_WASHOUT_DELAYED:
            return self._washout_present() and self._selected("washout_delayed")
        if step_idx == STEP_CAPSULE_VENOUS:
            return self._capsule_present() and self._selected("capsule_venous")
        if step_idx == STEP_CAPSULE_DELAYED:
            return self._capsule_present() and self._selected("capsule_delayed")
        return True

    def requires_segmentation(self, step_idx):
        if step_idx == STEP_LESION:
            return True
        if step_idx == STEP_APHE:
            return self._aphe_present()
        if step_idx == STEP_WASHOUT_VENOUS:
            return self._washout_present() and self._selected("washout_venous")
        if step_idx == STEP_WASHOUT_DELAYED:
            return self._washout_present() and self._selected("washout_delayed")
        if step_idx == STEP_CAPSULE_VENOUS:
            return self._capsule_present() and self._selected("capsule_venous")
        if step_idx == STEP_CAPSULE_DELAYED:
            return self._capsule_present() and self._selected("capsule_delayed")
        return False

    def workflow_step_complete(self, step_idx):
        if step_idx == STEP_LESION:
            return self.context.seg_has_voxels("lesion", None)
        if step_idx == STEP_LIVER:
            segment_value = self._annotation("liver_segment")
            diameter = self._annotation("max_diameter_mm")
            return bool(segment_value) and float(diameter or 0.0) > 0.0
        if step_idx == STEP_TIV:
            tumor_in_vein = self._annotation("tumor_in_vein")
            if not tumor_in_vein:
                return False
            if tumor_in_vein == "Present" and not self._annotation("vessel"):
                return False
            return True
        if step_idx == STEP_APHE:
            aphe = self._annotation("aphe")
            if not aphe:
                return False
            if aphe != "Absent":
                return self.context.seg_has_voxels("aphe", None)
            return True
        if step_idx == STEP_WASHOUT:
            washout = self._annotation("washout")
            if not washout:
                return False
            if washout == "Present":
                return self._selected("washout_venous") or self._selected("washout_delayed")
            return True
        if step_idx == STEP_WASHOUT_VENOUS:
            if not (self._washout_present() and self._selected("washout_venous")):
                return True
            return (
                self.context.seg_has_voxels("washout", 1)
                and self.context.seg_has_saved("washout", 1)
            )
        if step_idx == STEP_WASHOUT_DELAYED:
            if not (self._washout_present() and self._selected("washout_delayed")):
                return True
            return (
                self.context.seg_has_voxels("washout", 2)
                and self.context.seg_has_saved("washout", 2)
            )
        if step_idx == STEP_CAPSULE:
            capsule = self._annotation("capsule")
            if not capsule:
                return False
            if capsule == "Present":
                return self._selected("capsule_venous") or self._selected("capsule_delayed")
            return True
        if step_idx == STEP_CAPSULE_VENOUS:
            if not (self._capsule_present() and self._selected("capsule_venous")):
                return True
            return (
                self.context.seg_has_voxels("capsule", 1)
                and self.context.seg_has_saved("capsule", 1)
            )
        if step_idx == STEP_CAPSULE_DELAYED:
            if not (self._capsule_present() and self._selected("capsule_delayed")):
                return True
            return (
                self.context.seg_has_voxels("capsule", 2)
                and self.context.seg_has_saved("capsule", 2)
            )
        if step_idx == STEP_FINISH:
            return bool(self._annotation("lirads"))
        return False

    def next_applicable_step(self, start_idx):
        idx = start_idx + 1
        while idx < len(WORKFLOW_STEPS):
            if self.is_step_applicable(idx):
                return idx
            idx += 1
        return None

    def prev_applicable_step(self, start_idx):
        idx = start_idx - 1
        while idx >= 0:
            if self.is_step_applicable(idx):
                return idx
            idx -= 1
        return None
