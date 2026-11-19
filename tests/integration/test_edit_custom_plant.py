"""Manage Custom Plants edits the selected library record and its photo."""

import io
from dataclasses import replace

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, QTimer
from PyQt6.QtGui import QDesktopServices, QTransform
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from open_garden_planner.app.settings import get_settings
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services import plant_library
from open_garden_planner.services.bundled_species_db import merge_calendar_data
from open_garden_planner.services.plant_api import thumbnail_loader
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items.circle_item import CircleItem
from open_garden_planner.ui.dialogs.custom_plants_dialog import CustomPlantsDialog
from open_garden_planner.ui.dialogs.edit_custom_plant_dialog import EditCustomPlantDialog
from open_garden_planner.ui.panels.plant_database_panel import PlantDatabasePanel
from open_garden_planner.ui.plant_creation import build_species_plant
from open_garden_planner.ui.plant_map_art import plant_svg_renderer
from open_garden_planner.ui.widgets.category_dropdown import CategoryDropdown
from open_garden_planner.ui.widgets.gallery_data import build_toolbar_categories


def test_manage_custom_plant_edit_persists_and_cancels(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(plant_library, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(plant_library, "_library_instance", None)
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(thumbnail_loader, "get_cache_dir", lambda: cache)
    library = plant_library.get_plant_library()
    original = PlantSpeciesData(
        "Solanum lycopersicum", "Tomato", raw_data={"provider": {"notes": "original"}},
        thumbnail_url="https://example.invalid/old-photo.png",
    )
    plant_id = library.add_plant(original)
    manager = CustomPlantsDialog()
    qtbot.addWidget(manager)
    assert not manager.edit_btn.isEnabled()
    manager.table.selectRow(0)
    assert manager.edit_btn.isEnabled()

    photo = tmp_path / "tomato.png"
    Image.new("RGBA", (48, 64), (210, 0, 0, 128)).save(photo)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(photo), ""))

    def edit():
        editor = manager.findChild(EditCustomPlantDialog)
        try:
            assert set(editor._fields) == set(original.to_dict()) - {
                "image_url", "thumbnail_url", "source_id", "data_source", "slug", "raw_data",
                "map_svg",
            }
            editor.findChild(QLineEdit, "common_name").setText("Garden tomato")
            editor.findChild(QLineEdit, "max_height_cm").setText("275,25")
            editor.findChild(QLineEdit, "indoor_sow_start").setText("-4")
            editor.findChild(QPushButton, "choose_photo").click()
            assert not editor.findChild(QLabel, "plant_photo").pixmap().isNull()
            editor.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        finally:
            if editor.result() != QDialog.DialogCode.Accepted:
                editor.reject()

    QTimer.singleShot(0, edit)
    manager.edit_btn.click()
    assert manager.table.item(0, 0).text() == "Garden tomato"
    assert manager.selected_plant_id == plant_id
    saved = plant_library.PlantLibrary().get_plant(plant_id)
    assert saved is not None
    assert saved.source_id == plant_id
    assert saved.max_height_cm == 275.25
    assert saved.indoor_sow_start == -4
    assert merge_calendar_data(saved.to_dict())["indoor_sow_start"] == -4
    assert saved.raw_data == original.raw_data
    assert saved.thumbnail_url == ""
    photo.unlink()
    cached = thumbnail_loader.fetch_thumbnail(saved.image_url)
    assert cached is not None
    with Image.open(io.BytesIO(cached.read_bytes())) as image:
        assert image.getpixel((0, 0)) == (210, 0, 0, 128)

    def remove_photo(*, save: bool):
        editor = manager.findChild(EditCustomPlantDialog)
        button = QDialogButtonBox.StandardButton.Save if save else QDialogButtonBox.StandardButton.Cancel
        editor.findChild(QPushButton, "remove_photo").click()
        editor.findChild(QDialogButtonBox).button(button).click()

    for save in (False, True):
        # The previous modal editor is queued for deletion.
        qtbot.waitUntil(lambda: manager.findChild(EditCustomPlantDialog) is None)
        QTimer.singleShot(0, lambda save=save: remove_photo(save=save))
        manager.edit_btn.click()
        assert manager.selected_plant.image_url == ("" if save else saved.image_url)
        assert plant_library.PlantLibrary().get_plant(plant_id).image_url == (
            "" if save else saved.image_url
        )


