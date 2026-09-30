import glob
import os

import slicer
import vtk

from data.config import get_config_value, resolve_config_path
from data.loader import PHASE_NAMES

FEATURE_SEGMENT_NAMES = {
    "lesion": "Lesion",
    "aphe": "APHE",
    "washout": "Washout",
    "capsule": "Capsule",
}

FEATURE_COLORS = {
    "lesion": (0.9, 0.2, 0.2),
    "aphe": (0.2, 0.7, 0.2),
    "washout": (0.2, 0.4, 0.9),
    "washout_venous": (0.95, 0.55, 0.1),
    "washout_delayed": (0.15, 0.75, 0.85),
    "capsule_venous": (0.55, 0.2, 0.8),
    "capsule_delayed": (0.2, 0.55, 0.95),
    "capsule": (0.9, 0.7, 0.2),
}


def tag_segmentation(seg_node, case_id=None, feature=None, phase_id=None):
    if not seg_node:
        return
    if feature == "lesion":
        phase_id = None
    if case_id:
        try:
            seg_node.SetAttribute("AMLIFAI_CASE_ID", str(case_id))
        except Exception:
            pass
    if feature:
        try:
            seg_node.SetAttribute("AMLIFAI_FEATURE", str(feature))
        except Exception:
            pass
    if phase_id is not None:
        try:
            seg_node.SetAttribute("AMLIFAI_PHASE_ID", str(phase_id))
        except Exception:
            pass
    elif feature == "lesion":
        try:
            seg_node.RemoveAttribute("AMLIFAI_PHASE_ID")
        except Exception:
            pass


def _feature_color_key(feature, phase_id=None):
    if feature == "washout":
        if phase_id == 1:
            return "washout_venous"
        if phase_id == 2:
            return "washout_delayed"
    if feature == "capsule":
        if phase_id == 1:
            return "capsule_venous"
        if phase_id == 2:
            return "capsule_delayed"
    return feature


def _apply_feature_defaults(seg_node, feature, phase_id=None):
    if not seg_node:
        return
    segmentation = seg_node.GetSegmentation()
    if not segmentation:
        return
    ids = vtk.vtkStringArray()
    segmentation.GetSegmentIDs(ids)
    if ids.GetNumberOfValues() == 0:
        name = FEATURE_SEGMENT_NAMES.get(feature, str(feature))
        try:
            seg_id = segmentation.AddEmptySegment(name)
        except Exception:
            seg_id = None
        if seg_id:
            try:
                segment = segmentation.GetSegment(seg_id)
            except Exception:
                segment = None
            if segment:
                color_key = _feature_color_key(feature, phase_id)
                color = FEATURE_COLORS.get(color_key)
                if color:
                    try:
                        segment.SetColor(float(color[0]), float(color[1]), float(color[2]))
                    except Exception:
                        pass
        return

    if ids.GetNumberOfValues() == 1:
        seg_id = ids.GetValue(0)
        try:
            segment = segmentation.GetSegment(seg_id)
        except Exception:
            segment = None
        if segment:
            name = segment.GetName() or ""
            if name.startswith("Segment"):
                try:
                    segment.SetName(FEATURE_SEGMENT_NAMES.get(feature, str(feature)))
                except Exception:
                    pass

    color_key = _feature_color_key(feature, phase_id)
    color = FEATURE_COLORS.get(color_key)
    if not color:
        return
    for idx in range(ids.GetNumberOfValues()):
        seg_id = ids.GetValue(idx)
        try:
            segment = segmentation.GetSegment(seg_id)
        except Exception:
            segment = None
        if segment:
            try:
                segment.SetColor(float(color[0]), float(color[1]), float(color[2]))
            except Exception:
                pass


def apply_feature_defaults(seg_node, feature, phase_id=None):
    _apply_feature_defaults(seg_node, feature, phase_id=phase_id)


def list_segmentation_nodes(case_id=None, feature=None, phase_id=None):
    nodes = []
    for seg_node in slicer.util.getNodesByClass("vtkMRMLSegmentationNode"):
        if not seg_node:
            continue
        if case_id:
            try:
                cid = seg_node.GetAttribute("AMLIFAI_CASE_ID")
            except Exception:
                cid = None
            if str(cid) != str(case_id):
                continue
        if feature:
            try:
                feat = seg_node.GetAttribute("AMLIFAI_FEATURE")
            except Exception:
                feat = None
            if str(feat) != str(feature):
                continue
        if phase_id is not None:
            try:
                pid = seg_node.GetAttribute("AMLIFAI_PHASE_ID")
            except Exception:
                pid = None
            if str(pid) != str(phase_id):
                continue
        nodes.append(seg_node)
    return nodes


