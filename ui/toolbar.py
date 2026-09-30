import qt
import slicer

from data.loader import DEFAULT_LEVEL, DEFAULT_WINDOW, LOADER_ATTR, list_unseen_case_ids
from data.layout import PHASE_COMPARE_TAGS
from segmentation.services import find_feature_segmentation_node, segment_has_voxels

PHASE_TAGS = ("P0", "P1", "P2", "P3")

_TOOLBAR = None
_SEG_TOGGLE_TIMER = None
_GRID_SYNC_OBSERVERS = []
_SYNC_STATE = {"view_mode": None, "sync_mode": None}
_WL_OBSERVERS = []
_WL_SYNC_STATE = {"case_id": None, "volume_ids": set(), "busy": False, "last": None}

SEGMENT_ACTION_COLORS = {
    "lesion": (0.9, 0.2, 0.2),
    "aphe": (0.2, 0.7, 0.2),
    "washout_venous": (0.95, 0.55, 0.1),
    "washout_delayed": (0.15, 0.75, 0.85),
    "capsule_venous": (0.55, 0.2, 0.8),
    "capsule_delayed": (0.2, 0.55, 0.95),
}


def _slice_node(tag):
    lm = slicer.app.layoutManager()
    if not lm:
        return None
    widget = lm.sliceWidget(tag)
    if not widget:
        return None
    logic = widget.sliceLogic()
    return logic.GetSliceNode() if logic else None


def _clear_grid_sync_observers():
    for obj, cid in _GRID_SYNC_OBSERVERS:
        if obj and cid:
            obj.RemoveObserver(cid)
    _GRID_SYNC_OBSERVERS.clear()


def _clear_window_level_observers():
    for obj, cid in _WL_OBSERVERS:
        if obj and cid:
            obj.RemoveObserver(cid)
    _WL_OBSERVERS.clear()


def _make_offset_sync_callback(source_node, target_node, guard):
    def _cb(_caller, _event):
        if guard["busy"]:
            return
        guard["busy"] = True
        try:
            target_node.SetSliceOffset(source_node.GetSliceOffset())
        finally:
            guard["busy"] = False

    return _cb


def _link_all_views():
    _clear_grid_sync_observers()
    nodes = []
    for tag in PHASE_TAGS:
        node = _slice_node(tag)
        if node:
            nodes.append(node)
    _link_views(nodes)


def _link_compare_views():
    _clear_grid_sync_observers()
    nodes = []
    for tag in PHASE_COMPARE_TAGS:
        node = _slice_node(tag)
        if node:
            nodes.append(node)
    _link_views(nodes)


def _link_views(nodes):
    if len(nodes) < 2:
        return
    guard = {"busy": False}
    for source in nodes:
        for target in nodes:
            if source is target:
                continue
            cid = source.AddObserver("ModifiedEvent", _make_offset_sync_callback(source, target, guard))
            _GRID_SYNC_OBSERVERS.append((source, cid))


def _apply_grid_sync_mode(driver):
    view_mode = getattr(driver, "view_mode", None)
    sync_mode = getattr(driver, "sync_mode", None) or "independent"
    if _SYNC_STATE["view_mode"] == view_mode and _SYNC_STATE["sync_mode"] == sync_mode:
        return
    _SYNC_STATE["view_mode"] = view_mode
    _SYNC_STATE["sync_mode"] = sync_mode
    if view_mode not in ("grid", "compare"):
        _clear_grid_sync_observers()
        return
    if sync_mode == "all":
        if view_mode == "compare":
            _link_compare_views()
        else:
            _link_all_views()
    else:
        _clear_grid_sync_observers()


def _feature_display_state(seg_node):
    if not seg_node:
        return False
    try:
        disp = seg_node.GetDisplayNode()
    except Exception:
        disp = None
    if not disp:
        return False
    try:
        return bool(disp.GetVisibility())
    except Exception:
        return False


def _case_volumes(case_id):
    if not case_id:
        return []
    volumes = []
    for node in slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode"):
        try:
            if not node.GetAttribute(LOADER_ATTR):
                continue
        except Exception:
            continue
        name = ""
        try:
            name = node.GetName() or ""
        except Exception:
            name = ""
        if name.startswith(f"{case_id}_"):
            volumes.append(node)
    return volumes


def _apply_window_level_to_volumes(window, level, volumes):
    for vol in volumes:
        if not vol:
            continue
        if not vol.GetDisplayNode():
            vol.CreateDefaultDisplayNodes()
        disp = vol.GetDisplayNode()
        if not disp:
            continue
        try:
            disp.AutoWindowLevelOff()
            disp.SetWindowLevel(float(window), float(level))
        except Exception:
            continue


