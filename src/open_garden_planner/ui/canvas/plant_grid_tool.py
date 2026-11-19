"""A transient, transformable plant grid that commits as one undo step."""

from __future__ import annotations

from math import atan2, degrees
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication, QLineF, QPointF, QRectF, QSignalBlocker, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen, QTransform
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
)

from open_garden_planner.app.settings import AppSettings, get_settings
from open_garden_planner.core.commands import CompoundCommand, CreateItemsCommand, GroupCommand
from open_garden_planner.core.plant_renderer import paint_plant
from open_garden_planner.core.plant_sizing import species_drop_diameter_cm
from open_garden_planner.core.tools.base_tool import BaseTool, ToolType
from open_garden_planner.services import PlantAPIManager
from open_garden_planner.ui.canvas.items.resize_handle import (
    HANDLE_COLOR,
    HANDLE_HOVER_COLOR,
    HANDLE_SIZE,
    ROTATION_HANDLE_COLOR,
    ROTATION_HANDLE_HOVER_COLOR,
    ROTATION_HANDLE_OFFSET,
    ROTATION_HANDLE_SIZE,
    ROTATION_SNAP_ANGLE,
)
from open_garden_planner.ui.dialogs.plant_search_dialog import PlantSearchDialog
from open_garden_planner.ui.icons import get_icon
from open_garden_planner.ui.plant_creation import build_species_plant, species_object_type

if TYPE_CHECKING:
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    from open_garden_planner.ui.canvas.canvas_view import CanvasView

_ACCENT = "#e38127"
_MIN_SPAN = 2.0  # cm
_HANDLES = ((-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0))