def find_feature_segmentation_node(feature, case_id=None, phase_id=None):
    nodes = list_segmentation_nodes(case_id=case_id, feature=feature, phase_id=phase_id)
    return nodes[0] if nodes else None


def ensure_feature_segmentation_node(feature, case_id=None, phase_id=None):
    if feature == "lesion":
        phase_id = None
    existing = find_feature_segmentation_node(feature, case_id=case_id, phase_id=phase_id)
    if not existing and phase_id is not None:
        reuse_allowed = feature not in ("washout", "capsule")
        existing = (
            find_feature_segmentation_node(feature, case_id=case_id, phase_id=None)
            if reuse_allowed
            else None
        )
        if existing:
            try:
                existing.SetAttribute("AMLIFAI_PHASE_ID", str(phase_id))
            except Exception:
                pass
    if existing:
        if feature == "lesion":
            tag_segmentation(existing, case_id=case_id, feature=feature, phase_id=None)
        if case_id:
            name_parts = [str(case_id)]
            if phase_id is not None and feature != "lesion":
                phase_name = _phase_name_for_filename(phase_id)
                name_parts.append(str(phase_name))
            name_parts.append(str(feature))
            existing.SetName("_".join([p for p in name_parts if p]))
        if not existing.GetDisplayNode():
            try:
                existing.CreateDefaultDisplayNodes()
            except Exception:
                pass
        _apply_feature_defaults(existing, feature, phase_id=phase_id)
        return existing
    name_parts = [str(case_id)] if case_id else []
    if phase_id is not None and feature != "lesion":
        phase_name = _phase_name_for_filename(phase_id)
        name_parts.append(str(phase_name))
    name_parts.append(str(feature))
    name = "_".join([p for p in name_parts if p]) or "segmentation"
    seg_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode")
    seg_node.SetName(name)
    try:
        seg_node.CreateDefaultDisplayNodes()
    except Exception:
        pass
    tag_segmentation(seg_node, case_id=case_id, feature=feature, phase_id=phase_id)
    _apply_feature_defaults(seg_node, feature, phase_id=phase_id)
    return seg_node


def copy_segmentation_contents(source_node, target_node, feature=None, phase_id=None):
    if not source_node or not target_node:
        return False
    try:
        source_seg = source_node.GetSegmentation()
    except Exception:
        source_seg = None
    try:
        target_seg = target_node.GetSegmentation()
    except Exception:
        target_seg = None
    if not source_seg or not target_seg:
        return False
    try:
        target_seg.RemoveAllSegments()
    except Exception:
        pass
    try:
        target_seg.DeepCopy(source_seg)
        if feature:
            tag_segmentation(target_node, feature=feature, phase_id=phase_id)
            _apply_feature_defaults(target_node, feature, phase_id=phase_id)
        return True
    except Exception:
        return False


def clear_segmentation_contents(target_node, feature=None, phase_id=None):
    if not target_node:
        return False
    try:
        target_seg = target_node.GetSegmentation()
    except Exception:
        target_seg = None
    if not target_seg:
        return False
    try:
        target_seg.RemoveAllSegments()
    except Exception:
        pass
    if feature:
        try:
            tag_segmentation(target_node, feature=feature, phase_id=phase_id)
        except Exception:
            pass
        _apply_feature_defaults(target_node, feature, phase_id=phase_id)
    return True


def load_segmentation_into_node(path, target_node, feature=None, phase_id=None):
    if not path or not target_node:
        return False
    try:
        if hasattr(slicer.util, "loadSegmentation"):
            temp_node = slicer.util.loadSegmentation(path)
        else:
            temp_node = slicer.util.loadNode(path)
    except Exception:
        temp_node = None
    if not temp_node:
        return False
    ok = copy_segmentation_contents(temp_node, target_node, feature=feature, phase_id=phase_id)
    try:
        slicer.mrmlScene.RemoveNode(temp_node)
    except Exception:
        pass
    if ok and feature:
        _apply_feature_defaults(target_node, feature, phase_id=phase_id)
    return ok


def _phase_tokens(phase_id):
    if phase_id is None:
        return []
    tokens = [str(phase_id)]
    name = PHASE_NAMES.get(int(phase_id))
    if name:
        tokens.append(str(name))
    return tokens


