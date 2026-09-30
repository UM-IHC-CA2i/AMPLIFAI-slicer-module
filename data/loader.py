import csv
import glob
import os

import slicer
import slicer.util as su

from data.config import get_config_value, resolve_config_path
from data.layout import (
    PHASE_VIEW_TAGS,
    apply_compare_layout,
    apply_layout_for_volume,
    clear_backgrounds,
    disable_linking,
    disable_propagation,
    fit_views,
    set_background,
    set_phase_grid_layout,
)

LOADER_ATTR = "AMLIFAI_V2_LOADER"
LOADER_PHASE_ATTR = "AMLIFAI_V2_PHASE"
DEFAULT_WINDOW = 400
DEFAULT_LEVEL = 40

PHASE_NAMES = {
    3: "Dry",
    0: "Arterial",
    1: "Venous",
    2: "Delayed",
}


def _normalize_case_id(case_id):
    if case_id is None:
        return None
    case_str = str(case_id).strip()
    if case_str.upper().startswith("CASE"):
        return case_str.upper()
    if case_str.isdigit():
        return f"CASE{case_str.zfill(5)}"
    return case_str


def resolve_case_dir(case_id, root, label, required=True):
    if not root:
        if required:
            raise FileNotFoundError(f"{label} root not set.")
        return None
    if not os.path.isdir(root):
        if required:
            raise FileNotFoundError(f"{label} root '{root}' is missing or not a directory.")
        return None
    norm_case = _normalize_case_id(case_id)
    case_dir = os.path.join(root, str(norm_case))
    if not os.path.isdir(case_dir):
        if required:
            raise FileNotFoundError(f"{label} case folder not found: {case_dir}")
        return None
    return case_dir


