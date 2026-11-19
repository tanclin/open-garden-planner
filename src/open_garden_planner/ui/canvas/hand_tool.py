"""Pan the viewport with the primary mouse button."""

from PyQt6.QtCore import QCoreApplication, QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from open_garden_planner.core.tools.base_tool import BaseTool, ToolType


class HandTool(BaseTool):
    tool_type = ToolType.HAND
    shortcut = ""
    cursor = Qt.CursorShape.OpenHandCursor
    skip_anchor_snap = True
    accepts_typed_coordinates = False
    _last: QPointF | None = None

    def _translated_name(self) -> str:
        return QCoreApplication.translate("HandTool", "Hand")

    def mouse_press(self, event: QMouseEvent, _scene_pos: QPointF) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self._last = event.position()
        self.view.setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def mouse_move(self, event: QMouseEvent, _scene_pos: QPointF) -> bool:
        previous = self._last
        if previous is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._last = event.position()
            self.view.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.view._pan_by(self._last - previous)
        else:
            self.cancel()
        return True

    def mouse_release(self, event: QMouseEvent, _scene_pos: QPointF) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._last is not None:
            delta = event.position() - self._last
            if not delta.isNull():
                self.view._pan_by(delta)
        self.cancel()
        return True

    def cancel(self) -> None:
        self._last = None
        if self.is_active:
            self.view.setCursor(self.cursor)