def find_ai_segmentation_path(case_id, feature, phase_id=None, mask_root=None):
    if not case_id or not feature:
        return None
    root = mask_root or resolve_config_path(get_config_value("mask_root"))
    if not root or not os.path.isdir(root):
        return None
    case_dir = os.path.join(str(root), str(case_id))
    if not os.path.isdir(case_dir):
        return None
    patterns = [
        os.path.join(case_dir, f"{case_id}_*_{feature}*.seg.nrrd"),
        os.path.join(case_dir, f"{case_id}_*_{feature}*.nrrd"),
        os.path.join(case_dir, f"{case_id}_{feature}*.seg.nrrd"),
        os.path.join(case_dir, f"{case_id}_{feature}*.nrrd"),
    ]
    phase_tokens = [t.lower() for t in _phase_tokens(phase_id)]
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            parsed_feature, parsed_phase = _parse_segmentation_filename(path, case_id)
            if parsed_feature:
                if str(parsed_feature).lower() != str(feature).lower():
                    continue
                if phase_id is not None and parsed_phase is not None and int(parsed_phase) != int(phase_id):
                    continue
                if phase_id is not None and parsed_phase is None and str(feature).lower() != "lesion":
                    continue
                return path
            if str(feature).lower() not in os.path.basename(path).lower():
                continue
            if phase_tokens:
                lower = os.path.basename(path).lower()
                if not any(token in lower for token in phase_tokens):
                    continue
            return path
    return None


def load_ai_segmentation_into_node(case_id, feature, phase_id, target_node, mask_root=None):
    path = find_ai_segmentation_path(case_id, feature, phase_id=phase_id, mask_root=mask_root)
    if not path:
        return False
    return load_segmentation_into_node(path, target_node, feature=feature, phase_id=phase_id)


def segment_has_voxels(seg_node):
    if not seg_node:
        return False
    segmentation = seg_node.GetSegmentation() if seg_node else None
    if not segmentation:
        return False
    ids = vtk.vtkStringArray()
    segmentation.GetSegmentIDs(ids)
    if ids.GetNumberOfValues() <= 0:
        return False
    for idx in range(ids.GetNumberOfValues()):
        seg_id = ids.GetValue(idx)
        try:
            segment = segmentation.GetSegment(seg_id)
        except Exception:
            segment = None
        if segment:
            try:
                labelmap = segment.GetRepresentation("Binary labelmap")
            except Exception:
                labelmap = None
            if labelmap and hasattr(labelmap, "GetPointData"):
                try:
                    scalars = labelmap.GetPointData().GetScalars()
                    if scalars and scalars.GetRange()[1] > 0:
                        return True
                except Exception:
                    pass
            try:
                poly = segment.GetRepresentation("Closed surface")
                if poly and poly.GetNumberOfPoints() > 0:
                    return True
            except Exception:
                pass
        try:
            arr = slicer.util.arrayFromSegmentBinaryLabelmap(seg_node, seg_id)
        except Exception:
            arr = None
        if arr is None:
            continue
        try:
            if arr.any():
                return True
        except Exception:
            try:
                if arr.sum() > 0:
                    return True
            except Exception:
                pass
    return False


def save_segmentation_node(seg_node, path):
    if not seg_node or not path:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        storage = seg_node.GetStorageNode()
    except Exception:
        storage = None
    if not storage:
        storage = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationStorageNode")
        seg_node.SetAndObserveStorageNodeID(storage.GetID())
    storage.SetFileName(path)
    return bool(slicer.util.saveNode(seg_node, path))


def _resolve_save_dir():
    save_dir = resolve_config_path(get_config_value("seg_save_dir"))
    if save_dir:
        return save_dir
    mask_root = resolve_config_path(get_config_value("mask_root"))
    if mask_root:
        return mask_root
    scan_root = resolve_config_path(get_config_value("scan_root"))
    if scan_root:
        return scan_root
    return None


def _choose_seg_extension(directory):
    ext = ".seg.nrrd"
    try:
        existing = [
            name for name in os.listdir(directory) if name.lower().endswith(".nrrd")
        ]
    except Exception:
        existing = []
    if any(name.endswith(".nrrd") for name in existing) and not any(
        name.endswith(".seg.nrrd") for name in existing
    ):
        ext = ".nrrd"
    return ext


def save_active_segmentation(seg_node, case_id=None):
    if not seg_node:
        return None
    if case_id is None:
        try:
            case_id = seg_node.GetAttribute("AMLIFAI_CASE_ID")
        except Exception:
            case_id = None
    if not case_id:
        return None

    directory = None
    try:
        storage = seg_node.GetStorageNode()
    except Exception:
        storage = None
    if storage:
        try:
            storage_path = storage.GetFileName() or storage.GetURI()
        except Exception:
            storage_path = None
        if storage_path:
            directory = os.path.dirname(storage_path)

    if not directory:
        save_root = _resolve_save_dir()
        if not save_root:
            return None
        directory = os.path.join(str(save_root), str(case_id))

    os.makedirs(directory, exist_ok=True)
    ext = _choose_seg_extension(directory)
    name = seg_node.GetName() or "segmentation"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(name)).strip("_")
    safe = safe or "segmentation"
    filename = f"{safe}{ext}"
    path = os.path.join(directory, filename)
    ok = save_segmentation_node(seg_node, path)
    return path if ok else None