def find_scan_file(case_dir, case_id, phase):
    norm_case = _normalize_case_id(case_id) or str(case_id)
    candidates = [
        os.path.join(case_dir, f"{norm_case}_{phase}.nii.gz"),
        os.path.join(case_dir, f"{norm_case}_{phase}.nii"),
        os.path.join(case_dir, f"{norm_case}_{phase}_ct.nii.gz"),
        os.path.join(case_dir, f"{norm_case}_{phase}_ct.nii"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"No scan found for case {case_id}, phase {phase} in {case_dir}")


def list_available_phases(case_id, scan_root):
    case_dir = resolve_case_dir(case_id, scan_root, "SCAN_ROOT")
    available = {}
    for phase in PHASE_NAMES:
        try:
            path = find_scan_file(case_dir, case_id, phase)
            available[phase] = path
        except FileNotFoundError:
            continue
    return available


def discover_cases(scan_root):
    root = scan_root
    if not root or not os.path.isdir(root):
        raise FileNotFoundError(f"SCAN_ROOT '{root}' is missing or not a directory.")
    cases = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        if not name.upper().startswith("CASE"):
            continue
        has_scan = bool(
            glob.glob(os.path.join(path, f"{name}_*.nii*"))
            or glob.glob(os.path.join(path, f"{name}_*_ct.nii*"))
        )
        if has_scan:
            cases.append(name.upper())
    return sorted(cases)


def load_completed_case_ids():
    path = resolve_config_path(get_config_value("labels_csv"))
    completed = set()
    if not path:
        return completed
    try:
        with open(path, newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                case_id = (row.get("case_id") or "").strip()
                if case_id:
                    normalized = _normalize_case_id(case_id)
                    if normalized:
                        completed.add(normalized)
    except Exception:
        return completed
    return completed


def list_unseen_case_ids(driver, registry):
    all_cases = driver.list_cases()
    seen_cases = {
        _normalize_case_id(case_id) or str(case_id).strip().upper()
        for case_id in registry.list_case_ids()
    }
    completed = load_completed_case_ids()
    return [
        case_id
        for case_id in all_cases
        if case_id not in completed and case_id not in seen_cases
    ]


def _apply_window_level(volume_nodes, window_level=None):
    window = None
    level = None
    if isinstance(window_level, dict):
        try:
            window = float(window_level.get("window"))
            level = float(window_level.get("level"))
        except (TypeError, ValueError):
            window = None
            level = None
    for vol in volume_nodes:
        if not vol:
            continue
        if not vol.GetDisplayNode():
            vol.CreateDefaultDisplayNodes()
        disp = vol.GetDisplayNode()
        if disp:
            disp.AutoWindowLevelOff()
            if window is not None and level is not None:
                disp.SetWindowLevel(window, level)
            else:
                disp.SetWindowLevel(DEFAULT_WINDOW, DEFAULT_LEVEL)


def _volume_node_name(case_id, phase):
    phase_name = PHASE_NAMES.get(int(phase), str(phase))
    return f"{_normalize_case_id(case_id)}_{phase_name}_scan"


def _phase_from_volume_node(node, case_id):
    if not node:
        return None
    try:
        phase_attr = node.GetAttribute(LOADER_PHASE_ATTR)
        if phase_attr:
            return int(phase_attr)
    except (TypeError, ValueError):
        pass
    name = ""
    try:
        name = node.GetName() or ""
    except Exception:
        name = ""
    prefix = f"{_normalize_case_id(case_id)}_"
    if not name.startswith(prefix):
        return None
    for phase, phase_name in PHASE_NAMES.items():
        if name == _volume_node_name(case_id, phase):
            return int(phase)
    return None


class DriverState:
    def __init__(self, scan_root=None, mask_root=None, phase_order=None):
        default_scan_root = resolve_config_path(get_config_value("scan_root"))
        default_mask_root = resolve_config_path(get_config_value("mask_root"))
        self.scan_root = scan_root or default_scan_root
        self.mask_root = mask_root or default_mask_root or self.scan_root
        self.phase_order = tuple(phase_order or (0, 1, 2, 3))
        self.cases = []
        self.case_idx = -1
        self.current_case = None
        self.current_phase = None
        self.current_volume = None
        self.loaded_volumes = {}
        self.view_mode = "grid"
        self.view_case_id = None
        self.window_level = None

    def list_cases(self):
        if not self.cases:
            self.cases = discover_cases(self.scan_root)
        return list(self.cases)

    def available_phases(self, case_id):
        return list_available_phases(case_id, self.scan_root)

    def volume_for_phase(self, phase_id):
        if phase_id is None:
            return self.current_volume
        return self.loaded_volumes.get(int(phase_id))

    def _choose_first_available_phase(self, case_id):
        available = self.available_phases(case_id)
        for phase in self.phase_order:
            if phase in available:
                return phase
        return None

    def clear_case_volumes(self, case_id):
        case_id = _normalize_case_id(case_id)
        if not case_id:
            return
        for node in su.getNodesByClass("vtkMRMLScalarVolumeNode"):
            if not node or not node.GetAttribute(LOADER_ATTR):
                continue
            if _phase_from_volume_node(node, case_id) is None:
                continue
            slicer.mrmlScene.RemoveNode(node)
        if self.current_case == case_id:
            self.loaded_volumes = {}
            self.current_volume = None
            self.current_phase = None
            self.view_case_id = None

    def _load_phase_volume(self, case_id, phase, case_dir=None):
        phase = int(phase)
        case_id = _normalize_case_id(case_id)
        case_dir = case_dir or resolve_case_dir(case_id, self.scan_root, "SCAN_ROOT")
        scan_path = find_scan_file(case_dir, case_id, phase)
        vol = su.loadVolume(scan_path)
        if not vol:
            return None
        vol.SetAttribute(LOADER_ATTR, "1")
        vol.SetAttribute(LOADER_PHASE_ATTR, str(phase))
        vol.SetName(_volume_node_name(case_id, phase))
        _apply_window_level([vol], self.window_level)
        return vol

    def _collect_case_volumes_from_scene(self, case_id):
        case_id = _normalize_case_id(case_id)
        loaded = {}
        for node in su.getNodesByClass("vtkMRMLScalarVolumeNode"):
            if not node or not node.GetAttribute(LOADER_ATTR):
                continue
            phase = _phase_from_volume_node(node, case_id)
            if phase is None:
                continue
            if not node.GetAttribute(LOADER_PHASE_ATTR):
                node.SetAttribute(LOADER_PHASE_ATTR, str(phase))
            loaded[int(phase)] = node
        return loaded

    def ensure_case_volumes(self, case_id):
        if case_id is None:
            raise ValueError("case_id is required.")
        case_id = _normalize_case_id(case_id)
        if self.current_case and self.current_case != case_id:
            self.clear_case_volumes(self.current_case)
            self.view_mode = None
            self.view_case_id = None

        loaded = self._collect_case_volumes_from_scene(case_id)
        case_dir = resolve_case_dir(case_id, self.scan_root, "SCAN_ROOT")
        for phase in (0, 1, 2, 3):
            if phase in loaded and loaded[phase]:
                continue
            try:
                vol = self._load_phase_volume(case_id, phase, case_dir=case_dir)
            except FileNotFoundError:
                continue
            if vol:
                loaded[phase] = vol

        if not loaded:
            raise FileNotFoundError(f"No phases found for case {case_id} in {case_dir}")

        self.current_case = case_id
        self.loaded_volumes = dict(loaded)
        if self.cases and case_id in self.cases:
            self.case_idx = self.cases.index(case_id)
        return loaded

    def show_grid_view(self, case_id=None):
        case_id = _normalize_case_id(case_id or self.current_case)
        self.ensure_case_volumes(case_id)
        try:
            set_phase_grid_layout()
        except Exception:
            pass
        all_tags = []
        for tags in PHASE_VIEW_TAGS.values():
            all_tags.extend(tags)
        clear_backgrounds(all_tags)
        disable_linking(all_tags)
        disable_propagation(all_tags)

        tags_with_vol = []
        for phase, tags in PHASE_VIEW_TAGS.items():
            vol = self.loaded_volumes.get(phase)
            if vol and tags:
                set_background(vol, list(tags))
                tags_with_vol.extend(tags)
        if tags_with_vol:
            fit_views(tags_with_vol)

        preferred_phase = 0 if 0 in self.loaded_volumes else next(iter(self.loaded_volumes))
        self.current_phase = preferred_phase
        self.current_volume = self.loaded_volumes.get(preferred_phase)
        self.view_mode = "grid"
        self.view_case_id = case_id
        return self.loaded_volumes

    def show_single_phase(self, phase_id, case_id=None):
        case_id = _normalize_case_id(case_id or self.current_case)
        phase_id = int(phase_id)
        self.ensure_case_volumes(case_id)
        vol = self.loaded_volumes.get(phase_id)
        if not vol:
            raise FileNotFoundError(
                f"No scan found for case {case_id}, phase {phase_id} in cache."
            )
        try:
            apply_layout_for_volume(vol)
        except Exception:
            pass
        self.current_phase = phase_id
        self.current_volume = vol
        self.view_mode = "single"
        self.view_case_id = case_id
        return vol

    def show_compare_view(self, focus_phase, reference_phase=0, case_id=None):
        case_id = _normalize_case_id(case_id or self.current_case)
        focus_phase = int(focus_phase)
        reference_phase = int(reference_phase)
        self.ensure_case_volumes(case_id)
        focus_vol = self.loaded_volumes.get(focus_phase)
        ref_vol = self.loaded_volumes.get(reference_phase)
        if not focus_vol:
            raise FileNotFoundError(
                f"No scan found for case {case_id}, phase {focus_phase} in cache."
            )
        apply_compare_layout(ref_vol, focus_vol)
        self.current_phase = focus_phase
        self.current_volume = focus_vol
        self.view_mode = "compare"
        self.view_case_id = case_id
        return self.loaded_volumes

    def load_case(self, case_id, phase=None):
        if case_id is None:
            raise ValueError("case_id is required.")
        if phase is None:
            phase = self._choose_first_available_phase(case_id)
            if phase is None:
                raise FileNotFoundError(f"No phases found for case {case_id} in {self.scan_root}")
        return self.show_single_phase(phase, case_id=case_id)

    def load_all_phases(self, case_id):
        if case_id is None:
            raise ValueError("case_id is required.")
        return self.show_grid_view(case_id=case_id)

    def load_phase_comparison(self, case_id, focus_phase, reference_phase=0):
        if case_id is None:
            raise ValueError("case_id is required.")
        return self.show_compare_view(
            focus_phase, reference_phase=reference_phase, case_id=case_id
        )

    def next_case(self, case_ids=None):
        return self._step_case(1, case_ids)

    def prev_case(self, case_ids=None):
        return self._step_case(-1, case_ids)

    def _step_case(self, direction, case_ids=None):
        subset = case_ids is not None
        cases = list(case_ids) if subset else self.list_cases()
        if not cases:
            if subset:
                raise FileNotFoundError("No unseen cases to load.")
            raise FileNotFoundError("No cases discovered to load.")

        if subset:
            current = _normalize_case_id(self.current_case)
            if current and current in cases:
                start_idx = cases.index(current)
            else:
                start_idx = -1 if direction > 0 else 0
        elif 0 <= self.case_idx < len(cases):
            start_idx = self.case_idx
        else:
            start_idx = -1 if direction > 0 else 0

        for step in range(1, len(cases) + 1):
            idx = (start_idx + direction * step) % len(cases)
            case_id = cases[idx]
            try:
                if self.view_mode == "grid":
                    return self.show_grid_view(case_id=case_id)
                phase = self._choose_first_available_phase(case_id)
                if phase is not None:
                    return self.show_single_phase(phase, case_id=case_id)
            except FileNotFoundError:
                continue
        if subset:
            raise FileNotFoundError("No loadable unseen cases found.")
        raise FileNotFoundError("No phases available in any case.")
