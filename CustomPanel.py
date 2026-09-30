import os
import sys

import qt
import slicer
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleWidget,
    ScriptedLoadableModuleLogic,
)

# Ensure v2 package is importable when loaded by Slicer.
_V2_DIR = os.path.dirname(__file__)
if _V2_DIR not in sys.path:
    sys.path.append(_V2_DIR)

from data.loader import DriverState  # noqa: E402
from state.registry import CaseRegistry  # noqa: E402
from data.layout import set_phase_grid_layout  # noqa: E402
from ui.panels import CaseManagerPanel, WorkflowPanel  # noqa: E402
from ui.toolbar import add_driver_toolbar  # noqa: E402
from segmentation.services import load_saved_segmentations  # noqa: E402


class CustomPanelV2(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        parent.title = "AMLIFAI v2"
        parent.categories = ["Informatics"]
        parent.contributors = ["Amritansh"]
        parent.helpText = ""
        parent.acknowledgementText = ""
        self._open_panel_on_startup()

    def _open_panel_on_startup(self):
        try:
            if hasattr(CustomPanelV2, "_startup_select_done") and CustomPanelV2._startup_select_done:
                return
            CustomPanelV2._startup_select_done = True

            def _select():
                try:
                    slicer.util.selectModule("CustomPanel")
                except Exception:
                    pass

            if hasattr(slicer, "app") and slicer.app and hasattr(slicer.app, "connect"):
                slicer.app.connect("startupCompleted()", lambda: qt.QTimer.singleShot(0, _select))
            else:
                qt.QTimer.singleShot(0, _select)
        except Exception:
            pass


class CustomPanelV2Widget(ScriptedLoadableModuleWidget):
    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        self.logic = CustomPanelV2Logic()
        self.driver = DriverState()
        self.registry = CaseRegistry()

        try:
            add_driver_toolbar(
                self.driver,
                registry=self.registry,
                on_case_changed=self._on_toolbar_case_changed,
            )
        except Exception:
            pass
        try:
            set_phase_grid_layout()
        except Exception:
            pass

        layout = qt.QVBoxLayout()
        layout.setSizeConstraint(qt.QLayout.SetMinAndMaxSize)

        header = qt.QLabel("AMPLIFAI v2")
        header.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(header)

        self.workflowPanel = WorkflowPanel(
            driver=self.driver,
            registry=self.registry,
            on_step_changed=self._on_workflow_step_changed,
        )
        self.caseManagerPanel = CaseManagerPanel(
            driver=self.driver,
            registry=self.registry,
            on_case_opened=self.workflowPanel.sync_from_case,
        )
        layout.addWidget(self.caseManagerPanel)
        summary_widget = self.caseManagerPanel.summary_widget()
        if summary_widget:
            divider_top = qt.QFrame()
            divider_top.setFrameShape(qt.QFrame.HLine)
            divider_top.setFrameShadow(qt.QFrame.Sunken)
            divider_top.setFixedHeight(2)
            layout.addSpacing(6)
            layout.addWidget(divider_top)
            layout.addSpacing(6)
            layout.addWidget(summary_widget)
        divider = qt.QFrame()
        divider.setFrameShape(qt.QFrame.HLine)
        divider.setFrameShadow(qt.QFrame.Sunken)
        divider.setFixedHeight(2)
        layout.addSpacing(6)
        layout.addWidget(divider)
        layout.addSpacing(6)
        layout.addWidget(self.workflowPanel)

        self.parent.layout().addLayout(layout)
        self._set_data_probe_visible(False)
        qt.QTimer.singleShot(0, self._auto_load_last_case)

    def enter(self):
        base_enter = getattr(ScriptedLoadableModuleWidget, "enter", None)
        if callable(base_enter):
            base_enter(self)
        self._set_data_probe_visible(False)

    def _set_data_probe_visible(self, visible):
        try:
            if hasattr(slicer.util, "setDataProbeVisible"):
                slicer.util.setDataProbeVisible(bool(visible))
        except Exception:
            pass

    def _on_workflow_step_changed(self, _case_id):
        try:
            self.caseManagerPanel.refresh()
        except Exception:
            pass

    def _on_toolbar_case_changed(self, case_id):
        try:
            if case_id and hasattr(self, "caseManagerPanel"):
                try:
                    self.caseManagerPanel.clear_scene_for_case(case_id)
                except Exception:
                    pass
            if case_id:
                state = self.registry.get(case_id)
                if state and getattr(state, "window_level", None):
                    self.driver.window_level = dict(state.window_level)
                else:
                    self.driver.window_level = None
                try:
                    load_saved_segmentations(case_id)
                except Exception:
                    pass
                try:
                    state.touch()
                    self.registry.save(state)
                except Exception:
                    pass
            self.workflowPanel.sync_from_case(case_id)
        except Exception:
            pass
        try:
            self.caseManagerPanel.refresh()
        except Exception:
            pass

    def _auto_load_last_case(self):
        if getattr(self.driver, "current_case", None):
            return
        best_case = None
        best_time = 0.0
        for case_id in self.registry.list_case_ids():
            state = self.registry.get(case_id)
            if state.last_opened and state.last_opened > best_time:
                best_time = state.last_opened
                best_case = case_id
        if not best_case:
            return
        try:
            state = self.registry.get(best_case)
            if state and getattr(state, "window_level", None):
                self.driver.window_level = dict(state.window_level)
            else:
                self.driver.window_level = None
            self.driver.ensure_case_volumes(best_case)
            try:
                load_saved_segmentations(best_case)
            except Exception:
                pass
            self.workflowPanel.sync_from_case(best_case)
            self.caseManagerPanel.refresh()
        except Exception:
            pass


class CustomPanelV2Logic(ScriptedLoadableModuleLogic):
    pass


class CustomPanel(CustomPanelV2):
    pass


class CustomPanelWidget(CustomPanelV2Widget):
    pass


class CustomPanelLogic(CustomPanelV2Logic):
    pass