class PlantGridTool(BaseTool):
    tool_type = ToolType.PLANT_GRID
    shortcut = ""
    cursor = Qt.CursorShape.CrossCursor
    accepts_typed_coordinates = False
    skip_anchor_snap = True

    def _translated_name(self) -> str:
        return QCoreApplication.translate("PlantGridTool", "Plant Grid")

    def __init__(self, view: CanvasView, scene: CanvasScene) -> None:
        super().__init__(view)
        self._scene = scene
        self._scene.document_replaced.connect(self.cancel)
        self._origin: QPointF | None = None
        self._species: dict | None = None
        self._dimensions = (3, 3)
        self._diameter = 100.0
        self._size_edited = False
        self._center = QPointF()
        self._width = self._height = _MIN_SPAN
        self._angle = 0.0
        self._editing = False
        self._drag: str | tuple[int, int] | None = None
        self._drag_start = QPointF()
        self._before = (QPointF(), 0.0, 0.0, 0.0)
        self._diameter_before = 0.0
        self._hover: str | tuple[int, int] | None = None
        self._controls: QFrame | None = None
        self._size_button: QToolButton | None = None
        self._size_slider: QSlider | None = None
        self._preview_button: QToolButton | None = None

    def _edit_dimensions(self) -> bool:
        dialog = QDialog(self.view)
        dialog.setWindowTitle(QCoreApplication.translate("PlantGridTool", "Plant Grid"))
        form = QFormLayout(dialog)
        rows, columns = QSpinBox(dialog), QSpinBox(dialog)
        for widget, value in zip((rows, columns), self._dimensions, strict=True):
            widget.setRange(1, AppSettings.PLANT_GRID_MAX_SIDE)
            widget.setValue(value)
        form.addRow(QCoreApplication.translate("PlantGridTool", "Rows"), rows)
        form.addRow(QCoreApplication.translate("PlantGridTool", "Columns"), columns)
        self._dialog_buttons(dialog, form)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted:
            self._dimensions = (rows.value(), columns.value())
            settings = get_settings()
            settings.plant_grid_dimensions = self._dimensions
            settings.sync()
            self.view.viewport().update()
        dialog.deleteLater()
        return accepted

    @staticmethod
    def _dialog_buttons(dialog: QDialog, form: QFormLayout) -> None:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

    def _pick_plant(self) -> bool:
        dialog = PlantSearchDialog(PlantAPIManager(), self.view)
        try:
            if not dialog.exec() or dialog.selected_plant is None:
                return False
            self._species = dialog.selected_plant.to_dict()
            self._diameter = species_drop_diameter_cm(
                self._species, species_object_type(self._species).name,
            )
            self._size_edited = False
            self._sync_size_slider()
            self.view.viewport().update()
            return True
        finally:
            dialog.deleteLater()

    def _toggle_plant_size(self, enabled: bool) -> None:
        if self._size_slider is not None and self._controls is not None:
            self._sync_size_slider()
            self._size_slider.setVisible(enabled)
            self._controls.adjustSize()
        self.view.viewport().update()

    def _sync_size_slider(self) -> None:
        slider = self._size_slider
        if slider is not None:
            with QSignalBlocker(slider):
                slider.setMaximum(max(round(self._diameter), min(5000, max(200, round(self._diameter * 3)))))
                slider.setValue(round(self._diameter))

    def _slide_plant_size(self, value: int) -> None:
        self._diameter = float(value)
        self._size_edited = True
        self.view.viewport().update()

    def _ensure_controls(self) -> QFrame:
        if self._controls is not None:
            return self._controls
        controls = QFrame(self.view.viewport())
        controls.setFrameShape(QFrame.Shape.StyledPanel)
        controls.setAutoFillBackground(True)
        layout = QVBoxLayout(controls)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        buttons = QHBoxLayout()
        buttons.setSpacing(2)
        layout.addLayout(buttons)
        actions = (
            ("delete", QCoreApplication.translate("PlantGridTool", "Cancel grid"), self._finish),
            ("grid", QCoreApplication.translate("PlantGridTool", "Edit rows and columns"), self._edit_dimensions),
            ("shrub", QCoreApplication.translate("PlantGridTool", "Change plant"), self._pick_plant),
            ("measure", QCoreApplication.translate(
                "PlantGridTool", "Plant size slider",
            ), self._toggle_plant_size),
            ("eye", QCoreApplication.translate("PlantGridTool", "Preview selected plant"),
             lambda _checked: self.view.viewport().update()),
            ("check", QCoreApplication.translate("PlantGridTool", "Place plants"), self._commit),
        )
        for name, tip, action in actions:
            button = QToolButton(controls)
            button.setObjectName(name)
            button.setAccessibleName(tip)
            button.setToolTip(tip)
            if name == "measure":
                button.setCheckable(True)
                self._size_button = button
            elif name == "eye":
                button.setCheckable(True)
                self._preview_button = button
            icon = get_icon(name, color={"grid": "#ffffff", "shrub": "#91c9a0"}.get(name))
            if name == "delete":
                button.setText("×")
            elif icon is not None:
                button.setIcon(icon)
            else:
                button.setText(tip)
            button.clicked.connect(action)
            buttons.addWidget(button)
        slider = QSlider(Qt.Orientation.Horizontal, controls)
        slider.setAccessibleName(QCoreApplication.translate("PlantGridTool", "Plant size slider"))
        slider.setMinimum(round(_MIN_SPAN))
        slider.valueChanged.connect(self._slide_plant_size)
        slider.hide()
        layout.addWidget(slider)
        self._size_slider = slider
        self._sync_size_slider()
        self._controls = controls
        controls.adjustSize()
        return controls


    def _transform(self) -> QTransform:
        return QTransform().translate(self._center.x(), self._center.y()).rotate(self._angle)

    def _rect(self) -> QRectF:
        return QRectF(-self._width / 2, -self._height / 2, self._width, self._height)

    def _centers(self) -> list[QPointF]:
        rows, columns = self._dimensions
        dx, dy = self._width / columns, self._height / rows
        return [
            QPointF((column + 0.5) * dx - self._width / 2, (row + 0.5) * dy - self._height / 2)
            for row in range(rows) for column in range(columns)
        ]

    def _set_extent(self, point: QPointF) -> None:
        if self._origin is None:
            return
        rect = QRectF(self._origin, self.view.snap_point(point)).normalized()
        self._center = rect.center()
        self._width = max(_MIN_SPAN, rect.width())
        self._height = max(_MIN_SPAN, rect.height())

    def _pixel_scale(self) -> float:
        transform = self.view.transform()
        return max(1e-6, QLineF(transform.map(QPointF()), transform.map(QPointF(1, 0))).length())

    def _handles(self) -> dict[str | tuple[int, int], QPointF]:
        transform = self._transform()
        handles: dict[str | tuple[int, int], QPointF] = {
            (x, y): transform.map(QPointF(x * self._width / 2, y * self._height / 2))
            for x, y in _HANDLES
        }
        offset = ROTATION_HANDLE_OFFSET / self._pixel_scale()
        handles["rotate"] = transform.map(QPointF(0, -self._height / 2 - offset))
        handles["move"] = self._center
        handles["diameter"] = transform.map(self._first_plant() + QPointF(self._diameter / 2, 0))
        return handles

    def _first_plant(self) -> QPointF:
        rows, columns = self._dimensions
        return QPointF(-self._width / 2 + self._width / (2 * columns),
                       -self._height / 2 + self._height / (2 * rows))

    def _handle_at(self, point: QPointF) -> str | tuple[int, int] | None:
        nearest = None
        limit = (max(HANDLE_SIZE, ROTATION_HANDLE_SIZE) / 2 + 4) / self._pixel_scale()
        for name, center in reversed(tuple(self._handles().items())):
            distance = QLineF(center, point).length()
            if distance < limit:
                limit, nearest = distance, name
        if nearest is None and self._rect().contains(self._transform().inverted()[0].map(point)):
            return "move"
        return nearest

    def _update_cursor(self, handle: str | tuple[int, int] | None) -> None:
        cursor = Qt.CursorShape.ArrowCursor
        if handle == "move":
            cursor = Qt.CursorShape.ClosedHandCursor if self._drag else Qt.CursorShape.OpenHandCursor
        elif handle == "rotate":
            cursor = Qt.CursorShape.CrossCursor
        elif handle == "diameter":
            cursor = Qt.CursorShape.SizeAllCursor
        elif isinstance(handle, tuple):
            direction = self._transform().map(QPointF(*handle)) - self._center
            screen = self.view.transform().map(direction) - self.view.transform().map(QPointF())
            sector = round(degrees(atan2(screen.y(), screen.x())) / 45) % 4
            cursor = (Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeFDiagCursor,
                      Qt.CursorShape.SizeVerCursor, Qt.CursorShape.SizeBDiagCursor)[sector]
        self.view.setCursor(cursor)

    def mouse_press(self, event: QMouseEvent, scene_pos: QPointF) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._origin is None:
            self._dimensions = get_settings().plant_grid_dimensions
            if self._edit_dimensions() and self._pick_plant():
                self._origin = self.view.snap_point(scene_pos)
                self._set_extent(self._origin)
            else:
                self._finish()
        elif not self._editing:
            self._set_extent(scene_pos)
            self._editing = True
            self._ensure_controls().show()
        else:
            self._drag = self._handle_at(scene_pos)
            self._drag_start = scene_pos
            self._before = (QPointF(self._center), self._width, self._height, self._angle)
            self._diameter_before = self._diameter
            self._update_cursor(self._drag)
        self.view.viewport().update()
        return True

    def mouse_move(self, event: QMouseEvent, scene_pos: QPointF) -> bool:
        if self._origin is None:
            return False
        if not self._editing:
            self._set_extent(scene_pos)
        elif self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._transform_drag(scene_pos, event.modifiers())
        else:
            self._drag = None
            self._hover = self._handle_at(scene_pos)
            self._update_cursor(self._hover)
        self.view.viewport().update()
        return True

    def _transform_drag(self, point: QPointF, modifiers: Qt.KeyboardModifier) -> None:
        center, width, height, angle = self._before
        if self._drag == "move":
            self._center = center + point - self._drag_start
        elif self._drag == "rotate":
            start, end = self._drag_start - center, point - center
            if not end.isNull():
                self._angle = angle + degrees(atan2(end.y(), end.x()) - atan2(start.y(), start.x()))
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self._angle = round(self._angle / ROTATION_SNAP_ANGLE) * ROTATION_SNAP_ANGLE
        elif self._drag == "diameter":
            plant = self._transform().map(self._first_plant())
            delta = QLineF(plant, point).length() - QLineF(plant, self._drag_start).length()
            diameter = max(_MIN_SPAN, min(max(5000, self._diameter_before),
                                          self._diameter_before + 2 * delta))
            if abs(diameter - self._diameter_before) > 1e-6:
                self._size_edited = True
            self._diameter = diameter
            self._sync_size_slider()
        elif isinstance(self._drag, tuple):
            sx, sy = self._drag
            transform = QTransform().translate(center.x(), center.y()).rotate(angle)
            inverse = transform.inverted()[0]
            delta = inverse.map(point) - inverse.map(self._drag_start)
            start_x, end_x = self._resize_edges(width, sx, sx * width / 2 + delta.x())
            start_y, end_y = self._resize_edges(height, sy, sy * height / 2 + delta.y())
            self._width, self._height = end_x - start_x, end_y - start_y
            self._center = transform.map(QPointF((start_x + end_x) / 2, (start_y + end_y) / 2))

    @staticmethod
    def _resize_edges(span: float, direction: int, position: float) -> tuple[float, float]:
        low, high = -span / 2, span / 2
        if direction < 0:
            low = min(position, high - _MIN_SPAN)
        elif direction > 0:
            high = max(position, low + _MIN_SPAN)
        return low, high

    def mouse_release(self, event: QMouseEvent, scene_pos: QPointF) -> bool:
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return False
        if self._drag is not None:
            self._transform_drag(scene_pos, event.modifiers())
            self._drag = None
            self._hover = self._handle_at(scene_pos)
            self._update_cursor(self._hover)
            self.view.viewport().update()
        return True

    def key_press(self, event: QKeyEvent) -> bool:
        if event.key() == Qt.Key.Key_Escape:
            self._finish()
            return True
        return False

    def paint_preview(self, painter: QPainter) -> None:
        if self._origin is None:
            return
        transform = self._transform()
        painter.save()
        painter.setWorldTransform(transform, True)
        pen = QPen(QColor(_ACCENT))
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._rect())
        painter.setBrush(QColor(227, 129, 39, 35))
        radius = self._diameter / 2
        species = self._species
        preview = self._preview_button is not None and self._preview_button.isChecked()
        object_type = species_object_type(species) if preview and species is not None else None
        species_name = (species.get("common_name") or species.get("scientific_name") or "") if species else ""
        for point in self._centers():
            if object_type is not None:
                rect = QRectF(point.x() - radius, point.y() - radius, self._diameter, self._diameter)
                if paint_plant(painter, rect, object_type, species=species_name):
                    continue
            painter.drawEllipse(point, radius, radius)
        painter.restore()
        if self._editing:
            self._paint_handles(painter)

    def _paint_handles(self, painter: QPainter) -> None:
        painter.save()
        transform = painter.worldTransform()
        painter.resetTransform()
        handles = {handle: transform.map(point) for handle, point in self._handles().items()}
        painter.setPen(QPen(ROTATION_HANDLE_COLOR, 1.0))
        painter.drawLine(handles[(0, -1)], handles["rotate"])
        painter.setPen(QPen(QColor("white"), 1.0))
        for handle, point in handles.items():
            active = handle == (self._drag if self._drag is not None else self._hover)
            size, color = HANDLE_SIZE, HANDLE_HOVER_COLOR if active else HANDLE_COLOR
            if handle == "rotate":
                size = ROTATION_HANDLE_SIZE
                color = ROTATION_HANDLE_HOVER_COLOR if active else ROTATION_HANDLE_COLOR
            elif handle == "diameter":
                size, color = ROTATION_HANDLE_SIZE, QColor("#ffad60" if active else _ACCENT)
            painter.setBrush(color)
            bounds = QRectF(point.x() - size / 2, point.y() - size / 2, size, size)
            if handle in ("rotate", "move", "diameter"):
                painter.drawEllipse(bounds)
                if handle == "move":
                    painter.drawLine(point - QPointF(3, 0), point + QPointF(3, 0))
                    painter.drawLine(point - QPointF(0, 3), point + QPointF(0, 3))
            else:
                painter.drawRect(bounds)
        painter.restore()
        controls = self._controls
        if controls is not None:
            viewport = self.view.viewport()
            x = round(max(p.x() for p in handles.values())) - controls.width()
            y = round(max(p.y() for p in handles.values())) + 12
            if y + controls.height() > viewport.height():
                y = round(min(p.y() for p in handles.values())) - controls.height() - 12
            controls.move(max(0, min(x, viewport.width() - controls.width())),
                          max(0, min(y, viewport.height() - controls.height())))

    def _commit(self) -> None:
        if not self._editing or self._species is None:
            return
        transform = self._transform()
        manager = getattr(self.view.window(), "_project_manager", None)
        items = [
            build_species_plant(
                self._scene, transform.map(point), self._species,
                diameter_cm=self._diameter if self._size_edited else None,
                auto_assign=bool(manager and manager.auto_assign_permaculture_layers),
            )
            for point in self._centers()
        ]
        create = CreateItemsCommand(
            self._scene, items, QCoreApplication.translate("PlantGridTool", "plants"),
        )
        if len(items) > 1 and QMessageBox.question(
            self.view,
            QCoreApplication.translate("PlantGridTool", "Plant Grid"),
            QCoreApplication.translate("PlantGridTool", "Add all plants from this grid to one group?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self.view.command_manager.execute(CompoundCommand([create, GroupCommand(self._scene, items)]))
        else:
            self.view.command_manager.execute(create)
        self._finish()

    def _finish(self) -> None:
        self.view.set_active_tool(ToolType.SELECT)

    def cancel(self) -> None:
        self._origin = None
        self._species = None
        self._angle = 0.0
        self._editing = False
        self._drag = None
        self._hover = None
        if self._size_button is not None:
            self._size_button.setChecked(False)
        if self._size_slider is not None:
            self._size_slider.hide()
        if self._preview_button is not None:
            self._preview_button.setChecked(False)
        if self._controls is not None:
            self._controls.hide()
        self.view.viewport().update()