def test_save_failure_leaves_the_plant_and_editor_intact(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(plant_library, "get_app_data_dir", lambda: tmp_path)
    library = plant_library.PlantLibrary()
    original = PlantSpeciesData("Pyrus communis", "Pear")
    plant_id = library.add_plant(original)
    editor = EditCustomPlantDialog(original, lambda plant: library.update_plant(plant_id, plant))
    qtbot.addWidget(editor)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    editor.show()
    editor.findChild(QLineEdit, "common_name").setText("Edited pear")
    sow_start = editor.findChild(QLineEdit, "indoor_sow_start")
    sow_start.setText("999999999")
    editor.accept()
    assert len(warnings) == 1
    assert library.get_plant(plant_id) is original
    sow_start.clear()

    def locked_file(*_args):
        raise PermissionError("Library is locked")

    with monkeypatch.context() as patch:
        patch.setattr(plant_library.os, "replace", locked_file)
        editor.accept()
    assert len(warnings) == 2
    assert editor.isVisible()
    assert library.get_plant(plant_id) is original
    assert plant_library.PlantLibrary().get_plant(plant_id).common_name == "Pear"
    editor.accept()
    assert editor.result() == QDialog.DialogCode.Accepted
    assert plant_library.PlantLibrary().get_plant(plant_id).common_name == "Edited pear"


def test_placed_plant_edit_preserves_newer_library_fields(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(plant_library, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(plant_library, "_library_instance", None)
    library = plant_library.get_plant_library()
    original = PlantSpeciesData("Malus domestica", "Apple")
    plant_id = library.add_plant(original)
    placed = CircleItem(0.0, 0.0, 25.0, object_type=ObjectType.TREE)
    placed.metadata["plant_species"] = original.to_dict()
    panel = PlantDatabasePanel()
    qtbot.addWidget(panel)
    panel.set_selected_items([placed])

    assert library.update_plant(plant_id, replace(
        original, common_name="Garden apple", image_url="https://example.invalid/new.png",
    ))
    panel.family_edit.setText("Rosaceae")
    panel._on_field_changed()

    current = plant_library.PlantLibrary().get_plant(plant_id)
    assert current.common_name == "Garden apple"
    assert current.image_url == "https://example.invalid/new.png"
    assert current.family == "Rosaceae"
    assert placed.metadata["plant_species"]["common_name"] == "Apple"


def test_custom_kind_wiki_svg_and_badge(qtbot, monkeypatch, tmp_path):
    data = PlantSpeciesData("Malus domestica", "Apple")
    saved = []
    def save(plant):
        saved.append(plant)
        return True

    editor = EditCustomPlantDialog(data, save)
    qtbot.addWidget(editor)
    editor.findChild(QLineEdit, "home_name").setText("By the barn")
    editor.findChild(QLineEdit, "map_icon").setText("🍎")
    kind = editor.findChild(QComboBox, "plant_kind")
    kind.setCurrentIndex(kind.findData("tree"))
    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", urls.append)
    editor._find_on_wiki()
    assert urls[0].host() == "en.wikipedia.org"
    assert urls[0].query() == "search=Apple"

    svg = tmp_path / "plant.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
                   '<circle cx="20" cy="20" r="18" fill="red"/></svg>', encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(svg), ""))
    editor._choose_svg()
    assert not editor._svg_preview.pixmap().isNull()
    assert not editor.findChild(QCheckBox, "show_map_icon").isChecked()
    editor.accept()
    plant = PlantSpeciesData.from_dict(saved[0].to_dict())
    assert plant.display_name == "Apple (By the barn)"
    assert plant_svg_renderer(plant.map_svg) is not None

    scene = CanvasScene(width_cm=1000, height_cm=1000)
    view = CanvasView(scene)
    qtbot.addWidget(view)
    item = build_species_plant(scene, QPointF(150, 150), plant.to_dict())
    scene.addItem(item)
    assert item.object_type == ObjectType.TREE
    assert plant.home_name not in item._get_display_label_text()

    class BadgePainter:
        def __init__(self, zoom):
            self.zoom = zoom
            self.glyphs = []

        def worldTransform(self):
            return QTransform.fromScale(self.zoom, -self.zoom)

        def setFont(self, font):
            self.size = font.pixelSize()

        def drawText(self, box, _alignment, icon):
            self.glyphs.append((self.size, box.center(), icon))

        def __getattr__(self, name):
            if name in {"save", "restore", "resetTransform", "setPen"}:
                return lambda *_args: None
            raise AttributeError(name)

    settings = get_settings()
    settings.plant_map_icon_size = 36
    drawn = []

    def capture_svg(_painter, box, content):
        assert content == plant.map_svg
        drawn.append(box)
        return True

    monkeypatch.setattr("open_garden_planner.ui.plant_map_art.paint_map_svg", capture_svg)
    painter = BadgePainter(2)
    view._draw_plant_map_icons(painter, QRectF(0, 0, 1000, 1000))
    assert not painter.glyphs and not drawn
    item.metadata["plant_species"]["show_map_icon"] = True
    settings.plant_map_icons_fixed = True
    for zoom in (0.02, 12):
        view._draw_plant_map_icons(BadgePainter(zoom), QRectF(0, 0, 1000, 1000))
        assert drawn[-1].width() == drawn[-1].height() == 36
    settings.hide_plant_svg_icons = True
    drawn.clear()
    view._draw_plant_map_icons(BadgePainter(2), QRectF(0, 0, 1000, 1000))
    assert not drawn
    settings.hide_plant_svg_icons = False
    item.metadata["plant_species"]["map_svg"] = ""
    for fixed, expected in ((True, (36, 36)), (False, (18, 72))):
        settings.plant_map_icons_fixed = fixed
        for zoom, size in zip((0.5, 2), expected, strict=True):
            painter = BadgePainter(zoom)
            view._draw_plant_map_icons(painter, QRectF(0, 0, 1000, 1000))
            assert painter.glyphs == [(size, QPointF(150 * zoom, -150 * zoom), "🍎")]


def test_category_folder_only_shows_matching_custom_kind(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(plant_library, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(plant_library, "_library_instance", None)
    library = plant_library.get_plant_library()
    library.add_plant(PlantSpeciesData("Malus", "Apple", home_name="Gate", plant_kind="tree"))
    library.add_plant(PlantSpeciesData("Ribes", "Currant", plant_kind="shrub"))
    library.add_plant(PlantSpeciesData("Unknown", "Unclassified"))
    trees = next(cat for cat in build_toolbar_categories() if cat.icon_name == "tree")
    dropdown = CategoryDropdown(trees)
    qtbot.addWidget(dropdown)
    folder = dropdown._custom_folder
    assert not folder.isHidden()
    assert dropdown._grid.getItemPosition(dropdown._grid.indexOf(folder))[:2] == (0, 0)
    received = []
    dropdown.item_selected.connect(received.append)

    def pick(dialog):
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 0).text() == "Apple (Gate)"
        dialog.table.selectRow(0)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CustomPlantsDialog, "exec", pick)
    folder.click()
    assert received[0].species_record["home_name"] == "Gate"
    assert received[0].object_type == ObjectType.TREE


def test_duplicate_has_its_own_id_and_double_click_edits(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(plant_library, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(plant_library, "_library_instance", None)
    library = plant_library.get_plant_library()
    original = PlantSpeciesData("Malus", "Apple", map_icon="🍎", edible_parts=["fruit"])
    original_id = library.add_plant(original)
    dialog = CustomPlantsDialog()
    qtbot.addWidget(dialog)
    assert not dialog.duplicate_btn.isEnabled()
    dialog.table.selectRow(0)
    dialog.duplicate_btn.click()
    duplicate = dialog.selected_plant
    duplicate_id = dialog.selected_plant_id
    assert duplicate_id != original_id
    assert library.count == 2
    assert duplicate.map_icon == original.map_icon
    assert duplicate.edible_parts == original.edible_parts
    assert duplicate.edible_parts is not original.edible_parts
    assert plant_library.PlantLibrary().get_plant(duplicate_id) == duplicate

    opened = []

    def edit(editor):
        opened.append(editor)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(EditCustomPlantDialog, "exec", edit)
    dialog.table.itemDoubleClicked.emit(dialog.table.item(dialog.table.currentRow(), 0))
    assert len(opened) == 1
    picker = CustomPlantsDialog(picker=True)
    qtbot.addWidget(picker)
    picker.table.itemDoubleClicked.emit(picker.table.item(0, 0))
    assert picker.result() == QDialog.DialogCode.Accepted
    assert len(opened) == 1