def _save_window_level(registry, case_id, window, level):
    if not registry or not case_id:
        return
    try:
        state = registry.get(case_id)
        state.window_level = {"window": float(window), "level": float(level)}
        registry.save(state)
    except Exception:
        pass


def _make_window_level_callback(case_id, registry, driver, guard):
    def _cb(caller, _event):
        if guard["busy"]:
            return
        if not case_id or getattr(driver, "current_case", None) != case_id:
            return
        try:
            window = float(caller.GetWindow())
            level = float(caller.GetLevel())
        except Exception:
            return
        last = guard.get("last")
        if last and abs(window - last[0]) < 1e-3 and abs(level - last[1]) < 1e-3:
            return
        guard["busy"] = True
        try:
            volumes = _case_volumes(case_id)
            _apply_window_level_to_volumes(window, level, volumes)
            guard["last"] = (window, level)
            if driver is not None:
                driver.window_level = {"window": window, "level": level}
            _save_window_level(registry, case_id, window, level)
        finally:
            guard["busy"] = False

    return _cb


def _ensure_window_level_sync(driver, registry):
    case_id = getattr(driver, "current_case", None)
    volumes = _case_volumes(case_id) if case_id else []
    volume_ids = set()
    for vol in volumes:
        try:
            volume_ids.add(vol.GetID())
        except Exception:
            continue
    case_changed = case_id != _WL_SYNC_STATE["case_id"]
    volumes_changed = volume_ids != _WL_SYNC_STATE["volume_ids"]
    if not case_id:
        _clear_window_level_observers()
        _WL_SYNC_STATE.update({"case_id": None, "volume_ids": set(), "last": None})
        return
    if case_changed or volumes_changed:
        _clear_window_level_observers()
        _WL_SYNC_STATE["case_id"] = case_id
        _WL_SYNC_STATE["volume_ids"] = volume_ids
        _WL_SYNC_STATE["last"] = None
        saved = None
        if registry:
            try:
                saved = registry.get(case_id).window_level
            except Exception:
                saved = None
        if isinstance(saved, dict) and "window" in saved and "level" in saved:
            try:
                _apply_window_level_to_volumes(saved["window"], saved["level"], volumes)
                _WL_SYNC_STATE["last"] = (float(saved["window"]), float(saved["level"]))
                driver.window_level = {"window": float(saved["window"]), "level": float(saved["level"])}
            except Exception:
                pass
        elif case_changed and volumes:
            _apply_window_level_to_volumes(DEFAULT_WINDOW, DEFAULT_LEVEL, volumes)
            _WL_SYNC_STATE["last"] = None
            if driver is not None:
                driver.window_level = None
        for vol in volumes:
            if not vol:
                continue
            try:
                if not vol.GetDisplayNode():
                    vol.CreateDefaultDisplayNodes()
                disp = vol.GetDisplayNode()
            except Exception:
                disp = None
            if not disp:
                continue
            cid = disp.AddObserver("ModifiedEvent", _make_window_level_callback(case_id, registry, driver, _WL_SYNC_STATE))
            _WL_OBSERVERS.append((disp, cid))


def _toggle_feature_visibility(feature, case_id, phase_id=None):
    if not case_id:
        return
    seg_node = find_feature_segmentation_node(feature, case_id=case_id, phase_id=phase_id)
    if not segment_has_voxels(seg_node):
        return
    try:
        if not seg_node.GetDisplayNode():
            seg_node.CreateDefaultDisplayNodes()
        disp = seg_node.GetDisplayNode()
        if disp:
            disp.SetVisibility(0 if disp.GetVisibility() else 1)
    except Exception:
        pass


def _update_seg_toggle_actions(driver, actions):
    case_id = getattr(driver, "current_case", None)
    if not case_id:
        for action in actions.values():
            action.setEnabled(False)
            action.setChecked(False)
        return
    config = [
        ("lesion", actions["lesion"], "Lesion", None),
        ("aphe", actions["aphe"], "APHE", None),
        ("washout", actions["washout_venous"], "Washout (Venous)", 1),
        ("washout", actions["washout_delayed"], "Washout (Delayed)", 2),
        ("capsule", actions["capsule_venous"], "Capsule (Venous)", 1),
        ("capsule", actions["capsule_delayed"], "Capsule (Delayed)", 2),
    ]
    for feature, action, label, phase_id in config:
        seg_node = find_feature_segmentation_node(
            feature, case_id=case_id, phase_id=phase_id
        )
        has_seg = segment_has_voxels(seg_node)
        action.setEnabled(bool(has_seg))
        action.setText(label)
        if not has_seg:
            action.setChecked(False)
            continue
        action.setChecked(_feature_display_state(seg_node))