def has_unsaved_segmentations():
    for seg_node in slicer.util.getNodesByClass("vtkMRMLSegmentationNode"):
        if not seg_node:
            continue
        try:
            if seg_node.GetModifiedSinceRead():
                return True
        except Exception:
            pass
    return False


def _phase_name_for_filename(phase_id):
    name = PHASE_NAMES.get(int(phase_id), str(phase_id))
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(name)).strip("_")
    return safe or str(phase_id)


def _build_seg_filename(case_id, feature=None, phase_id=None):
    if feature:
        if phase_id is not None:
            phase_name = _phase_name_for_filename(phase_id)
            base = f"{case_id}_{phase_name}_{feature}"
        else:
            base = f"{case_id}_{feature}"
    else:
        base = f"{case_id}_segmentation"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(base)).strip("_")
    return f"{safe}.seg.nrrd"


def save_all_segmentations(case_id=None):
    save_dir = _resolve_save_dir()
    if not save_dir:
        return False
    case_id = str(case_id) if case_id else None
    ok = True
    for seg_node in slicer.util.getNodesByClass("vtkMRMLSegmentationNode"):
        if not seg_node:
            continue
        try:
            cid = seg_node.GetAttribute("AMLIFAI_CASE_ID")
        except Exception:
            cid = None
        if case_id and str(cid) != str(case_id):
            continue
        if not segment_has_voxels(seg_node):
            # Skip empty placeholder nodes (for example AI preview or fresh scratch).
            continue
        try:
            feature = seg_node.GetAttribute("AMLIFAI_FEATURE")
        except Exception:
            feature = None
        try:
            phase_id = seg_node.GetAttribute("AMLIFAI_PHASE_ID")
        except Exception:
            phase_id = None
        use_case = case_id or cid or "UNKNOWN"
        case_dir = os.path.join(str(save_dir), str(use_case))
        filename = _build_seg_filename(use_case, feature=feature, phase_id=phase_id)
        path = os.path.join(case_dir, filename)
        ok = save_segmentation_node(seg_node, path) and ok
    return ok


def _phase_id_from_token(token):
    if token is None:
        return None
    norm = "".join(ch.lower() for ch in str(token) if ch.isalnum())
    for pid, name in PHASE_NAMES.items():
        key = "".join(ch.lower() for ch in str(name) if ch.isalnum())
        if norm == key:
            return int(pid)
    return None


def _parse_segmentation_filename(path, case_id):
    base = os.path.basename(path)
    name = base
    for suffix in (".seg.nrrd", ".nrrd"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    parts = [p for p in name.split("_") if p]
    if not parts:
        return None, None
    if str(parts[0]).upper() != str(case_id).upper():
        return None, None
    if len(parts) == 1:
        return None, None
    phase_id = _phase_id_from_token(parts[1])
    feature_parts = parts[2:] if phase_id is not None else parts[1:]
    feature = "_".join(feature_parts) if feature_parts else None
    return feature, phase_id


def load_saved_segmentations(case_id, seg_dir=None):
    if not case_id:
        return []
    seg_dir = seg_dir or get_config_value("seg_save_dir")
    if not seg_dir or not os.path.isdir(seg_dir):
        return []
    case_dir = os.path.join(seg_dir, str(case_id))
    if not os.path.isdir(case_dir):
        return []
    loaded = []
    patterns = [
        os.path.join(case_dir, f"{case_id}*.seg.nrrd"),
    ]
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            feature, phase_id = _parse_segmentation_filename(path, case_id)
            existing = None
            if feature:
                existing = find_feature_segmentation_node(
                    feature, case_id=case_id, phase_id=phase_id
                )
                # If a node already exists but is empty (common when source was set to
                # scratch before reopening), hydrate it from disk instead of skipping.
                if existing and not segment_has_voxels(existing):
                    ok = load_segmentation_into_node(
                        path, existing, feature=feature, phase_id=phase_id
                    )
                    if ok:
                        loaded.append(existing)
                    continue
                if existing:
                    continue
            try:
                if hasattr(slicer.util, "loadSegmentation"):
                    seg_node = slicer.util.loadSegmentation(path)
                else:
                    seg_node = slicer.util.loadNode(path)
            except Exception:
                seg_node = None
            if not seg_node:
                continue
            tag_segmentation(seg_node, case_id=case_id, feature=feature, phase_id=phase_id)
            loaded.append(seg_node)
    return loaded
