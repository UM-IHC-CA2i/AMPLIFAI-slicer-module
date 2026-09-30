import csv
import math
import os
import time

import ctk
import qt
import slicer
import slicer.util as su
import vtk

from data.config import get_config_value, resolve_config_path
from data.loader import DriverState, LOADER_ATTR
from segmentation.services import (
    clear_segmentation_contents,
    copy_segmentation_contents,
    ensure_feature_segmentation_node,
    find_feature_segmentation_node,
    has_unsaved_segmentations,
    list_segmentation_nodes,
    find_ai_segmentation_path,
    load_saved_segmentations,
    load_ai_segmentation_into_node,
    save_active_segmentation,
    save_all_segmentations,
    segment_has_voxels,
)
from state.registry import CaseRegistry
from workflow.controller import WorkflowController, WorkflowContext
from workflow.steps import (
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
    WORKFLOW_STEPS,
)


class CaseManagerPanel(qt.QWidget):
    def __init__(self, parent=None, driver=None, registry=None, on_case_opened=None):
        super().__init__(parent)
        self.driver = driver or DriverState()
        self.registry = registry or CaseRegistry()
        self._on_case_opened = on_case_opened
        self._selected_case_id = None
        self._notes_timer = qt.QTimer()
        self._notes_timer.setSingleShot(True)
        self._notes_timer.timeout.connect(self._save_notes)
        self._refresh_timer = qt.QTimer()
        self._refresh_timer.setInterval(60 * 1000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self._deferred_refresh_timer = qt.QTimer()
        self._deferred_refresh_timer.setSingleShot(True)
        self._deferred_refresh_timer.timeout.connect(self.refresh)
        self._updating_list = False
        self._expanded_groups = set()
        self._section_expanded = {}
        self._suppress_selection_change = False
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = qt.QVBoxLayout(self)
        self._build_summary_widget()
        self.collapsible = ctk.ctkCollapsibleButton()
        self.collapsible.setText("Case Manager")
        self.collapsible.collapsed = True
        layout.addWidget(self.collapsible)

        panel = qt.QVBoxLayout(self.collapsible)

        button_row = qt.QHBoxLayout()
        self.openLastButton = qt.QPushButton("Open Last Case")
        self.openLastButton.clicked.connect(self._on_open_last)
        button_row.addWidget(self.openLastButton)
        self.refreshButton = qt.QPushButton("Refresh")
        self.refreshButton.clicked.connect(self.refresh)
        button_row.addWidget(self.refreshButton)
        button_row.addStretch(1)
        panel.addLayout(button_row)

        filter_row = qt.QHBoxLayout()
        self.flaggedFilter = qt.QCheckBox("Flagged")
        self.flaggedFilter.stateChanged.connect(self.refresh)
        filter_row.addWidget(self.flaggedFilter)
        self.notesFilter = qt.QCheckBox("Has Notes")
        self.notesFilter.stateChanged.connect(self.refresh)
        filter_row.addWidget(self.notesFilter)
        filter_row.addStretch(1)
        panel.addLayout(filter_row)

        main_row = qt.QHBoxLayout()
        self.caseList = qt.QTreeWidget()
        self.caseList.setColumnCount(5)
        self.caseList.setHeaderLabels(["Case", "Status", "Step", "Flag", "Notes"])
        header = self.caseList.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, qt.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, qt.QHeaderView.Fixed)
        header.setSectionResizeMode(2, qt.QHeaderView.Fixed)
        header.setSectionResizeMode(3, qt.QHeaderView.Fixed)
        header.setSectionResizeMode(4, qt.QHeaderView.Stretch)
        header.resizeSection(1, 90)
        header.resizeSection(2, 60)
        header.resizeSection(3, 45)
        self.caseList.setMinimumWidth(520)
        self._case_list_min_width = self.caseList.minimumWidth
        self.caseList.itemSelectionChanged.connect(self._on_selection_changed)
        self.caseList.itemChanged.connect(self._on_item_changed)
        self.caseList.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.caseList.itemExpanded.connect(self._on_section_expansion_changed)
        self.caseList.itemCollapsed.connect(self._on_section_expansion_changed)
        self.caseList.setMinimumHeight(360)
        main_row.addWidget(self.caseList, 3)

        notes_layout = qt.QVBoxLayout()
        notes_label = qt.QLabel("Notes")
        notes_label.setStyleSheet("font-weight: 600;")
        notes_layout.addWidget(notes_label)
        self.notesEdit = qt.QPlainTextEdit()
        self.notesEdit.setPlaceholderText("Select a case to edit notes.")
        self.notesEdit.textChanged.connect(self._on_notes_changed)
        notes_layout.addWidget(self.notesEdit, 1)
        notes_container = qt.QWidget()
        notes_container.setLayout(notes_layout)
        self._notes_container = notes_container
        self._notes_container_min_width = notes_container.minimumWidth
        main_row.addWidget(notes_container, 2)

        panel.addLayout(main_row)
        self._wire_case_manager_collapse()

    def _wire_case_manager_collapse(self):
        def _connect(signal_name):
            try:
                self.collapsible.connect(signal_name, self._on_case_manager_collapsed)
                return True
            except Exception:
                return False

        connected = False
        try:
            self.collapsible.collapsedChanged.connect(self._on_case_manager_collapsed)
            connected = True
        except Exception:
            connected = False
        if not connected:
            connected = _connect("collapsedChanged(bool)")
        if not connected:
            connected = _connect("toggled(bool)")
        self._on_case_manager_collapsed(bool(self.collapsible.collapsed))

    def _on_case_manager_collapsed(self, collapsed):
        try:
            collapsed = bool(collapsed)
        except Exception:
            collapsed = False
        if collapsed:
            self.caseList.setMinimumWidth(0)
            if hasattr(self, "_notes_container"):
                self._notes_container.setMinimumWidth(0)
        else:
            min_width = self._case_list_min_width or 0
            self.caseList.setMinimumWidth(min_width)
            if hasattr(self, "_notes_container"):
                notes_width = self._notes_container_min_width or 0
                self._notes_container.setMinimumWidth(notes_width)
        try:
            self.caseList.updateGeometry()
            self.updateGeometry()
        except Exception:
            pass

    def _labels_csv_path(self):
        raw = get_config_value("labels_csv")
        return resolve_config_path(raw)

    def _build_summary_widget(self):
        self.summaryWidget = qt.QWidget()
        wrapper = qt.QVBoxLayout(self.summaryWidget)
        wrapper.setContentsMargins(0, 0, 0, 0)
        status_row = qt.QHBoxLayout()
        self.currentCaseLabel = qt.QLabel("<b>Current</b>: -")
        self._summarySep1 = qt.QLabel("|")
        self.currentStatusLabel = qt.QLabel("<b>Status</b>: -")
        self._summarySep2 = qt.QLabel("|")
        self.completedLabel = qt.QLabel("<b>Completed</b>: 0/0")
        status_row.addWidget(self.currentCaseLabel)
        status_row.addWidget(self._summarySep1)
        status_row.addWidget(self.currentStatusLabel)
        status_row.addWidget(self._summarySep2)
        status_row.addWidget(self.completedLabel)
        self.summaryFlag = qt.QCheckBox("Flag case")
        self.summaryFlag.toggled.connect(self._on_summary_flag_toggled)
        status_row.addWidget(self.summaryFlag)
        status_row.addStretch(1)

        notes_row = qt.QHBoxLayout()
        notes_label = qt.QLabel("Notes")
        notes_label.setMinimumWidth(40)
        notes_row.addWidget(notes_label)
        self.summaryNotes = qt.QLineEdit()
        self.summaryNotes.setPlaceholderText("Add notes for current case...")
        self.summaryNotes.textChanged.connect(self._on_summary_notes_changed)
        notes_row.addWidget(self.summaryNotes, 1)
        wrapper.addLayout(status_row)
        wrapper.addLayout(notes_row)

        self._summary_sync_guard = False
        self._notes_sync_guard = False

    def summary_widget(self):
        return self.summaryWidget

    def _load_completed_cases(self):
        path = self._labels_csv_path()
        completed = set()
        if not path:
            return completed
        try:
            with open(path, newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    case_id = (row.get("case_id") or "").strip()
                    if case_id:
                        completed.add(case_id)
        except Exception:
            return completed
        return completed

    def _case_state(self, case_id):
        return self.registry.get(case_id)

    def _case_status(self, case_id, completed_set, seen_case_ids):
        if case_id in completed_set:
            return "Completed"
        if case_id in seen_case_ids:
            return "In Progress"
        return "Unseen"

    def _format_last_opened(self, timestamp):
        if not timestamp:
            return ""
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
        except Exception:
            return ""

    def _case_matches_filters(self, state):
        if self.flaggedFilter.isChecked() and not state.flagged:
            return False
        if self.notesFilter.isChecked() and not (state.notes or "").strip():
            return False
        return True

    def _capture_section_expansion(self):
        root = self.caseList.invisibleRootItem()
        for index in range(root.childCount()):
            item = root.child(index)
            meta = item.data(0, qt.Qt.UserRole)
            if not isinstance(meta, dict) or meta.get("type") != "group":
                continue
            label = meta.get("group")
            if label:
                self._section_expanded[label] = item.isExpanded()

    def _on_section_expansion_changed(self, item):
        if self._updating_list:
            return
        meta = item.data(0, qt.Qt.UserRole)
        if not isinstance(meta, dict) or meta.get("type") != "group":
            return
        label = meta.get("group")
        if label:
            self._section_expanded[label] = item.isExpanded()

    def refresh(self):
        if self._updating_list:
            return
        self._updating_list = True
        current_id = self._selected_case_id
        current_notes = self.notesEdit.toPlainText()
        self._suppress_selection_change = True
        try:
            self._capture_section_expansion()
            self.caseList.clear()
            completed = self._load_completed_cases()
            try:
                self.driver.cases = []
            except Exception:
                pass
            all_cases = self.driver.list_cases()
            seen_cases = set(self.registry.list_case_ids())

            group_order = ["Reannotate", "In Progress", "Unseen", "Completed"]
            grouped = {label: [] for label in group_order}
            for case_id in all_cases:
                state = self._case_state(case_id) if case_id in seen_cases else None
                if state is None:
                    state = self.registry.get(case_id)
                if not self._case_matches_filters(state):
                    continue
                if state.reannotation:
                    grouped["Reannotate"].append((case_id, state))
                    continue
                status = self._case_status(case_id, completed, seen_cases)
                grouped[status].append((case_id, state))

            for status in group_order:
                items = grouped[status]
                items.sort(key=lambda item: (not item[1].flagged, item[0]))
                self._add_group(status, items)
            if current_id:
                item = self._find_case_item(current_id)
                if item:
                    item.setSelected(True)
                    self.caseList.setCurrentItem(item)
                    self._selected_case_id = current_id
                    self.notesEdit.blockSignals(True)
                    self.notesEdit.setPlainText(current_notes)
                    self.notesEdit.blockSignals(False)
            self._update_current_case_summary(completed, all_cases, seen_cases)
        finally:
            self._updating_list = False
            self._suppress_selection_change = False

    def _add_group(self, label, items):
        group_item = qt.QTreeWidgetItem(self.caseList, [f"{label} ({len(items)})"])
        group_item.setFirstColumnSpanned(True)
        group_item.setData(0, qt.Qt.UserRole, {"type": "group", "group": label})
        group_item.setExpanded(self._section_expanded.get(label, True))
        limit = 25
        show_all = label in self._expanded_groups
        visible = items if show_all else items[:limit]
        for case_id, state in visible:
            self._add_case_item(group_item, case_id, state, label)
        if not show_all and len(items) > limit:
            more_item = qt.QTreeWidgetItem(group_item, [f"Show all {len(items)} cases..."])
            more_item.setFirstColumnSpanned(True)
            more_item.setData(0, qt.Qt.UserRole, {"type": "more", "group": label})

    def _add_case_item(self, parent, case_id, state, status_label):
        step_label = ""
        if status_label == "In Progress":
            step_label = self._format_step_progress(state.current_step)
        notes_label = "Yes" if (state.notes or "").strip() else ""
        item = qt.QTreeWidgetItem(
            parent,
            [case_id, status_label, step_label, "", notes_label],
        )
        item.setData(0, qt.Qt.UserRole, {"type": "case", "case_id": case_id})
        item.setFlags(item.flags() | qt.Qt.ItemIsUserCheckable | qt.Qt.ItemIsSelectable | qt.Qt.ItemIsEnabled)
        item.setCheckState(3, qt.Qt.Checked if state.flagged else qt.Qt.Unchecked)
        return item

    def _format_step_progress(self, step_idx):
        step_labels = {
            STEP_LESION: "1",
            STEP_LIVER: "2",
            STEP_TIV: "3",
            STEP_APHE: "4",
            STEP_WASHOUT: "5",
            STEP_WASHOUT_VENOUS: "5.1",
            STEP_WASHOUT_DELAYED: "5.2",
            STEP_CAPSULE: "6",
            STEP_CAPSULE_VENOUS: "6.1",
            STEP_CAPSULE_DELAYED: "6.2",
            STEP_FINISH: "7",
        }
        label = step_labels.get(int(step_idx), str(int(step_idx) + 1))
        return f"Step {label}/7"

    def _on_item_changed(self, item, column):
        if self._updating_list:
            return
        meta = item.data(0, qt.Qt.UserRole)
        if not isinstance(meta, dict) or meta.get("type") != "case":
            return
        if column != 3:
            return
        case_id = meta.get("case_id")
        if not case_id:
            return
        state = self.registry.get(case_id)
        state.flagged = item.checkState(3) == qt.Qt.Checked
        self.registry.save(state)
        self._schedule_refresh()

    def _on_selection_changed(self):
        if self._suppress_selection_change:
            return
        items = self.caseList.selectedItems()
        if not items:
            self._selected_case_id = None
            self.notesEdit.blockSignals(True)
            self.notesEdit.setPlainText("")
            self.notesEdit.blockSignals(False)
            return
        item = items[0]
        meta = item.data(0, qt.Qt.UserRole)
        if not isinstance(meta, dict) or meta.get("type") != "case":
            return
        case_id = meta.get("case_id")
        self._selected_case_id = case_id
        state = self.registry.get(case_id)
        self.notesEdit.blockSignals(True)
        self.notesEdit.setPlainText(state.notes or "")
        self.notesEdit.blockSignals(False)

    def _on_item_double_clicked(self, item, _column):
        meta = item.data(0, qt.Qt.UserRole)
        if not isinstance(meta, dict):
            return
        if meta.get("type") == "more":
            group = meta.get("group")
            if group:
                self._expanded_groups.add(group)
                self.refresh()
            return
        if meta.get("type") == "case":
            self._open_case(meta.get("case_id"))

    def _on_open_selected(self):
        items = self.caseList.selectedItems()
        if not items:
            slicer.util.errorDisplay("Select a case to open.")
            return
        meta = items[0].data(0, qt.Qt.UserRole)
        if not isinstance(meta, dict) or meta.get("type") != "case":
            slicer.util.errorDisplay("Select a case to open.")
            return
        self._open_case(meta.get("case_id"))

    def _on_open_last(self):
        last_case = self._last_opened_case_id()
        if not last_case:
            slicer.util.errorDisplay("No previously opened case found.")
            return
        self._open_case(last_case)

    def _last_opened_case_id(self):
        best_case = None
        best_time = 0.0
        current = self.driver.current_case
        for case_id in self.registry.list_case_ids():
            if current and str(case_id) == str(current):
                continue
            state = self.registry.get(case_id)
            if state.last_opened > best_time:
                best_time = state.last_opened
                best_case = case_id
        return best_case

    def _on_notes_changed(self):
        if self._notes_sync_guard:
            return
        if not self._selected_case_id:
            return
        current_case = getattr(self.driver, "current_case", None)
        if hasattr(self, "summaryNotes") and current_case == self._selected_case_id:
            self._notes_sync_guard = True
            try:
                self.summaryNotes.setText(self.notesEdit.toPlainText())
            finally:
                self._notes_sync_guard = False
        self._save_notes()

    def _save_notes(self):
        case_id = self._selected_case_id
        if not case_id:
            return
        state = self.registry.get(case_id)
        state.notes = self.notesEdit.toPlainText()
        self.registry.save(state)
        if self.notesFilter.isChecked():
            self._schedule_refresh()
        else:
            self._update_case_item_notes(case_id, bool((state.notes or "").strip()))

    def _confirm_switch(self):
        if not has_unsaved_segmentations():
            return True
        case_id = self.driver.current_case
        ok = save_all_segmentations(case_id=case_id)
        if not ok:
            slicer.util.errorDisplay("Save failed. Finish the current case before switching.")
            return False
        return True

    def _open_case(self, case_id):
        if not case_id:
            return
        if not self._confirm_switch():
            return
        try:
            self.clear_scene_for_case(case_id)
            state = self.registry.get(case_id)
            if state and getattr(state, "window_level", None):
                self.driver.window_level = dict(state.window_level)
            else:
                self.driver.window_level = None
            self.driver.ensure_case_volumes(case_id)
            try:
                load_saved_segmentations(case_id)
            except Exception:
                pass
            state.touch()
            self.registry.save(state)
            self._schedule_refresh(0)
            if callable(self._on_case_opened):
                self._on_case_opened(case_id)
        except Exception as exc:
            slicer.util.errorDisplay(str(exc))

    def clear_scene_for_case(self, case_id):
        case_id = str(case_id)
        try:
            volumes = slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")
        except Exception:
            volumes = []
        for node in volumes:
            if not node:
                continue
            try:
                if not node.GetAttribute(LOADER_ATTR):
                    continue
            except Exception:
                continue
            try:
                name = node.GetName() or ""
            except Exception:
                name = ""
            if name.startswith(f"{case_id}_"):
                continue
            try:
                slicer.mrmlScene.RemoveNode(node)
            except Exception:
                pass
        try:
            segs = slicer.util.getNodesByClass("vtkMRMLSegmentationNode")
        except Exception:
            segs = []
        for seg in segs:
            if not seg:
                continue
            try:
                seg_case = seg.GetAttribute("AMLIFAI_CASE_ID")
            except Exception:
                seg_case = None
            if seg_case and str(seg_case) == case_id:
                continue
            try:
                slicer.mrmlScene.RemoveNode(seg)
            except Exception:
                pass

    def _schedule_refresh(self, delay_ms=150):
        self._deferred_refresh_timer.stop()
        self._deferred_refresh_timer.start(max(0, int(delay_ms)))

    def _find_case_item(self, case_id):
        root = self.caseList.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                item = group.child(j)
                meta = item.data(0, qt.Qt.UserRole)
                if isinstance(meta, dict) and meta.get("type") == "case":
                    if meta.get("case_id") == case_id:
                        return item
        return None

    def _update_current_case_summary(self, completed_set, all_cases, seen_case_ids):
        current_case = getattr(self.driver, "current_case", None)
        if current_case:
            self.currentCaseLabel.setText(f"<b>Current</b>: {current_case}")
            status = self._case_status(str(current_case), completed_set, seen_case_ids)
            status_label = "Complete" if status == "Completed" else "Incomplete"
            status_color = "#2e7d32" if status == "Completed" else "#c62828"
            self.currentStatusLabel.setText(
                f"<b>Status</b>: <span style='color:{status_color};'>{status_label}</span>"
            )
            state = self.registry.get(current_case)
            self._summary_sync_guard = True
            try:
                self.summaryFlag.setChecked(bool(state.flagged))
                self.summaryNotes.setText(state.notes or "")
            finally:
                self._summary_sync_guard = False
        else:
            self.currentCaseLabel.setText("<b>Current</b>: -")
            self.currentStatusLabel.setText("<b>Status</b>: -")
            self._summary_sync_guard = True
            try:
                self.summaryFlag.setChecked(False)
                self.summaryNotes.setText("")
            finally:
                self._summary_sync_guard = False
        total = len(all_cases or [])
        completed_count = len(completed_set or [])
        self.completedLabel.setText(f"<b>Completed</b>: {completed_count}/{total}")

    def _on_summary_flag_toggled(self, checked):
        if self._summary_sync_guard:
            return
        case_id = getattr(self.driver, "current_case", None)
        if not case_id:
            return
        state = self.registry.get(case_id)
        state.flagged = bool(checked)
        self.registry.save(state)
        self._schedule_refresh()

    def _on_summary_notes_changed(self):
        if self._summary_sync_guard or self._notes_sync_guard:
            return
        case_id = getattr(self.driver, "current_case", None)
        if not case_id:
            return
        state = self.registry.get(case_id)
        try:
            text_attr = self.summaryNotes.text
            notes_value = text_attr() if callable(text_attr) else text_attr
        except Exception:
            notes_value = ""
        if getattr(self, "_selected_case_id", None) == case_id and hasattr(self, "notesEdit"):
            self._notes_sync_guard = True
            try:
                self.notesEdit.setPlainText(notes_value)
            finally:
                self._notes_sync_guard = False
        state.notes = notes_value
        self.registry.save(state)
        if self.notesFilter.isChecked():
            self._schedule_refresh()
        else:
            self._update_case_item_notes(case_id, bool((state.notes or "").strip()))

    def _update_case_item_notes(self, case_id, has_notes):
        item = self._find_case_item(case_id)
        if not item:
            return
        item.setText(4, "Yes" if has_notes else "")


class WorkflowPanel(qt.QWidget):
    def __init__(self, parent=None, driver=None, registry=None, on_step_changed=None):
        super().__init__(parent)
        self.driver = driver or DriverState()
        self.registry = registry or CaseRegistry()
        self._on_step_changed = on_step_changed
        self._workflow_step = 0
        self._seg_source_block = False
        self._seg_source_last_key = None
        self._build_ui()
        self._sync_from_current_case()

    def _build_ui(self):
        layout = qt.QVBoxLayout(self)
        header = qt.QLabel("Workflow")
        header.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(header)

        self.workflowHeader = qt.QLabel("")
        self.workflowHeader.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self.workflowHeader)

        self.workflowStack = qt.QStackedWidget()
        layout.addWidget(self.workflowStack)

        self._build_step_panels()


        self._build_segment_editor(layout)
        self._wire_workflow_handlers()

        button_row = qt.QHBoxLayout()
        self.backButton = qt.QPushButton("Back")
        self.backButton.clicked.connect(self._on_back)
        button_row.addWidget(self.backButton)
        self.nextButton = qt.QPushButton("Continue")
        self.nextButton.clicked.connect(self._on_next)
        button_row.addWidget(self.nextButton)
        button_row.addStretch(1)
        layout.addLayout(button_row)

    def _make_checkable_button(self, text, value=None):
        btn = qt.QPushButton(text)
        btn.setCheckable(True)
        btn.setProperty("value", value if value is not None else text)
        btn.setSizePolicy(qt.QSizePolicy.Maximum, qt.QSizePolicy.Fixed)
        btn.setStyleSheet(
            "QPushButton {"
            " padding: 4px 10px;"
            " border: 1px solid #c8c8c8;"
            " border-radius: 4px;"
            " background-color: #f5f5f5;"
            "}"
            "QPushButton:hover { background-color: #eeeeee; }"
            "QPushButton:checked {"
            " background-color: #2e7d32;"
            " border-color: #1b5e20;"
            " color: white;"
            " font-weight: 600;"
            "}"
            "QPushButton:checked:hover { background-color: #1b5e20; }"
        )
        return btn

    def _build_button_group(self, rows, exclusive=True):
        widget = qt.QWidget()
        layout = qt.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        group = qt.QButtonGroup(widget)
        group.setExclusive(bool(exclusive))
        group.setProperty("exclusive_default", bool(exclusive))
        for row in rows:
            row_layout = qt.QHBoxLayout()
            row_layout.setContentsMargins(0, 0, 0, 0)
            for value in row:
                btn = self._make_checkable_button(str(value))
                row_layout.addWidget(btn)
                group.addButton(btn)
            row_layout.addStretch(1)
            layout.addLayout(row_layout)
        return widget, group

    def _annotation_value(self, group):
        btn = group.checkedButton()
        if not btn:
            return ""
        val = btn.property("value")
        return val if val is not None else self._button_label(btn)

    def _group_checked_values(self, group):
        if not group:
            return []
        values = []
        for btn in group.buttons():
            try:
                checked = bool(btn.isChecked())
            except Exception:
                checked = False
            if not checked:
                continue
            val = btn.property("value")
            values.append(str(val if val is not None else self._button_label(btn)))
        return values

    def _parse_multi_value(self, value):
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            raw_values = [str(v) for v in value]
        else:
            raw_values = str(value).split("|")
        cleaned = [v.strip() for v in raw_values if str(v).strip()]
        seen = set()
        ordered = []
        for item in cleaned:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    def _liver_segment_order(self):
        return {
            "S1": 1,
            "S2": 2,
            "S3": 3,
            "S4A": 4,
            "S4B": 5,
            "S5": 6,
            "S6": 7,
            "S7": 8,
            "S8": 9,
            "S4": 4,  # legacy value before S4A/S4B split
        }

    def _normalize_liver_segment_for_ui(self, value):
        segments = self._parse_multi_value(value)
        normalized = []
        for item in segments:
            if item == "S4":
                normalized.extend(["S4A", "S4B"])
            else:
                normalized.append(item)
        return "|".join(normalized)

    def _liver_segments_value(self):
        values = self._group_checked_values(self.segmentGroup)
        order = self._liver_segment_order()
        values = sorted(values, key=lambda item: order.get(str(item), 999))
        return "|".join(values)

    def _button_label(self, btn):
        if not btn:
            return ""
        text_attr = getattr(btn, "text", "")
        return text_attr() if callable(text_attr) else text_attr

    def _group_exclusive_default(self, group):
        if not group:
            return True
        try:
            prop = group.property("exclusive_default")
            if prop is not None:
                return bool(prop)
        except Exception:
            pass
        try:
            return bool(group.exclusive())
        except Exception:
            return True

    def _set_group_value(self, group, value):
        if not group:
            return
        is_exclusive = self._group_exclusive_default(group)
        if not is_exclusive:
            selected = set(self._parse_multi_value(value))
            try:
                group.setExclusive(False)
            except Exception:
                pass
            for btn in group.buttons():
                label = str(self._button_label(btn))
                try:
                    btn.setChecked(label in selected)
                except Exception:
                    pass
            try:
                group.setExclusive(is_exclusive)
            except Exception:
                pass
            return
        for btn in group.buttons():
            label = self._button_label(btn)
            if str(label) == str(value):
                btn.setChecked(True)
                return

    def _clear_button_group(self, group):
        if not group:
            return
        default_exclusive = self._group_exclusive_default(group)
        try:
            group.setExclusive(False)
        except Exception:
            pass
        for btn in group.buttons():
            try:
                btn.setChecked(False)
            except Exception:
                pass
        try:
            group.setExclusive(default_exclusive)
        except Exception:
            pass

    def _build_step_panels(self):
        # Step 1: Lesion segmentation
        self.stepLesion = qt.QWidget()
        lesion_layout = qt.QVBoxLayout()
        lesion_layout.addWidget(qt.QLabel("Segment the lesion for this phase."))
        self.btnSkipNoLesion = qt.QPushButton("No lesion, skip to end")
        lesion_layout.addWidget(self.btnSkipNoLesion)
        lesion_layout.addStretch(1)
        self.stepLesion.setLayout(lesion_layout)
        self.workflowStack.addWidget(self.stepLesion)

        # Step 2: Liver segment + size
        self.stepLiver = qt.QWidget()
        liver_form = qt.QFormLayout()
        seg_widget, self.segmentGroup = self._build_button_group(
            [["S1", "S2", "S3", "S4A", "S4B"], ["S5", "S6", "S7", "S8"]],
            exclusive=False,
        )
        liver_form.addRow("Liver Segment", seg_widget)
        self.diameterSpinBox = ctk.ctkDoubleSpinBox()
        self.diameterSpinBox.decimals = 1
        self.diameterSpinBox.minimum = 0.0
        self.diameterSpinBox.maximum = 1000.0
        self.diameterSpinBox.singleStep = 1.0
        self.diameterSpinBox.suffix = " mm"
        liver_form.addRow("Max diameter", self.diameterSpinBox)
        self.btnAutoDiameter = qt.QPushButton("Auto-fill diameter from lesion")
        liver_form.addRow("", self.btnAutoDiameter)
        self.stepLiver.setLayout(liver_form)
        self.workflowStack.addWidget(self.stepLiver)

        # Step 3: TIV + involved vessel
        self.stepTiv = qt.QWidget()
        tiv_form = qt.QFormLayout()
        tv_widget, self.tumorVeinGroup = self._build_button_group([["Absent", "Present"]])
        tiv_form.addRow("Tumor in Vein", tv_widget)
        vessel_widget, self.vesselGroup = self._build_button_group([["Portal", "Hepatic", "IVC"]])
        self.vesselWidget = vessel_widget
        self.vesselWidget.setEnabled(False)
        tiv_form.addRow("Involved vessel", self.vesselWidget)
        self.btnSkipToEnd = qt.QPushButton("Skip to end (TIV)")
        self.btnSkipToEnd.setEnabled(False)
        tiv_form.addRow("", self.btnSkipToEnd)
        self.stepTiv.setLayout(tiv_form)
        self.workflowStack.addWidget(self.stepTiv)

        # Step 4: APHE
        self.stepAphe = qt.QWidget()
        aphe_form = qt.QFormLayout()
        aphe_widget, self.apheGroup = self._build_button_group(
            [["Absent", "Non-rim APHE", "Rim APHE"]]
        )
        aphe_form.addRow("APHE", aphe_widget)
        aphe_form.addRow("", qt.QLabel("If present, segment APHE using the editor below."))
        self.stepAphe.setLayout(aphe_form)
        self.workflowStack.addWidget(self.stepAphe)

        # Step 5: Washout
        self.stepWashout = qt.QWidget()
        washout_form = qt.QFormLayout()
        wo_widget, self.washoutGroup = self._build_button_group([["Absent", "Present"]])
        washout_form.addRow("Washout (non-peripheral)", wo_widget)
        self.washoutPhaseLabel = qt.QLabel("Washout phase")
        self.washoutVenousCheck = qt.QCheckBox("Present on venous")
        self.washoutDelayedCheck = qt.QCheckBox("Present on delayed")
        self.washoutPhaseWidget = qt.QWidget()
        washout_phase_layout = qt.QVBoxLayout()
        washout_phase_layout.setContentsMargins(0, 0, 0, 0)
        washout_phase_layout.addWidget(self.washoutVenousCheck)
        washout_phase_layout.addWidget(self.washoutDelayedCheck)
        self.washoutPhaseWidget.setLayout(washout_phase_layout)
        washout_form.addRow(self.washoutPhaseLabel, self.washoutPhaseWidget)
        self.washoutSegHint = qt.QLabel("If present, segment washout using the editor below.")
        washout_form.addRow("", self.washoutSegHint)
        self.stepWashout.setLayout(washout_form)
        self.workflowStack.addWidget(self.stepWashout)

        # Step 5.1: Washout - Venous
        self.stepWashoutVenous = qt.QWidget()
        washout_venous_form = qt.QFormLayout()
        washout_venous_form.addRow("", qt.QLabel("Segment washout on venous phase using the editor below."))
        self.stepWashoutVenous.setLayout(washout_venous_form)
        self.workflowStack.addWidget(self.stepWashoutVenous)

        # Step 5.2: Washout - Delayed
        self.stepWashoutDelayed = qt.QWidget()
        washout_delayed_form = qt.QFormLayout()
        washout_delayed_form.addRow("", qt.QLabel("Segment washout on delayed phase using the editor below."))
        self.stepWashoutDelayed.setLayout(washout_delayed_form)
        self.workflowStack.addWidget(self.stepWashoutDelayed)

        # Step 6: Capsule
        self.stepCapsule = qt.QWidget()
        capsule_form = qt.QFormLayout()
        cap_widget, self.capsuleGroup = self._build_button_group([["Absent", "Present"]])
        capsule_form.addRow("Enhancing capsule", cap_widget)
        self.capsulePhaseLabel = qt.QLabel("Capsule phase")
        self.capsuleVenousCheck = qt.QCheckBox("Present on venous")
        self.capsuleDelayedCheck = qt.QCheckBox("Present on delayed")
        self.capsulePhaseWidget = qt.QWidget()
        capsule_phase_layout = qt.QVBoxLayout()
        capsule_phase_layout.setContentsMargins(0, 0, 0, 0)
        capsule_phase_layout.addWidget(self.capsuleVenousCheck)
        capsule_phase_layout.addWidget(self.capsuleDelayedCheck)
        self.capsulePhaseWidget.setLayout(capsule_phase_layout)
        capsule_form.addRow(self.capsulePhaseLabel, self.capsulePhaseWidget)
        self.capsuleSegHint = qt.QLabel("If present, segment capsule using the editor below.")
        capsule_form.addRow("", self.capsuleSegHint)
        self.stepCapsule.setLayout(capsule_form)
        self.workflowStack.addWidget(self.stepCapsule)

        # Step 6.1: Capsule - Venous
        self.stepCapsuleVenous = qt.QWidget()
        capsule_venous_form = qt.QFormLayout()
        capsule_venous_form.addRow("", qt.QLabel("Segment capsule on venous phase using the editor below."))
        self.stepCapsuleVenous.setLayout(capsule_venous_form)
        self.workflowStack.addWidget(self.stepCapsuleVenous)

        # Step 6.2: Capsule - Delayed
        self.stepCapsuleDelayed = qt.QWidget()
        capsule_delayed_form = qt.QFormLayout()
        capsule_delayed_form.addRow("", qt.QLabel("Segment capsule on delayed phase using the editor below."))
        self.stepCapsuleDelayed.setLayout(capsule_delayed_form)
        self.workflowStack.addWidget(self.stepCapsuleDelayed)

        # Step 7: Finish
        self.stepFinish = qt.QWidget()
        finish_form = qt.QFormLayout()
        lirads_widget, self.liradsGroup = self._build_button_group(
            [["LR-1", "LR-2", "LR-3", "LR-4"], ["LR-5", "LR-M", "LR-TIV", "No lesion"]]
        )
        finish_form.addRow("LIRADS score", lirads_widget)
        self.summaryLabel = qt.QLabel("")
        self.summaryLabel.setWordWrap(True)
        finish_form.addRow("Summary", self.summaryLabel)
        self.btnFinishSave = qt.QPushButton("Finish Case")
        self.btnFinishSave.setEnabled(False)
        self.btnFinishSave.setMinimumWidth(260)
        self.btnFinishSave.setMinimumHeight(44)
        self.btnFinishSave.setStyleSheet(
            "QPushButton {"
            " background-color: #2e7d32;"
            " color: white;"
            " font-weight: 600;"
            " font-size: 14px;"
            " padding: 6px 16px;"
            " border-radius: 6px;"
            "}"
            "QPushButton:hover { background-color: #1b5e20; }"
            "QPushButton:pressed { background-color: #0f3d14; }"
            "QPushButton:disabled {"
            " background-color: #c8e6c9;"
            " color: #ffffff;"
            "}"
        )
        finish_form.addRow("", self.btnFinishSave)
        self.stepFinish.setLayout(finish_form)
        self.workflowStack.addWidget(self.stepFinish)

    def _build_segment_editor(self, layout):
        self.segSourceContainer = qt.QGroupBox("Segmentation Source")
        source_layout = qt.QVBoxLayout()
        source_layout.setContentsMargins(6, 6, 6, 6)
        source_row = qt.QHBoxLayout()
        source_label = qt.QLabel("Source")
        source_label.setMinimumWidth(70)
        source_row.addWidget(source_label)
        self.segSourceCombo = qt.QComboBox()
        self.segSourceCombo.setSizeAdjustPolicy(qt.QComboBox.AdjustToContents)
        source_row.addWidget(self.segSourceCombo, 1)
        source_layout.addLayout(source_row)
        self.segSourceStatus = qt.QLabel("Select a source to begin.")
        self.segSourceStatus.setStyleSheet("color: #555555;")
        source_layout.addWidget(self.segSourceStatus)
        preview_row = qt.QHBoxLayout()
        self.segSourcePreview = qt.QCheckBox("Preview")
        self.segSourcePreview.setChecked(True)
        self.segSourcePreview.setVisible(False)
        preview_row.addWidget(self.segSourcePreview)
        preview_row.addStretch(1)
        self.segSourceConfirm = qt.QPushButton("Confirm")
        self.segSourceConfirm.setEnabled(False)
        preview_row.addWidget(self.segSourceConfirm)
        source_layout.addLayout(preview_row)
        self.segSourceContainer.setLayout(source_layout)
        layout.addWidget(self.segSourceContainer)

        self.segEditorWidget = slicer.qMRMLSegmentEditorWidget()
        self.segEditorWidget.setMRMLScene(slicer.mrmlScene)
        if hasattr(self.segEditorWidget, "setAutoShowSourceVolumeNode"):
            try:
                self.segEditorWidget.setAutoShowSourceVolumeNode(False)
            except Exception:
                pass
        elif hasattr(self.segEditorWidget, "setAutoShowSourceVolume"):
            try:
                self.segEditorWidget.setAutoShowSourceVolume(False)
            except Exception:
                pass
        self.segEditorNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentEditorNode")
        self.segEditorWidget.setMRMLSegmentEditorNode(self.segEditorNode)
        self._seg_editor_observer_tag = self.segEditorNode.AddObserver(
            vtk.vtkCommand.ModifiedEvent, self._on_seg_editor_node_modified
        )
        self.segEditorContainer = qt.QGroupBox("Segment Editor")
        seg_layout = qt.QVBoxLayout()
        seg_layout.addWidget(self.segEditorWidget)
        self.segEditorContainer.setLayout(seg_layout)
        layout.addWidget(self.segEditorContainer)

        self.btnSaveSeg = qt.QPushButton("Save segmentation")
        self.btnSaveSeg.setSizePolicy(qt.QSizePolicy.Fixed, qt.QSizePolicy.Fixed)
        self.btnSaveSeg.setMinimumWidth(260)
        self.btnSaveSeg.setMinimumHeight(44)
        save_row = qt.QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self.btnSaveSeg)
        save_row.addStretch(1)
        self.saveSegContainer = qt.QWidget()
        self.saveSegContainer.setLayout(save_row)
        layout.addWidget(self.saveSegContainer)

        self.segSourceCombo.currentIndexChanged.connect(self._on_seg_source_changed)
        self.segSourceConfirm.clicked.connect(self._on_seg_source_confirm)
        self.segSourcePreview.toggled.connect(self._on_seg_source_preview_toggled)

    def _wire_workflow_handlers(self):
        def on_tiv_changed():
            tiv_present = self._annotation_value(self.tumorVeinGroup) == "Present"
            self.vesselWidget.setEnabled(tiv_present)
            if not tiv_present:
                self._clear_button_group(self.vesselGroup)
            self._save_annotation("tumor_in_vein", self._annotation_value(self.tumorVeinGroup))
            self._save_annotation("vessel", self._annotation_value(self.vesselGroup))
            self._update_workflow_controls()

        self.tumorVeinGroup.buttonClicked.connect(on_tiv_changed)

        def on_vessel_changed():
            self._save_annotation("vessel", self._annotation_value(self.vesselGroup))
            self._update_workflow_controls()

        self.vesselGroup.buttonClicked.connect(on_vessel_changed)

        def on_aphe_changed():
            self._save_annotation("aphe", self._annotation_value(self.apheGroup))
            self._apply_workflow_visibility()
            self._update_workflow_controls()

        self.apheGroup.buttonClicked.connect(on_aphe_changed)

        def on_washout_changed():
            self._save_annotation("washout", self._annotation_value(self.washoutGroup))
            self._update_washout_phase_visibility()
            self._apply_workflow_visibility()
            self._update_workflow_controls()

        self.washoutGroup.buttonClicked.connect(on_washout_changed)

        def on_capsule_changed():
            self._save_annotation("capsule", self._annotation_value(self.capsuleGroup))
            self._update_capsule_phase_visibility()
            self._apply_workflow_visibility()
            self._update_workflow_controls()

        self.capsuleGroup.buttonClicked.connect(on_capsule_changed)

        def on_liver_segment_changed():
            self._save_annotation("liver_segment", self._liver_segments_value())
            self._update_workflow_controls()

        self.segmentGroup.buttonClicked.connect(on_liver_segment_changed)

        def on_diameter_changed(_value):
            self._save_annotation("max_diameter_mm", float(self.diameterSpinBox.value))
            self._update_workflow_controls()

        self.diameterSpinBox.valueChanged.connect(on_diameter_changed)

        def on_washout_phase_toggle():
            self._save_selection("washout_venous", bool(self.washoutVenousCheck.isChecked()))
            self._save_selection("washout_delayed", bool(self.washoutDelayedCheck.isChecked()))
            self._apply_workflow_visibility()
            self._update_workflow_controls()

        self.washoutVenousCheck.toggled.connect(on_washout_phase_toggle)
        self.washoutDelayedCheck.toggled.connect(on_washout_phase_toggle)

        def on_capsule_phase_toggle():
            self._save_selection("capsule_venous", bool(self.capsuleVenousCheck.isChecked()))
            self._save_selection("capsule_delayed", bool(self.capsuleDelayedCheck.isChecked()))
            self._apply_workflow_visibility()
            self._update_workflow_controls()

        self.capsuleVenousCheck.toggled.connect(on_capsule_phase_toggle)
        self.capsuleDelayedCheck.toggled.connect(on_capsule_phase_toggle)

        def on_lirads_changed():
            self._save_annotation("lirads", self._annotation_value(self.liradsGroup))
            self._update_workflow_controls()

        self.liradsGroup.buttonClicked.connect(on_lirads_changed)

        self.btnSkipToEnd.clicked.connect(self._on_skip_to_end)
        self.btnSkipNoLesion.clicked.connect(self._on_skip_no_lesion)
        self.btnAutoDiameter.clicked.connect(self._on_auto_diameter)
        self.btnSaveSeg.clicked.connect(self._on_save_segmentation)
        self.btnFinishSave.clicked.connect(self._on_finish_save)

    def _format_step_label(self, idx, name):
        step_labels = {
            STEP_LESION: "1",
            STEP_LIVER: "2",
            STEP_TIV: "3",
            STEP_APHE: "4",
            STEP_WASHOUT: "5",
            STEP_WASHOUT_VENOUS: "5.1",
            STEP_WASHOUT_DELAYED: "5.2",
            STEP_CAPSULE: "6",
            STEP_CAPSULE_VENOUS: "6.1",
            STEP_CAPSULE_DELAYED: "6.2",
            STEP_FINISH: "7",
        }
        return step_labels.get(idx, str(idx + 1))

    def _sync_from_current_case(self):
        case_id = self.driver.current_case
        if not case_id:
            self._set_controls_enabled(False)
            return
        state = self.registry.get(case_id)
        if self._is_unseen_case(state):
            self._reset_workflow_inputs(state)
        idx = max(0, min(int(state.current_step), len(WORKFLOW_STEPS) - 1))
        self._load_state_into_ui(state)
        self._set_workflow_step(idx, save_state=False)
        self._set_controls_enabled(True)

    def sync_from_case(self, case_id):
        if not getattr(self.driver, "current_case", None):
            return
        self._sync_from_current_case()

    def _set_controls_enabled(self, enabled):
        self.backButton.setEnabled(enabled)
        self.nextButton.setEnabled(enabled)
        if hasattr(self, "segSourceContainer"):
            self.segSourceContainer.setEnabled(enabled)
        if hasattr(self, "segEditorContainer"):
            self.segEditorContainer.setEnabled(enabled)

    def _update_header(self, idx):
        total = 7
        name = WORKFLOW_STEPS[idx]
        step_label = self._format_step_label(idx, name)
        self.workflowHeader.setText(f"Step {step_label}/{total}: {name}")
        if hasattr(self, "workflowStack"):
            self.workflowStack.setCurrentIndex(idx)

    def _set_workflow_step(self, idx, save_state=True):
        idx = max(0, min(int(idx), len(WORKFLOW_STEPS) - 1))
        self._workflow_step = idx
        self._update_header(idx)
        if save_state:
            case_id = self.driver.current_case
            if case_id is None:
                return
            state = self.registry.get(case_id)
            state.current_step = int(idx)
            self.registry.save(state)
        self._apply_phase_focus_for_step(idx)
        self._apply_workflow_visibility()
        self._update_workflow_controls()

    def _on_next(self):
        case_id = self.driver.current_case
        if case_id is None:
            return
        state = self.registry.get(case_id)
        current = max(0, min(int(state.current_step), len(WORKFLOW_STEPS) - 1))
        controller = self._controller(state)
        if not controller.workflow_step_complete(current):
            slicer.util.errorDisplay("Complete the current step before continuing.")
            return
        target = controller.next_applicable_step(current)
        if target is None:
            return
        if self._step_requires_segmentation(current) and has_unsaved_segmentations():
            ok = save_all_segmentations(case_id=case_id)
            if not ok:
                slicer.util.errorDisplay("Save failed. Finish the current step before continuing.")
                return
        self._set_workflow_step(target, save_state=True)
        if callable(self._on_step_changed):
            self._on_step_changed(case_id)

    def _on_back(self):
        case_id = self.driver.current_case
        if case_id is None:
            return
        state = self.registry.get(case_id)
        current = max(0, min(int(state.current_step), len(WORKFLOW_STEPS) - 1))
        controller = self._controller(state)
        target = controller.prev_applicable_step(current)
        if target is None:
            return
        self._set_workflow_step(target, save_state=True)
        if callable(self._on_step_changed):
            self._on_step_changed(case_id)

    def _on_skip_to_end(self):
        case_id = self.driver.current_case
        if case_id is None:
            return
        self._set_lirads_button("LR-TIV")
        self._set_workflow_step(STEP_FINISH, save_state=True)
        if callable(self._on_step_changed):
            self._on_step_changed(case_id)

    def _on_skip_no_lesion(self):
        case_id = self.driver.current_case
        if case_id is None:
            return
        self._set_lirads_button("No lesion")
        self._set_workflow_step(STEP_FINISH, save_state=True)
        if callable(self._on_step_changed):
            self._on_step_changed(case_id)

    def _on_save_segmentation(self):
        case_id = self.driver.current_case
        if case_id is None:
            slicer.util.errorDisplay("No case loaded.")
            return
        seg_node = self.segEditorWidget.segmentationNode()
        if not seg_node:
            slicer.util.errorDisplay("No segmentation selected.")
            return
        path = save_active_segmentation(seg_node, case_id=case_id)
        if not path:
            slicer.util.errorDisplay("Save failed.")
            return
        slicer.util.infoDisplay(f"Saved segmentation to {path}")
        self._update_workflow_controls()

    def _on_auto_diameter(self):
        seg = self._seg_node_for("lesion", phase_id=self.driver.current_phase)
        if not seg:
            slicer.util.errorDisplay("Segment the lesion before measuring diameter.")
            return
        btn_text = None
        try:
            btn_text = self.btnAutoDiameter.text
            self.btnAutoDiameter.setEnabled(False)
            self.btnAutoDiameter.setText("Computing diameter...")
            qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)
            qt.QApplication.processEvents()
            diameter = self.compute_segment_max_diameter_mm(seg)
        finally:
            try:
                qt.QApplication.restoreOverrideCursor()
            except Exception:
                pass
            self.btnAutoDiameter.setEnabled(True)
            if btn_text:
                try:
                    label = btn_text() if callable(btn_text) else btn_text
                except Exception:
                    label = "Auto-fill diameter from lesion"
                self.btnAutoDiameter.setText(label)
        if diameter is None:
            slicer.util.errorDisplay(
                "Could not compute diameter. Ensure the segmentation has a closed surface."
            )
            return
        self.diameterSpinBox.value = round(float(diameter), 1)
        self._save_annotation("max_diameter_mm", float(self.diameterSpinBox.value))
        slicer.util.infoDisplay(f"Estimated maximum diameter: {diameter:.1f} mm")

    def _on_finish_save(self):
        case_id = self.driver.current_case
        if case_id is None:
            slicer.util.errorDisplay("No case loaded.")
            return
        state = self.registry.get(case_id)
        controller = self._controller(state)
        if not controller.workflow_step_complete(STEP_FINISH):
            slicer.util.errorDisplay("Complete required fields before saving.")
            return
        ok = save_all_segmentations(case_id=case_id)
        if not ok:
            slicer.util.errorDisplay("Save failed.")
            return
        csv_ok = self._write_labels_csv_row(case_id, state)
        if not csv_ok:
            slicer.util.errorDisplay("Failed to update labels.csv.")
            return
        state.reannotation = False
        self.registry.save(state)
        slicer.util.infoDisplay(f"Final save completed for {case_id}.")
        if callable(self._on_step_changed):
            self._on_step_changed(case_id)

    def _labels_csv_path(self):
        raw = get_config_value("labels_csv")
        return resolve_config_path(raw)

    def _seg_storage_path(self, seg_node):
        if not seg_node:
            return ""
        try:
            storage = seg_node.GetStorageNode()
        except Exception:
            storage = None
        if not storage:
            return ""
        try:
            return storage.GetFileName() or storage.GetURI() or ""
        except Exception:
            return ""

    def _write_labels_csv_row(self, case_id, state):
        path = self._labels_csv_path()
        if not path:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)

        annotations = state.annotations or {}
        selections = state.selections or {}

        lesion_seg = self._seg_node_for("lesion", phase_id=self.driver.current_phase)
        aphe_seg = self._seg_node_for("aphe", phase_id=None)
        washout_venous_seg = self._seg_node_for("washout", phase_id=1)
        washout_delayed_seg = self._seg_node_for("washout", phase_id=2)
        capsule_venous_seg = self._seg_node_for("capsule", phase_id=1)
        capsule_delayed_seg = self._seg_node_for("capsule", phase_id=2)

        volume_path = ""
        if self.driver.current_volume:
            try:
                vol_storage = self.driver.current_volume.GetStorageNode()
            except Exception:
                vol_storage = None
            if vol_storage:
                try:
                    volume_path = vol_storage.GetFileName() or vol_storage.GetURI() or ""
                except Exception:
                    volume_path = ""

        row = {
            "case_id": str(case_id),
            "ct_path": volume_path,
            "tumor_seg_path": self._seg_storage_path(lesion_seg),
            "aphe_seg_path": self._seg_storage_path(aphe_seg),
            "washout_venous_seg_path": self._seg_storage_path(washout_venous_seg),
            "washout_delayed_seg_path": self._seg_storage_path(washout_delayed_seg),
            "capsule_venous_seg_path": self._seg_storage_path(capsule_venous_seg),
            "capsule_delayed_seg_path": self._seg_storage_path(capsule_delayed_seg),
            "phase": str(self.driver.current_phase) if self.driver.current_phase is not None else "",
            "tumor_present": "1" if segment_has_voxels(lesion_seg) else "0",
            "liver_segment": annotations.get("liver_segment", ""),
            "max_diameter_mm": annotations.get("max_diameter_mm", ""),
            "tumor_in_vein": annotations.get("tumor_in_vein", ""),
            "involved_vessel": annotations.get("vessel", ""),
            "aphe": annotations.get("aphe", ""),
            "washout": annotations.get("washout", ""),
            "washout_venous": "1" if selections.get("washout_venous") else "0",
            "washout_delayed": "1" if selections.get("washout_delayed") else "0",
            "enhancing_capsule": annotations.get("capsule", ""),
            "capsule_venous": "1" if selections.get("capsule_venous") else "0",
            "capsule_delayed": "1" if selections.get("capsule_delayed") else "0",
            "lirads_score": annotations.get("lirads", ""),
        }

        header = list(row.keys())
        existing_rows = []
        if os.path.exists(path):
            try:
                with open(path, newline="") as handle:
                    reader = csv.DictReader(handle)
                    if reader.fieldnames:
                        header = list(reader.fieldnames)
                    for item in reader:
                        if item.get("case_id") == str(case_id):
                            continue
                        existing_rows.append(item)
            except Exception:
                existing_rows = []

        existing_rows.append({key: row.get(key, "") for key in header})
        try:
            with open(path, "w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=header)
                writer.writeheader()
                for item in existing_rows:
                    writer.writerow({key: item.get(key, "") for key in header})
            return True
        except Exception:
            return False

    def _on_seg_editor_node_modified(self, _caller=None, _event=None):
        try:
            self._update_workflow_controls()
        except Exception:
            pass

    def _controller(self, state):
        selections = dict(state.selections or {})
        annotations = dict(state.annotations or {})
        ctx = WorkflowContext(
            annotations=annotations,
            selections=selections,
            seg_has_voxels=self._seg_has_voxels,
            seg_has_saved=self._seg_has_saved,
        )
        return WorkflowController(ctx)

    def _seg_has_voxels(self, feature, phase_id=None):
        if not self._seg_source_confirmed(feature, phase_id):
            return False
        seg = self._seg_node_for(feature, phase_id)
        return segment_has_voxels(seg)

    def _seg_has_saved(self, feature, phase_id=None):
        if not self._seg_source_confirmed(feature, phase_id):
            return False
        seg = self._seg_node_for(feature, phase_id)
        if not seg:
            return False
        try:
            storage = seg.GetStorageNode()
        except Exception:
            storage = None
        if not storage:
            return False
        try:
            return bool(storage.GetFileName() or storage.GetURI())
        except Exception:
            return False

    def _seg_node_for(self, feature, phase_id=None):
        case_id = self.driver.current_case
        if not case_id:
            return None
        if feature == "lesion":
            phase_id = None
        return find_feature_segmentation_node(feature, case_id=case_id, phase_id=phase_id)

    def _seg_node_has_segments(self, seg_node):
        if not seg_node:
            return False
        try:
            segmentation = seg_node.GetSegmentation()
        except Exception:
            segmentation = None
        if not segmentation:
            return False
        ids = vtk.vtkStringArray()
        try:
            segmentation.GetSegmentIDs(ids)
        except Exception:
            return False
        return bool(ids.GetNumberOfValues() > 0)

    def _save_annotation(self, key, value):
        case_id = self.driver.current_case
        if case_id is None:
            return
        state = self.registry.get(case_id)
        data = dict(state.annotations or {})
        data[key] = value
        state.annotations = data
        self.registry.save(state)

    def _save_selection(self, key, value):
        case_id = self.driver.current_case
        if case_id is None:
            return
        state = self.registry.get(case_id)
        data = dict(state.selections or {})
        data[key] = value
        state.selections = data
        self.registry.save(state)

    def _seg_meta_key(self, feature, phase_id=None):
        phase_id = self._normalize_meta_phase(feature, phase_id)
        phase = "none" if phase_id is None else str(phase_id)
        return f"{feature}:{phase}"

    def _normalize_meta_phase(self, feature, phase_id=None):
        if feature == "lesion":
            return None
        if feature == "aphe" and phase_id is None:
            return 0
        return phase_id

    def _get_seg_meta(self, feature, phase_id=None):
        case_id = self.driver.current_case
        if case_id is None:
            return {}
        state = self.registry.get(case_id)
        meta = dict(state.segmentation_meta or {})
        return dict(meta.get(self._seg_meta_key(feature, phase_id)) or {})

    def _set_seg_meta(self, feature, phase_id=None, values=None, replace=False):
        case_id = self.driver.current_case
        if case_id is None:
            return {}
        state = self.registry.get(case_id)
        meta = dict(state.segmentation_meta or {})
        key = self._seg_meta_key(feature, phase_id)
        current = {} if replace else dict(meta.get(key) or {})
        if values:
            current.update(values)
        meta[key] = current
        state.segmentation_meta = meta
        self.registry.save(state)
        return current

    def _seg_source_confirmed(self, feature, phase_id=None):
        if not feature:
            return False
        meta = self._get_seg_meta(feature, phase_id)
        return bool(meta.get("confirmed"))

    def _seg_source_key(self, source_type, source_feature=None, source_phase=None):
        if source_type == "copy":
            phase = "" if source_phase is None else str(source_phase)
            return f"copy:{source_feature}:{phase}"
        return str(source_type)

    def _seg_source_label(self, source_type, source_feature=None, source_phase=None):
        if source_type == "ai":
            return "AI"
        if source_type == "scratch":
            return "Scratch"
        if source_type == "copy":
            return f"Copy from {self._seg_source_feature_label(source_feature, source_phase)}"
        return "Source"

    def _seg_source_feature_label(self, feature, phase_id=None):
        if feature == "washout" and phase_id == 1:
            return "Washout (Venous)"
        if feature == "washout" and phase_id == 2:
            return "Washout (Delayed)"
        if feature == "capsule" and phase_id == 1:
            return "Capsule (Venous)"
        if feature == "capsule" and phase_id == 2:
            return "Capsule (Delayed)"
        if feature == "lesion":
            return "Lesion"
        if feature == "aphe":
            return "APHE"
        if feature == "washout":
            return "Washout"
        if feature == "capsule":
            return "Capsule"
        return str(feature)

    def _source_segmentation_node(self, feature, phase_id=None):
        case_id = self.driver.current_case
        if not case_id or not feature:
            return None
        node = None
        if phase_id is not None:
            return find_feature_segmentation_node(
                feature, case_id=case_id, phase_id=phase_id
            )
        node = find_feature_segmentation_node(feature, case_id=case_id, phase_id=None)
        if node:
            return node
        nodes = list_segmentation_nodes(case_id=case_id, feature=feature)
        return nodes[0] if nodes else None

    def _seg_source_options(self, feature, phase_id=None):
        options = []
        options.append({"type": "ai"})
        options.append({"type": "scratch"})
        if feature == "lesion":
            return options
        if feature == "aphe":
            options.insert(0, {"type": "copy", "feature": "lesion", "phase_id": None})
            return options
        if feature == "washout" and phase_id == 1:
            options.insert(0, {"type": "copy", "feature": "aphe", "phase_id": None})
            options.insert(0, {"type": "copy", "feature": "lesion", "phase_id": None})
            return options
        if feature == "washout" and phase_id == 2:
            options.insert(0, {"type": "copy", "feature": "washout", "phase_id": 1})
            options.insert(0, {"type": "copy", "feature": "aphe", "phase_id": None})
            options.insert(0, {"type": "copy", "feature": "lesion", "phase_id": None})
            return options
        if feature == "capsule" and phase_id == 2:
            options.insert(0, {"type": "copy", "feature": "capsule", "phase_id": 1})
            return options
        return options

    def _load_state_into_ui(self, state):
        annotations = state.annotations or {}
        selections = state.selections or {}
        self._clear_button_group(self.segmentGroup)
        self._clear_button_group(self.tumorVeinGroup)
        self._clear_button_group(self.vesselGroup)
        self._clear_button_group(self.apheGroup)
        self._clear_button_group(self.washoutGroup)
        self._clear_button_group(self.capsuleGroup)
        self._clear_button_group(self.liradsGroup)
        liver_segment = self._normalize_liver_segment_for_ui(
            annotations.get("liver_segment", "")
        )
        self._set_group_value(self.segmentGroup, liver_segment)
        try:
            self.diameterSpinBox.value = float(annotations.get("max_diameter_mm") or 0.0)
        except Exception:
            self.diameterSpinBox.value = 0.0
        self._set_group_value(self.tumorVeinGroup, annotations.get("tumor_in_vein", ""))
        self._set_group_value(self.vesselGroup, annotations.get("vessel", ""))
        self._set_group_value(self.apheGroup, annotations.get("aphe", ""))
        self._set_group_value(self.washoutGroup, annotations.get("washout", ""))
        self._set_group_value(self.capsuleGroup, annotations.get("capsule", ""))
        self._set_group_value(self.liradsGroup, annotations.get("lirads", ""))
        self.washoutVenousCheck.setChecked(bool(selections.get("washout_venous")))
        self.washoutDelayedCheck.setChecked(bool(selections.get("washout_delayed")))
        self.capsuleVenousCheck.setChecked(bool(selections.get("capsule_venous")))
        self.capsuleDelayedCheck.setChecked(bool(selections.get("capsule_delayed")))
        self._update_washout_phase_visibility()
        self._update_capsule_phase_visibility()

    def _is_unseen_case(self, state):
        if not state:
            return False
        if state.last_opened:
            return False
        if state.annotations or state.selections:
            return False
        return True

    def _reset_workflow_inputs(self, state):
        self._clear_button_group(self.segmentGroup)
        self._clear_button_group(self.tumorVeinGroup)
        self._clear_button_group(self.vesselGroup)
        self._clear_button_group(self.apheGroup)
        self._clear_button_group(self.washoutGroup)
        self._clear_button_group(self.capsuleGroup)
        self._clear_button_group(self.liradsGroup)
        try:
            self.diameterSpinBox.value = 0.0
        except Exception:
            pass
        self.washoutVenousCheck.setChecked(False)
        self.washoutDelayedCheck.setChecked(False)
        self.capsuleVenousCheck.setChecked(False)
        self.capsuleDelayedCheck.setChecked(False)
        self.vesselWidget.setEnabled(False)
        state.annotations = {}
        state.selections = {}
        state.segmentation_meta = {}
        self.registry.save(state)

    def _update_washout_phase_visibility(self):
        present = self._annotation_value(self.washoutGroup) == "Present"
        phases = {}
        try:
            phases = self.driver.available_phases(self.driver.current_case)
        except Exception:
            phases = {}
        venous_available = 1 in phases
        delayed_available = 2 in phases
        self.washoutVenousCheck.setEnabled(bool(venous_available))
        self.washoutDelayedCheck.setEnabled(bool(delayed_available))
        if not venous_available and self.washoutVenousCheck.isChecked():
            self.washoutVenousCheck.setChecked(False)
            self._save_selection("washout_venous", False)
        if not delayed_available and self.washoutDelayedCheck.isChecked():
            self.washoutDelayedCheck.setChecked(False)
            self._save_selection("washout_delayed", False)
        self.washoutPhaseLabel.setVisible(present)
        self.washoutPhaseWidget.setVisible(present)
        self.washoutSegHint.setVisible(present)

    def _update_capsule_phase_visibility(self):
        present = self._annotation_value(self.capsuleGroup) == "Present"
        phases = {}
        try:
            phases = self.driver.available_phases(self.driver.current_case)
        except Exception:
            phases = {}
        venous_available = 1 in phases
        delayed_available = 2 in phases
        self.capsuleVenousCheck.setEnabled(bool(venous_available))
        self.capsuleDelayedCheck.setEnabled(bool(delayed_available))
        if not venous_available and self.capsuleVenousCheck.isChecked():
            self.capsuleVenousCheck.setChecked(False)
            self._save_selection("capsule_venous", False)
        if not delayed_available and self.capsuleDelayedCheck.isChecked():
            self.capsuleDelayedCheck.setChecked(False)
            self._save_selection("capsule_delayed", False)
        self.capsulePhaseLabel.setVisible(present)
        self.capsulePhaseWidget.setVisible(present)
        self.capsuleSegHint.setVisible(present)

    def _step_feature(self, step_idx):
        if step_idx == STEP_LESION:
            return "lesion"
        if step_idx == STEP_APHE:
            return "aphe" if self._annotation_value(self.apheGroup) != "Absent" else None
        if step_idx == STEP_WASHOUT_VENOUS:
            return "washout" if self.washoutVenousCheck.isChecked() else None
        if step_idx == STEP_WASHOUT_DELAYED:
            return "washout" if self.washoutDelayedCheck.isChecked() else None
        if step_idx == STEP_CAPSULE_VENOUS:
            return "capsule" if self.capsuleVenousCheck.isChecked() else None
        if step_idx == STEP_CAPSULE_DELAYED:
            return "capsule" if self.capsuleDelayedCheck.isChecked() else None
        return None

    def _phase_for_step(self, step_idx):
        if step_idx == STEP_APHE:
            return 0
        if step_idx in (STEP_WASHOUT_VENOUS, STEP_CAPSULE_VENOUS):
            return 1
        if step_idx in (STEP_WASHOUT_DELAYED, STEP_CAPSULE_DELAYED):
            return 2
        return None

    def _is_compare_step(self, step_idx):
        return step_idx in (
            STEP_WASHOUT_VENOUS,
            STEP_WASHOUT_DELAYED,
            STEP_CAPSULE_VENOUS,
            STEP_CAPSULE_DELAYED,
        )

    def _apply_phase_focus_for_step(self, step_idx):
        case_id = self.driver.current_case
        if not case_id:
            return
        grid_steps = {
            STEP_LESION,
            STEP_LIVER,
            STEP_TIV,
            STEP_WASHOUT,
            STEP_CAPSULE,
            STEP_FINISH,
        }
        if step_idx in grid_steps:
            if (
                getattr(self.driver, "view_mode", None) == "grid"
                and getattr(self.driver, "view_case_id", None) == case_id
            ):
                return
            try:
                self.driver.show_grid_view(case_id)
            except Exception:
                return
            return
        if self._is_compare_step(step_idx):
            phase_id = self._phase_for_step(step_idx)
            if phase_id is None:
                return
            available = self.driver.available_phases(case_id)
            if phase_id not in available:
                slicer.util.infoDisplay("Phase not available for this case.")
                return
            has_arterial = 0 in available
            if (
                getattr(self.driver, "view_mode", None) == "compare"
                and getattr(self.driver, "view_case_id", None) == case_id
                and self.driver.current_phase == phase_id
                and (
                    (not has_arterial)
                    or bool(self.driver.volume_for_phase(0))
                )
            ):
                return
            try:
                if has_arterial:
                    self.driver.show_compare_view(
                        phase_id, reference_phase=0, case_id=case_id
                    )
                else:
                    self.driver.show_single_phase(phase_id, case_id=case_id)
            except Exception:
                return
            return
        phase_id = self._phase_for_step(step_idx)
        if phase_id is None:
            return
        if (
            getattr(self.driver, "view_mode", None) == "single"
            and getattr(self.driver, "view_case_id", None) == case_id
            and self.driver.current_phase == phase_id
        ):
            return
        available = self.driver.available_phases(case_id)
        if phase_id not in available:
            slicer.util.infoDisplay("Phase not available for this case.")
            return
        try:
            self.driver.show_single_phase(phase_id, case_id=case_id)
        except Exception:
            return

    def _step_requires_segmentation(self, step_idx):
        state = self.registry.get(self.driver.current_case) if self.driver.current_case else None
        if not state:
            return False
        controller = self._controller(state)
        return controller.requires_segmentation(step_idx)

    def _activate_feature_segmentation(self, feature, phase_id=None):
        case_id = self.driver.current_case
        if not case_id or not feature:
            return
        source_phase_id = phase_id
        if feature == "lesion" and phase_id is None:
            phase_id = self.driver.current_phase
            source_phase_id = phase_id
        if not self._ensure_seg_editor_ready():
            return
        seg_node = ensure_feature_segmentation_node(
            feature, case_id=case_id, phase_id=phase_id
        )
        self.segEditorWidget.setSegmentationNode(seg_node)
        vol = self.driver.volume_for_phase(source_phase_id)
        if not vol:
            vol = self.driver.current_volume
        if vol:
            self._sync_segmentation_geometry_to_volume(seg_node, vol)
            # If the segmentation is effectively empty, reinitialize it after
            # geometry sync so stale oriented labelmap state does not keep paint
            # operations anchored to an old reference frame.
            if not segment_has_voxels(seg_node):
                clear_segmentation_contents(
                    seg_node,
                    feature=feature,
                    phase_id=phase_id,
                )
                self._sync_segmentation_geometry_to_volume(seg_node, vol)
            self.segEditorWidget.setSourceVolumeNode(vol)
        self._select_first_segment(seg_node)

    def _sync_segmentation_geometry_to_volume(self, seg_node, volume_node):
        if not seg_node or not volume_node:
            return
        try:
            if hasattr(seg_node, "SetReferenceImageGeometryParameterFromVolumeNode"):
                seg_node.SetReferenceImageGeometryParameterFromVolumeNode(volume_node)
                return
        except Exception:
            pass
        try:
            logic = slicer.modules.segmentations.logic()
        except Exception:
            logic = None
        if not logic:
            return
        try:
            if hasattr(logic, "SetReferenceImageGeometryParameterFromVolumeNode"):
                logic.SetReferenceImageGeometryParameterFromVolumeNode(seg_node, volume_node)
        except Exception:
            pass

    def _ensure_seg_editor_ready(self):
        if not getattr(self, "segEditorWidget", None):
            return False
        if not getattr(self, "segEditorNode", None):
            return False
        try:
            current = self.segEditorWidget.mrmlSegmentEditorNode()
        except Exception:
            current = None
        if current is None:
            try:
                self.segEditorWidget.setMRMLSegmentEditorNode(self.segEditorNode)
            except Exception:
                return False
        return True

    def _clear_segmentation_editor(self):
        ready = self._ensure_seg_editor_ready()
        if ready:
            try:
                self.segEditorWidget.setSourceVolumeNode(None)
            except Exception:
                pass
        try:
            self.segEditorWidget.setSegmentationNode(None)
        except Exception:
            pass

    def _set_segmentation_visibility(self, seg_node, visible):
        if not seg_node:
            return
        try:
            if not seg_node.GetDisplayNode():
                seg_node.CreateDefaultDisplayNodes()
            disp = seg_node.GetDisplayNode()
        except Exception:
            disp = None
        if not disp:
            return
        try:
            disp.SetVisibility(1 if visible else 0)
        except Exception:
            pass

    def _current_seg_context(self):
        feature = self._step_feature(self._workflow_step)
        phase_id = self._phase_for_step(self._workflow_step)
        if feature == "lesion":
            phase_id = None
        return feature, phase_id

    def _seg_source_meta_from_option(self, option):
        if not option:
            return {}
        return {
            "source_type": option.get("type"),
            "source_feature": option.get("feature"),
            "source_phase": option.get("phase_id"),
        }

    def _seg_source_key_from_meta(self, meta):
        if not meta:
            return None
        return self._seg_source_key(
            meta.get("source_type"),
            meta.get("source_feature"),
            meta.get("source_phase"),
        )

    def _seg_source_current_option(self):
        getter = getattr(self.segSourceCombo, "currentData", None)
        if callable(getter):
            return getter(qt.Qt.UserRole)
        try:
            idx = int(self.segSourceCombo.currentIndex)
        except Exception:
            idx = -1
        try:
            return self.segSourceCombo.itemData(idx, qt.Qt.UserRole)
        except Exception:
            return None

    def _seg_source_available(self, option):
        if not option:
            return False
        source_type = option.get("type")
        if source_type == "ai":
            case_id = self.driver.current_case
            if not case_id:
                return False
            feature, phase_id = self._current_seg_context()
            feature = feature or option.get("feature")
            phase_id = option.get("phase_id", phase_id)
            return bool(
                find_ai_segmentation_path(
                    case_id, feature, phase_id
                )
            )
        if source_type != "copy":
            return True
        source_node = self._source_segmentation_node(
            option.get("feature"), option.get("phase_id")
        )
        return segment_has_voxels(source_node)

    def _refresh_seg_source_picker(self, feature, phase_id):
        if not feature:
            self.segSourceContainer.setVisible(False)
            return
        meta = self._get_seg_meta(feature, phase_id)
        seg_node = self._seg_node_for(feature, phase_id)
        # If a non-empty segmentation is already present for this context, treat it
        # as a confirmed scratch source so Step 1 opens directly in the editor.
        # Empty placeholder segments from preview/scratch should not auto-confirm.
        has_existing = segment_has_voxels(seg_node)
        source_type = meta.get("source_type")
        should_autoselect_scratch = (not source_type) or (source_type == "scratch")
        if not meta.get("confirmed") and has_existing and should_autoselect_scratch:
            meta = self._set_seg_meta(
                feature,
                phase_id,
                {
                    "source_type": "scratch",
                    "source_feature": None,
                    "source_phase": None,
                    "confirmed": True,
                    "preview": True,
                },
                replace=True,
            )
        desired_key = self._seg_source_key_from_meta(meta)
        options = self._seg_source_options(feature, phase_id)
        self._seg_source_block = True
        try:
            self.segSourceCombo.clear()
            self.segSourceCombo.addItem("Select source...")
            self.segSourceCombo.setItemData(0, None, qt.Qt.UserRole)
            model = self.segSourceCombo.model()
            selected_index = 0
            for idx, option in enumerate(options, start=1):
                source_type = option.get("type")
                label = self._seg_source_label(
                    source_type, option.get("feature"), option.get("phase_id")
                )
                self.segSourceCombo.addItem(label)
                self.segSourceCombo.setItemData(idx, option, qt.Qt.UserRole)
                enabled = self._seg_source_available(option)
                item = model.item(idx)
                if item is not None:
                    item.setEnabled(bool(enabled))
                key = self._seg_source_key(
                    source_type, option.get("feature"), option.get("phase_id")
                )
                if desired_key and key == desired_key:
                    selected_index = idx
            self.segSourceCombo.setCurrentIndex(selected_index)
            current = self._seg_source_current_option()
            current_type = current.get("type") if isinstance(current, dict) else None
            confirmed = bool(meta.get("confirmed"))
            preview_value = bool(meta.get("preview", True))
            self.segSourcePreview.setChecked(preview_value)
            self.segSourcePreview.setVisible(current_type in ("ai", "copy"))
            self.segSourceConfirm.setEnabled(bool(current) and not confirmed)
            label = self._seg_source_label(
                meta.get("source_type"),
                meta.get("source_feature"),
                meta.get("source_phase"),
            )
            if confirmed and meta.get("source_type"):
                self.segSourceStatus.setText(f"Source: {label}")
            elif current:
                self.segSourceStatus.setText(f"Selected: {label}. Confirm to continue.")
            else:
                self.segSourceStatus.setText("Select a source to begin.")
            self._seg_source_last_key = desired_key
        finally:
            self._seg_source_block = False
        self.segSourceContainer.setVisible(True)

    def _force_seg_source_scratch(self, feature, phase_id, message=None):
        if message:
            slicer.util.errorDisplay(message)
        case_id = self.driver.current_case
        if not case_id or not feature:
            return
        target_node = ensure_feature_segmentation_node(
            feature, case_id=case_id, phase_id=phase_id
        )
        clear_segmentation_contents(target_node, feature=feature, phase_id=phase_id)
        self._set_segmentation_visibility(target_node, True)
        self._set_seg_meta(
            feature,
            phase_id,
            {
                "source_type": "scratch",
                "source_feature": None,
                "source_phase": None,
                "confirmed": True,
                "preview": True,
            },
        )
        self._refresh_seg_source_picker(feature, phase_id)
        self._apply_workflow_visibility()
        self._update_workflow_controls()

    def _apply_seg_source_preview(self, feature, phase_id, option):
        if not option or not feature:
            return False
        case_id = self.driver.current_case
        if not case_id:
            return False
        target_node = ensure_feature_segmentation_node(
            feature, case_id=case_id, phase_id=phase_id
        )
        source_type = option.get("type")
        if source_type == "scratch":
            clear_segmentation_contents(target_node, feature=feature, phase_id=phase_id)
        elif source_type == "copy":
            source_node = self._source_segmentation_node(
                option.get("feature"), option.get("phase_id")
            )
            if not segment_has_voxels(source_node):
                return False
            if not copy_segmentation_contents(
                source_node, target_node, feature=feature, phase_id=phase_id
            ):
                return False
        elif source_type == "ai":
            ok = load_ai_segmentation_into_node(
                case_id, feature, phase_id, target_node
            )
            if not ok:
                return False
        else:
            return False
        preview_visible = bool(self.segSourcePreview.isChecked())
        self._set_segmentation_visibility(target_node, preview_visible)
        return True

    def _on_seg_source_changed(self, _index):
        if self._seg_source_block:
            return
        feature, phase_id = self._current_seg_context()
        if not feature:
            return
        option = self._seg_source_current_option()
        if not isinstance(option, dict):
            self._set_seg_meta(feature, phase_id, {"confirmed": False})
            self._refresh_seg_source_picker(feature, phase_id)
            self._apply_workflow_visibility()
            return
        meta = self._get_seg_meta(feature, phase_id)
        new_key = self._seg_source_key(
            option.get("type"), option.get("feature"), option.get("phase_id")
        )
        old_key = self._seg_source_key_from_meta(meta)
        if meta.get("confirmed") and new_key != old_key:
            ok = slicer.util.confirmYesNoDisplay(
                "Changing the segmentation source will overwrite current edits. Continue?"
            )
            if not ok:
                self._seg_source_block = True
                try:
                    self._refresh_seg_source_picker(feature, phase_id)
                finally:
                    self._seg_source_block = False
                return
        values = self._seg_source_meta_from_option(option)
        values.update({"confirmed": False, "preview": True})
        self._set_seg_meta(feature, phase_id, values)
        ok = self._apply_seg_source_preview(feature, phase_id, option)
        if not ok:
            self._force_seg_source_scratch(
                feature,
                phase_id,
                "Source not available. Starting from scratch instead.",
            )
            return
        self._refresh_seg_source_picker(feature, phase_id)
        self._apply_workflow_visibility()
        self._update_workflow_controls()

    def _on_seg_source_confirm(self):
        feature, phase_id = self._current_seg_context()
        if not feature:
            return
        option = self._seg_source_current_option()
        if not isinstance(option, dict):
            return
        ok = self._apply_seg_source_preview(feature, phase_id, option)
        if not ok:
            self._force_seg_source_scratch(
                feature,
                phase_id,
                "Source could not be loaded. Starting from scratch instead.",
            )
            return
        self._set_seg_meta(
            feature,
            phase_id,
            {
                "confirmed": True,
                "preview": bool(self.segSourcePreview.isChecked()),
            },
        )
        self._refresh_seg_source_picker(feature, phase_id)
        self._apply_workflow_visibility()
        self._update_workflow_controls()

    def _on_seg_source_preview_toggled(self, checked):
        feature, phase_id = self._current_seg_context()
        if not feature:
            return
        seg_node = self._seg_node_for(feature, phase_id)
        self._set_segmentation_visibility(seg_node, bool(checked))
        self._set_seg_meta(
            feature,
            phase_id,
            {"preview": bool(checked)},
        )

    def _select_first_segment(self, seg_node):
        if not seg_node or not getattr(self, "segEditorNode", None):
            return
        try:
            segmentation = seg_node.GetSegmentation()
        except Exception:
            segmentation = None
        if not segmentation:
            return
        ids = vtk.vtkStringArray()
        segmentation.GetSegmentIDs(ids)
        if ids.GetNumberOfValues() == 0:
            return
        try:
            self.segEditorNode.SetSelectedSegmentID(ids.GetValue(0))
        except Exception:
            pass

    def _step_description(self, step_idx):
        descriptions = {
            STEP_LESION: "Segment the lesion for this phase using the editor below.",
            STEP_LIVER: "Select liver segment and estimate the maximum diameter.",
            STEP_TIV: "Mark tumor-in-vein and select the involved vessel.",
            STEP_APHE: "Select APHE presence and segment if present.",
            STEP_WASHOUT: "Select washout presence and choose the phases involved.",
            STEP_WASHOUT_VENOUS: "Segment washout on venous phase.",
            STEP_WASHOUT_DELAYED: "Segment washout on delayed phase.",
            STEP_CAPSULE: "Select capsule presence and choose the phases involved.",
            STEP_CAPSULE_VENOUS: "Segment capsule on venous phase.",
            STEP_CAPSULE_DELAYED: "Segment capsule on delayed phase.",
            STEP_FINISH: "Set the LIRADS score and review the summary.",
        }
        return descriptions.get(step_idx, "")

    def _apply_workflow_visibility(self):
        feature, phase_id = self._current_seg_context()
        show_picker = bool(feature)
        if self._workflow_step == STEP_APHE:
            aphe_value = self._annotation_value(self.apheGroup)
            if aphe_value not in ("Non-rim APHE", "Rim APHE"):
                show_picker = False
        if show_picker:
            self._refresh_seg_source_picker(feature, phase_id)
        confirmed = bool(feature) and self._seg_source_confirmed(feature, phase_id)
        show_editor = bool(feature) and confirmed and show_picker
        self.segSourceContainer.setVisible(show_picker)
        self.segEditorContainer.setVisible(show_editor)
        self.saveSegContainer.setVisible(show_editor)
        if show_editor:
            self._activate_feature_segmentation(feature, phase_id=phase_id)
        else:
            self._clear_segmentation_editor()

    def _update_workflow_controls(self):
        case_id = self.driver.current_case
        if not case_id:
            return
        state = self.registry.get(case_id)
        controller = self._controller(state)
        self._ensure_applicable_step(controller)
        self.backButton.setEnabled(self._workflow_step > 0)
        self.nextButton.setEnabled(controller.workflow_step_complete(self._workflow_step))
        self.btnFinishSave.setEnabled(
            self._workflow_step == STEP_FINISH
            and controller.workflow_step_complete(STEP_FINISH)
        )
        tiv_present = self._annotation_value(self.tumorVeinGroup) == "Present"
        vessel_selected = bool(self._annotation_value(self.vesselGroup))
        self.btnSkipToEnd.setEnabled(tiv_present and vessel_selected)
        self._update_summary()

    def _ensure_applicable_step(self, controller):
        if controller.is_step_applicable(self._workflow_step):
            return
        prev_idx = controller.prev_applicable_step(self._workflow_step)
        next_idx = controller.next_applicable_step(self._workflow_step)
        target = prev_idx if prev_idx is not None else next_idx
        if target is not None:
            self._set_workflow_step(target, save_state=True)

    def _set_lirads_button(self, value):
        self._set_group_value(self.liradsGroup, value)
        self._save_annotation("lirads", value)

    def _update_summary(self):
        annotations = {}
        case_id = self.driver.current_case
        if case_id:
            state = self.registry.get(case_id)
            annotations = state.annotations or {}
        parts = []
        diameter = annotations.get("max_diameter_mm")
        if diameter:
            try:
                diameter_value = float(diameter)
            except Exception:
                diameter_value = None
            if diameter_value is not None:
                parts.append(f"Lesion size: {diameter_value:.1f} mm")
        if annotations.get("aphe"):
            parts.append(f"APHE: {annotations.get('aphe')}")
        if annotations.get("washout"):
            parts.append(f"Washout: {annotations.get('washout')}")
        if annotations.get("capsule"):
            parts.append(f"Capsule: {annotations.get('capsule')}")
        if annotations.get("lirads"):
            parts.append(f"LIRADS: {annotations.get('lirads')}")
        self.summaryLabel.setText(" | ".join(parts))

    def compute_segment_max_diameter_mm(self, seg_node, segment_id=None):
        if not seg_node:
            return None
        segmentation = seg_node.GetSegmentation()
        if not segmentation:
            return None

        if segment_id is None:
            ids = vtk.vtkStringArray()
            segmentation.GetSegmentIDs(ids)
            if ids.GetNumberOfValues() == 0:
                return None
            segment_id = ids.GetValue(0)

        try:
            mask = slicer.util.arrayFromSegmentBinaryLabelmap(seg_node, segment_id)
        except Exception:
            mask = None
        if mask is None:
            label_node = None
            try:
                label_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode")
                ids = vtk.vtkStringArray()
                ids.InsertNextValue(segment_id)
                seg_logic = slicer.modules.segmentations.logic()
                volume_node = getattr(self.driver, "current_volume", None)
                exported = False
                try:
                    if volume_node:
                        exported = bool(
                            seg_logic.ExportSegmentsToLabelmapNode(
                                seg_node, ids, label_node, volume_node
                            )
                        )
                except Exception:
                    exported = False
                if not exported:
                    try:
                        exported = bool(
                            seg_logic.ExportSegmentsToLabelmapNode(seg_node, ids, label_node)
                        )
                    except Exception:
                        exported = False
                if not exported:
                    return None
                try:
                    mask = slicer.util.arrayFromVolume(label_node)
                except Exception:
                    mask = None
            finally:
                if label_node:
                    try:
                        slicer.mrmlScene.RemoveNode(label_node)
                    except Exception:
                        pass
            if mask is None:
                return None
        try:
            if not mask.any():
                return None
        except Exception:
            return None

        spacing_x = 1.0
        spacing_y = 1.0
        volume_node = getattr(self.driver, "current_volume", None)
        if volume_node:
            try:
                spacing = volume_node.GetSpacing()
                spacing_x = float(spacing[0]) if spacing and len(spacing) >= 1 else 1.0
                spacing_y = float(spacing[1]) if spacing and len(spacing) >= 2 else 1.0
            except Exception:
                spacing_x = 1.0
                spacing_y = 1.0

        def convex_hull_2d(points_xy):
            pts = sorted(set((float(p[0]), float(p[1])) for p in points_xy))
            if len(pts) <= 1:
                return pts

            def cross(o, a, b):
                return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

            lower = []
            for p in pts:
                while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0.0:
                    lower.pop()
                lower.append(p)

            upper = []
            for p in reversed(pts):
                while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0.0:
                    upper.pop()
                upper.append(p)

            return lower[:-1] + upper[:-1]

        def max_pairwise_distance(points_xy):
            count = len(points_xy)
            if count <= 1:
                return 0.0
            if count == 2:
                dx = points_xy[0][0] - points_xy[1][0]
                dy = points_xy[0][1] - points_xy[1][1]
                return float(math.sqrt(dx * dx + dy * dy))
            max_d2 = 0.0
            for i in range(count):
                ax, ay = points_xy[i]
                for j in range(i + 1, count):
                    bx, by = points_xy[j]
                    dx = ax - bx
                    dy = ay - by
                    d2 = dx * dx + dy * dy
                    if d2 > max_d2:
                        max_d2 = d2
            return float(math.sqrt(max_d2))

        max_diameter = 0.0
        try:
            non_empty_slices = mask.any(axis=(1, 2)).nonzero()[0]
        except Exception:
            return None
        for slice_idx in non_empty_slices:
            slice_mask = mask[slice_idx]
            ys, xs = slice_mask.nonzero()
            if len(xs) < 2:
                continue
            points = [
                (float(x) * spacing_x, float(y) * spacing_y) for y, x in zip(ys, xs)
            ]
            hull = convex_hull_2d(points)
            if not hull:
                continue
            local_diameter = max_pairwise_distance(hull)
            if local_diameter > max_diameter:
                max_diameter = local_diameter

        if max_diameter <= 0.0:
            return None
        return float(max_diameter)
