from PyQt6.QtCore import QPoint, QPointF, Qt

from open_garden_planner.core.tools import ToolType
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items.circle_item import CircleItem
from open_garden_planner.ui.widgets.toolbar import MainToolbar


def test_hand_tool_pans_over_an_item_without_selecting_it(qtbot):
    scene = CanvasScene(width_cm=2000, height_cm=2000)
    view = CanvasView(scene)
    qtbot.addWidget(view)
    view.resize(720, 600)
    view.show()
    view.set_zoom(1)
    view.centerOn(1000, 1000)
    item = CircleItem(1000, 1000, 50)
    scene.addItem(item)
    toolbar = MainToolbar()
    qtbot.addWidget(toolbar)
    toolbar.tool_selected.connect(view.set_active_tool)
    button = toolbar._buttons[ToolType.HAND]
    assert not button.icon().isNull()
    button.click()
    tool_region = view.viewport()
    start = view.mapFromScene(QPointF(1000, 1000))
    end = start + QPoint(65, 40)
    qtbot.mousePress(tool_region, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(tool_region, end)
    qtbot.mouseRelease(tool_region, Qt.MouseButton.LeftButton, pos=end)
    assert (view.mapFromScene(QPointF(1000, 1000)) - end).manhattanLength() <= 2
    assert not scene.selectedItems()
    assert item.pos().isNull()
    assert view.command_manager.undo_depth == 0