def _toolbar_text_color(rgb):
    if not rgb or len(rgb) != 3:
        return "#ffffff"
    r, g, b = rgb
    luminance = (0.299 * r + 0.587 * g + 0.114 * b)
    return "#1a1a1a" if luminance > 0.62 else "#ffffff"


def _apply_seg_action_colors(toolbar, seg_actions):
    for key, action in seg_actions.items():
        color = SEGMENT_ACTION_COLORS.get(key)
        if not color or not toolbar:
            continue
        btn = toolbar.widgetForAction(action)
        if not btn:
            continue
        r, g, b = [int(max(0, min(1, c)) * 255) for c in color]
        text_color = _toolbar_text_color(color)
        btn.setStyleSheet(
            "QToolButton {"
            f" background-color: rgba({r}, {g}, {b}, 120);"
            f" color: {text_color};"
            " font-weight: 600;"
            " padding: 4px 6px;"
            " border-radius: 4px;"
            "}"
            "QToolButton:checked {"
            f" background-color: rgb({r}, {g}, {b});"
            f" color: {text_color};"
            "}"
            "QToolButton:disabled {"
            " background-color: #e0e0e0;"
            " color: #777777;"
            "}"
        )


def add_driver_toolbar(driver, registry=None, on_case_changed=None):
    global _TOOLBAR, _SEG_TOGGLE_TIMER
    if _TOOLBAR:
        return _TOOLBAR
    mw = slicer.util.mainWindow()
    if mw is None:
        raise RuntimeError("Main window not ready yet.")

    tb = qt.QToolBar("Driver")
    tb.setObjectName("DriverToolbar")
    mw.addToolBar(tb)

    act_prev_case = qt.QAction("Prev case", tb)
    act_next_case = qt.QAction("Next case", tb)
    act_adjust_wl = qt.QAction("Adjust Window/Level", tb)
    act_adjust_wl.setCheckable(True)
    act_sync_toggle = qt.QAction("Sync: Independent", tb)
    act_sync_toggle.setCheckable(True)
    if not hasattr(driver, "sync_mode"):
        driver.sync_mode = "independent"

    act_seg_lesion = qt.QAction("Hide Lesion", tb)
    act_seg_aphe = qt.QAction("Hide APHE", tb)
    act_seg_washout_venous = qt.QAction("Hide Washout (Venous)", tb)
    act_seg_washout_delayed = qt.QAction("Hide Washout (Delayed)", tb)
    act_seg_capsule_venous = qt.QAction("Hide Capsule (Venous)", tb)
    act_seg_capsule_delayed = qt.QAction("Hide Capsule (Delayed)", tb)
    for action in (
        act_seg_lesion,
        act_seg_aphe,
        act_seg_washout_venous,
        act_seg_washout_delayed,
        act_seg_capsule_venous,
        act_seg_capsule_delayed,
    ):
        action.setCheckable(True)

    seg_actions = {
        "lesion": act_seg_lesion,
        "aphe": act_seg_aphe,
        "washout_venous": act_seg_washout_venous,
        "washout_delayed": act_seg_washout_delayed,
        "capsule_venous": act_seg_capsule_venous,
        "capsule_delayed": act_seg_capsule_delayed,
    }

    def update_sync_toggle_label():
        mode = getattr(driver, "sync_mode", None) or "independent"
        view_mode = getattr(driver, "view_mode", None)
        if mode == "all":
            if view_mode == "compare":
                act_sync_toggle.setText("Sync: Linked")
            else:
                act_sync_toggle.setText("Sync: All Views")
            act_sync_toggle.setChecked(True)
        else:
            act_sync_toggle.setText("Sync: Independent")
            act_sync_toggle.setChecked(False)
        act_sync_toggle.setEnabled(view_mode in ("grid", "compare"))

    def on_sync_toggle(checked):
        driver.sync_mode = "all" if checked else "independent"
        update_sync_toggle_label()
        _apply_grid_sync_mode(driver)

    def _unseen_case_ids():
        if registry is None:
            raise FileNotFoundError("Case registry not available.")
        return list_unseen_case_ids(driver, registry)

    def on_prev_case():
        try:
            driver.prev_case(case_ids=_unseen_case_ids())
            if callable(on_case_changed):
                on_case_changed(getattr(driver, "current_case", None))
        except Exception as exc:
            slicer.util.errorDisplay(f"Prev case failed: {exc}")

    def on_next_case():
        try:
            driver.next_case(case_ids=_unseen_case_ids())
            if callable(on_case_changed):
                on_case_changed(getattr(driver, "current_case", None))
        except Exception as exc:
            slicer.util.errorDisplay(f"Next case failed: {exc}")

    def on_adjust_wl():
        try:
            interaction_node = slicer.app.applicationLogic().GetInteractionNode()
            mode_adjust = None
            mode_view = None
            for attr in ("InteractionModeAdjustWindowLevel", "AdjustWindowLevel"):
                if hasattr(slicer.vtkMRMLInteractionNode, attr):
                    mode_adjust = getattr(slicer.vtkMRMLInteractionNode, attr)
                    break
            for attr in ("InteractionModeViewTransform", "ViewTransform"):
                if hasattr(slicer.vtkMRMLInteractionNode, attr):
                    mode_view = getattr(slicer.vtkMRMLInteractionNode, attr)
                    break
            if interaction_node and mode_adjust is not None:
                current = interaction_node.GetCurrentInteractionMode()
                if mode_view is not None and current == mode_adjust:
                    interaction_node.SetCurrentInteractionMode(mode_view)
                else:
                    interaction_node.SetCurrentInteractionMode(mode_adjust)
            else:
                slicer.util.selectModule("WindowLevel")
            _update_adjust_wl_state()
        except Exception as exc:
            slicer.util.errorDisplay(f"Adjust Window/Level failed: {exc}")

    act_prev_case.triggered.connect(on_prev_case)
    act_next_case.triggered.connect(on_next_case)
    act_adjust_wl.triggered.connect(on_adjust_wl)
    act_sync_toggle.triggered.connect(on_sync_toggle)
    act_seg_lesion.triggered.connect(
        lambda: _toggle_feature_visibility("lesion", getattr(driver, "current_case", None))
    )
    act_seg_aphe.triggered.connect(
        lambda: _toggle_feature_visibility("aphe", getattr(driver, "current_case", None))
    )
    act_seg_washout_venous.triggered.connect(
        lambda: _toggle_feature_visibility("washout", getattr(driver, "current_case", None), phase_id=1)
    )
    act_seg_washout_delayed.triggered.connect(
        lambda: _toggle_feature_visibility("washout", getattr(driver, "current_case", None), phase_id=2)
    )
    act_seg_capsule_venous.triggered.connect(
        lambda: _toggle_feature_visibility("capsule", getattr(driver, "current_case", None), phase_id=1)
    )
    act_seg_capsule_delayed.triggered.connect(
        lambda: _toggle_feature_visibility("capsule", getattr(driver, "current_case", None), phase_id=2)
    )

    tb.addAction(act_prev_case)
    tb.addAction(act_next_case)
    tb.addAction(act_adjust_wl)
    tb.addSeparator()
    tb.addAction(act_sync_toggle)
    tb.addSeparator()
    tb.addAction(act_seg_lesion)
    tb.addAction(act_seg_aphe)
    tb.addAction(act_seg_washout_venous)
    tb.addAction(act_seg_washout_delayed)
    tb.addAction(act_seg_capsule_venous)
    tb.addAction(act_seg_capsule_delayed)

    _apply_seg_action_colors(tb, seg_actions)

    if _SEG_TOGGLE_TIMER is None:
        _SEG_TOGGLE_TIMER = qt.QTimer()
        _SEG_TOGGLE_TIMER.setInterval(1000)

        def _tick():
            update_sync_toggle_label()
            _apply_grid_sync_mode(driver)
            _ensure_window_level_sync(driver, registry)
            _update_seg_toggle_actions(driver, seg_actions)
            _update_adjust_wl_state()

        _SEG_TOGGLE_TIMER.timeout.connect(_tick)
        _SEG_TOGGLE_TIMER.start()
    _update_seg_toggle_actions(driver, seg_actions)
    update_sync_toggle_label()
    _apply_grid_sync_mode(driver)
    _update_adjust_wl_state()

    _TOOLBAR = tb
    return tb


def _update_adjust_wl_state():
    try:
        interaction_node = slicer.app.applicationLogic().GetInteractionNode()
    except Exception:
        interaction_node = None
    if not interaction_node:
        return
    mode_adjust = None
    for attr in ("InteractionModeAdjustWindowLevel", "AdjustWindowLevel"):
        if hasattr(slicer.vtkMRMLInteractionNode, attr):
            mode_adjust = getattr(slicer.vtkMRMLInteractionNode, attr)
            break
    if mode_adjust is None:
        return
    try:
        active = interaction_node.GetCurrentInteractionMode() == mode_adjust
    except Exception:
        active = False
    try:
        if _TOOLBAR:
            for action in _TOOLBAR.actions():
                if action.text() == "Adjust Window/Level":
                    action.setChecked(bool(active))
                    break
    except Exception:
        pass
