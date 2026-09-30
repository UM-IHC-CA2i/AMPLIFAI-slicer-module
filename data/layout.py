import time

import slicer
import slicer.util as su

# Use the same IDs as the v1 layout to avoid conflicts.
CUSTOM_LAYOUT_ID = 502
PHASE_GRID_LAYOUT_ID = 503
PHASE_COMPARE_LAYOUT_ID = 504

PHASE_NAMES = {
    3: "Dry",
    0: "Arterial",
    1: "Venous",
    2: "Delayed",
}

PHASE_VIEW_TAGS = {
    0: ("P0",),  # arterial
    1: ("P1",),  # venous
    2: ("P2",),  # delayed
    3: ("P3",),  # dry
}

# Tags used in the custom layout (single axial view).
TOP_SLICE_TAGS = ["CT1_Axial"]
PHASE_COMPARE_TAGS = ("CMP_REF", "CMP_FOCUS")

layoutXML = """
<layout type="horizontal" split="false">
  <item>
    <view class="vtkMRMLSliceNode" singletontag="CT1_Axial">
      <property name="orientation" action="default">Axial</property>
    </view>
  </item>
</layout>
"""

phaseGridLayoutXML = """
<layout type="vertical" split="true">
  <item>
    <layout type="horizontal" split="true">
      <item><view class="vtkMRMLSliceNode" singletontag="P0"><property name="orientation" action="default">Axial</property></view></item>
      <item><view class="vtkMRMLSliceNode" singletontag="P1"><property name="orientation" action="default">Axial</property></view></item>
    </layout>
  </item>
  <item>
    <layout type="horizontal" split="true">
      <item><view class="vtkMRMLSliceNode" singletontag="P2"><property name="orientation" action="default">Axial</property></view></item>
      <item><view class="vtkMRMLSliceNode" singletontag="P3"><property name="orientation" action="default">Axial</property></view></item>
    </layout>
  </item>
</layout>
"""

phaseCompareLayoutXML = """
<layout type="horizontal" split="true">
  <item>
    <view class="vtkMRMLSliceNode" singletontag="CMP_REF">
      <property name="orientation" action="default">Axial</property>
    </view>
  </item>
  <item>
    <view class="vtkMRMLSliceNode" singletontag="CMP_FOCUS">
      <property name="orientation" action="default">Axial</property>
    </view>
  </item>
</layout>
"""


def _wait_for_layout_manager(timeout_sec=5.0, poll_sec=0.1):
    deadline = time.time() + timeout_sec
    lm = slicer.app.layoutManager()
    while lm is None and time.time() < deadline:
        slicer.app.processEvents()
        time.sleep(poll_sec)
        lm = slicer.app.layoutManager()
    if lm is None:
        raise RuntimeError("Layout manager not ready.")
    return lm


def _wait_for_layout_node(timeout_sec=5.0, poll_sec=0.1):
    deadline = time.time() + timeout_sec
    lm = _wait_for_layout_manager(timeout_sec=timeout_sec, poll_sec=poll_sec)
    layout_logic = lm.layoutLogic() if lm else None
    layout_node = layout_logic.GetLayoutNode() if layout_logic else None
    while layout_node is None and time.time() < deadline:
        slicer.app.processEvents()
        time.sleep(poll_sec)
        layout_logic = lm.layoutLogic() if lm else None
        layout_node = layout_logic.GetLayoutNode() if layout_logic else None
    return layout_node


def ensure_layouts_registered():
    layout_node = _wait_for_layout_node()
    if not layout_node:
        return False
    if layout_node.GetLayoutDescription(CUSTOM_LAYOUT_ID) != layoutXML:
        layout_node.AddLayoutDescription(CUSTOM_LAYOUT_ID, layoutXML)
    if layout_node.GetLayoutDescription(PHASE_GRID_LAYOUT_ID) != phaseGridLayoutXML:
        layout_node.AddLayoutDescription(PHASE_GRID_LAYOUT_ID, phaseGridLayoutXML)
    if layout_node.GetLayoutDescription(PHASE_COMPARE_LAYOUT_ID) != phaseCompareLayoutXML:
        layout_node.AddLayoutDescription(PHASE_COMPARE_LAYOUT_ID, phaseCompareLayoutXML)
    return True


def set_custom_layout():
    if not ensure_layouts_registered():
        return
    lm = slicer.app.layoutManager()
    if lm:
        lm.setLayout(CUSTOM_LAYOUT_ID)


def set_phase_grid_layout():
    if not ensure_layouts_registered():
        return
    lm = slicer.app.layoutManager()
    if lm:
        lm.setLayout(PHASE_GRID_LAYOUT_ID)


def set_phase_compare_layout():
    if not ensure_layouts_registered():
        return
    lm = slicer.app.layoutManager()
    if lm:
        lm.setLayout(PHASE_COMPARE_LAYOUT_ID)


def apply_layout_for_volume(volume_node):
    if not volume_node:
        return
    set_custom_layout()
    su.setSliceViewerLayers(background=volume_node)
    su.resetSliceViews()


def apply_compare_layout(reference_volume, focus_volume):
    if not reference_volume and not focus_volume:
        return
    set_phase_compare_layout()
    if focus_volume:
        set_background(focus_volume, [PHASE_COMPARE_TAGS[0]])
    if reference_volume:
        set_background(reference_volume, [PHASE_COMPARE_TAGS[1]])
    fit_views(PHASE_COMPARE_TAGS)


def _slice_logic(tag):
    lm = slicer.app.layoutManager()
    if not lm:
        return None
    widget = lm.sliceWidget(tag)
    if not widget:
        return None
    return widget.sliceLogic()


def set_background(volume_node, slice_tags):
    if not volume_node:
        return
    for tag in slice_tags:
        logic = _slice_logic(tag)
        if not logic:
            continue
        logic.GetSliceCompositeNode().SetBackgroundVolumeID(volume_node.GetID())


def fit_views(slice_tags):
    for tag in slice_tags:
        logic = _slice_logic(tag)
        if not logic:
            continue
        logic.FitSliceToAll()


def clear_backgrounds(slice_tags):
    for tag in slice_tags:
        logic = _slice_logic(tag)
        if not logic:
            continue
        logic.GetSliceCompositeNode().SetBackgroundVolumeID(None)


def disable_linking(slice_tags):
    for idx, tag in enumerate(slice_tags):
        logic = _slice_logic(tag)
        if not logic:
            continue
        comp = logic.GetSliceCompositeNode()
        if hasattr(comp, "SetLinkedControlGroup"):
            comp.SetLinkedControlGroup(1000 + idx)
        comp.SetLinkedControl(0)
        if hasattr(comp, "SetHotLinkedControl"):
            comp.SetHotLinkedControl(0)


def disable_propagation(slice_tags):
    for tag in slice_tags:
        logic = _slice_logic(tag)
        if not logic:
            continue
        comp = logic.GetSliceCompositeNode()
        if hasattr(comp, "SetDoPropagateVolumeSelection"):
            comp.SetDoPropagateVolumeSelection(0)
