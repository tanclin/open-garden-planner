"""Edit a library species independently of any plant placed on the plan."""

import math
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from PIL import Image
from PyQt6.QtCore import QLocale, QRectF, Qt, QUrl, QUrlQuery
from PyQt6.QtGui import QDesktopServices, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.app.paths import default_dialog_dir
from open_garden_planner.core.plant_cycle_label import plant_cycle_label
from open_garden_planner.models.plant_data import (
    FlowerType,
    GrowthRate,
    PermacultureLayer,
    PlantCycle,
    PlantFunction,
    PlantSpeciesData,
    PollinationType,
    SunRequirement,
    WaterNeeds,
)
from open_garden_planner.services.plant_api.thumbnail_loader import (
    embedded_photo_bytes,
    get_cached_thumbnail,
    import_plant_photo,
)
from open_garden_planner.ui.number_fields import with_field_mark
from open_garden_planner.ui.plant_map_art import MAX_SVG_BYTES, plant_svg_renderer
from open_garden_planner.ui.widgets.trait_filters import trait_label


class EditCustomPlantDialog(QDialog):
    """Stage edits locally until the user saves them to the library."""

    def __init__(
        self,
        plant: PlantSpeciesData,
        save_plant: Callable[[PlantSpeciesData], bool],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit Custom Plant"))
        self.resize(620, 640)
        self._data = plant.to_dict()
        self._save_plant = save_plant
        self._fields: dict[str, tuple[Callable[[], Any], QWidget, str, int]] = {}
        self._locale = QLocale()
        self._locale.setNumberOptions(QLocale.NumberOption.OmitGroupSeparator)

        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)
        self._setup_identity()
        self._setup_growth()
        self._setup_soil()
        self._setup_calendar()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _tab(self, title: str) -> QFormLayout:
        content = QWidget()
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self._tabs.addTab(scroll, title)
        return form

    def _field(
        self, form: QFormLayout, key: str, label: str, editor: QWidget, read: Callable[[], Any],
    ) -> None:
        editor.setObjectName(key)
        form.addRow(label, editor)
        self._fields[key] = (read, editor, label, self._tabs.count() - 1)

    def _text_fields(self, form: QFormLayout, fields: list[tuple[str, str]]) -> None:
        for key, label in fields:
            editor = QLineEdit(self._data[key] or "")
            self._field(form, key, label, editor, editor.text)

    def _choice(
        self, form: QFormLayout, key: str, label: str, options: list[tuple[str, Any]],
    ) -> None:
        combo = QComboBox()
        for text, value in options:
            combo.addItem(text, value)
        current = self._data[key]
        index = combo.findData(current)
        if index == -1:
            combo.addItem(str(current), current)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        self._field(form, key, label, combo, combo.currentData)

    def _enum_choice(
        self, form: QFormLayout, key: str, label: str, options: dict[Enum, str],
    ) -> None:
        self._choice(form, key, label, [(text, option.value) for option, text in options.items()])

    def _number_fields(
        self, form: QFormLayout, fields: list[tuple[str, str]], *,
        integer: bool = False, bounds: tuple[int, int] | None = None,
    ) -> None:
        for key, label in fields:
            value = self._data[key]
            text = "" if value is None else str(value)
            if not integer:
                text = with_field_mark(text, self._locale.decimalPoint())
            editor = QLineEdit(text)
            editor.setPlaceholderText(self.tr("Unspecified"))
            if bounds is not None:
                editor.setToolTip(self.tr("Allowed range: {minimum} to {maximum}").format(
                    minimum=bounds[0], maximum=bounds[1],
                ))
            self._field(
                form, key, label, editor,
                lambda editor=editor, text=text, value=value: (
                    value if editor.text() == text
                    else self._read_number(editor.text(), integer, bounds)
                ),
            )

    def _read_number(
        self, text: str, integer: bool, bounds: tuple[int, int] | None,
    ) -> int | float | None:
        text = text.strip()
        if not text:
            return None
        if integer:
            value: int | float = int(text)
        else:
            value, valid = self._locale.toDouble(with_field_mark(text, self._locale.decimalPoint()))
            if not valid or not math.isfinite(value):
                raise ValueError
        if bounds is not None and not bounds[0] <= value <= bounds[1]:
            raise ValueError
        return value

    def _setup_identity(self) -> None:
        form = self._tab(self.tr("Identity and Photo"))
        self._text_fields(form, [
            ("common_name", self.tr("Common Name")),
            ("home_name", self.tr("Home Name")),
            ("scientific_name", self.tr("Scientific Name")),
            ("family", self.tr("Family")),
            ("genus", self.tr("Genus")),
        ])
        wiki = QPushButton(self.tr("Find on Wiki"))
        wiki.clicked.connect(self._find_on_wiki)
        form.addRow(wiki)
        self._choice(form, "plant_kind", self.tr("Kind"), [
            (self.tr("Unspecified"), ""), (self.tr("Tree"), "tree"),
            (self.tr("Shrub"), "shrub"), (self.tr("Perennial"), "perennial"),
            (self.tr("Vegetable or herb"), "vegetable"),
        ])
        self._text_fields(form, [("map_icon", self.tr("Map icon (emoji or symbol)"))])
        show_icon = QCheckBox()
        show_icon.setChecked(self._data["show_map_icon"])
        self._field(
            form, "show_map_icon", self.tr("Show icon on the map"), show_icon, show_icon.isChecked,
        )
        description = QPlainTextEdit(self._data["description"])
        description.setMaximumHeight(110)
        self._field(form, "description", self.tr("Description"), description, description.toPlainText)

        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(120)
        self._preview.setObjectName("plant_photo")
        visuals = QHBoxLayout()
        photo = QVBoxLayout()
        photo.addWidget(QLabel(self.tr("Photo")))
        photo.addWidget(self._preview)
        photo_buttons = QHBoxLayout()
        choose = QPushButton(self.tr("Choose Photo…"))
        choose.setObjectName("choose_photo")
        choose.clicked.connect(self._choose_photo)
        photo_buttons.addWidget(choose)
        self._remove_photo_button = QPushButton(self.tr("Remove Photo"))
        self._remove_photo_button.setObjectName("remove_photo")
        self._remove_photo_button.clicked.connect(lambda: self._set_photo(""))
        photo_buttons.addWidget(self._remove_photo_button)
        photo_buttons.addStretch()
        photo.addLayout(photo_buttons)
        visuals.addLayout(photo, 1)
        svg = QVBoxLayout()
        svg.addWidget(QLabel(self.tr("Map SVG")))
        self._svg_preview = QLabel()
        self._svg_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._svg_preview.setMinimumHeight(120)
        svg.addWidget(self._svg_preview)
        svg_buttons = QHBoxLayout()
        choose_svg = QPushButton(self.tr("Choose SVG…"))
        choose_svg.clicked.connect(self._choose_svg)
        svg_buttons.addWidget(choose_svg)
        self._remove_svg = QPushButton(self.tr("Remove SVG"))
        self._remove_svg.clicked.connect(lambda: self._set_svg(""))
        svg_buttons.addWidget(self._remove_svg)
        svg_buttons.addStretch()
        svg.addLayout(svg_buttons)
        visuals.addLayout(svg, 1)
        form.addRow(visuals)
        self._refresh_photo()
        self._set_svg(self._data["map_svg"])

    def _find_on_wiki(self) -> None:
        name = self._fields["common_name"][0]().strip() or self._fields["scientific_name"][0]().strip()
        if not name:
            return
        url = QUrl("https://en.wikipedia.org/w/index.php")
        query = QUrlQuery()
        query.addQueryItem("search", name)
        url.setQuery(query)
        QDesktopServices.openUrl(url)

    def _choose_svg(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, self.tr("Choose SVG…"), str(default_dialog_dir()), "SVG (*.svg)",
        )
        if not filename:
            return
        try:
            with Path(filename).open("rb") as stream:
                content = stream.read(MAX_SVG_BYTES + 1)
            svg = content.decode("utf-8-sig")
            if plant_svg_renderer(svg) is None:
                raise ValueError
        except (OSError, ValueError):
            QMessageBox.warning(self, self.windowTitle(), self.tr("Could not read this SVG."))
            return
        self._set_svg(svg)

    def _set_svg(self, svg: str) -> None:
        self._data["map_svg"] = svg
        self._remove_svg.setEnabled(bool(svg))
        renderer = plant_svg_renderer(svg)
        if renderer is None:
            self._svg_preview.setText(self.tr("No preview available"))
            return
        preview = QPixmap(120, 120)
        preview.fill(Qt.GlobalColor.transparent)
        painter = QPainter(preview)
        renderer.render(painter, QRectF(0, 0, 120, 120))
        painter.end()
        self._svg_preview.setPixmap(preview)

    def _setup_growth(self) -> None:
        form = self._tab(self.tr("Growth and Traits"))
        unknown = self.tr("Unspecified")
        self._enum_choice(form, "cycle", self.tr("Cycle"), {
            cycle: plant_cycle_label(cycle, unknown=unknown) for cycle in PlantCycle
        })
        self._enum_choice(form, "growth_rate", self.tr("Growth Rate"), {
            GrowthRate.UNKNOWN: unknown,
            GrowthRate.SLOW: self.tr("Slow"),
            GrowthRate.MEDIUM: self.tr("Medium"),
            GrowthRate.FAST: self.tr("Fast"),
        })
        self._enum_choice(form, "permaculture_layer", self.tr("Food-forest layer"), {
            layer: trait_label(layer.value) for layer in PermacultureLayer
        })
        functions = QListWidget()
        functions.setMaximumHeight(110)
        function_items: list[QListWidgetItem] = []
        for function in PlantFunction:
            item = QListWidgetItem(trait_label(function.value), functions)
            item.setData(Qt.ItemDataRole.UserRole, function.value)
            item.setCheckState(
                Qt.CheckState.Checked if function.value in self._data["functions"]
                else Qt.CheckState.Unchecked
            )
            function_items.append(item)
        self._field(form, "functions", self.tr("Recorded functions"), functions, lambda: [
            item.data(Qt.ItemDataRole.UserRole)
            for item in function_items
            if item.checkState() == Qt.CheckState.Checked
        ])
        self._number_fields(form, [
            ("min_height_cm", self.tr("Min Height (cm)")),
            ("max_height_cm", self.tr("Max Height (cm)")),
            ("min_spread_cm", self.tr("Min Spread (cm)")),
            ("max_spread_cm", self.tr("Max Spread (cm)")),
        ], bounds=(0, 100_000))
        for key, label in [("edible", self.tr("Edible")), ("flowering", self.tr("Flowering"))]:
            checkbox = QCheckBox()
            checkbox.setChecked(self._data[key])
            self._field(form, key, label, checkbox, checkbox.isChecked)
        original_parts = self._data["edible_parts"] or []
        original_text = "\n".join(original_parts)
        parts = QPlainTextEdit(original_text)
        parts.setMaximumHeight(90)
        parts.setPlaceholderText(self.tr("One edible part per line"))

        def read_parts() -> list[str]:
            text = parts.toPlainText()
            if text == original_text:
                return original_parts
            return [line.strip() for line in text.splitlines() if line.strip()]

        self._field(form, "edible_parts", self.tr("Edible Parts"), parts, read_parts)
        self._text_fields(form, [
            ("flower_color", self.tr("Flower Color")),
            ("foliage_color", self.tr("Foliage Color")),
            ("foliage_texture", self.tr("Foliage Texture")),
        ])
        self._enum_choice(form, "flower_type", self.tr("Flower Type"), {
            FlowerType.UNKNOWN: unknown,
            FlowerType.HERMAPHRODITE: self.tr("Hermaphrodite (perfect flowers)"),
            FlowerType.MONOECIOUS: self.tr("Monoecious (separate ♂/♀ flowers)"),
            FlowerType.DIOECIOUS_MALE: self.tr("Dioecious Male (♂ only)"),
            FlowerType.DIOECIOUS_FEMALE: self.tr("Dioecious Female (♀ only)"),
        })
        self._enum_choice(form, "pollination_type", self.tr("Pollination"), {
            PollinationType.UNKNOWN: unknown,
            PollinationType.SELF_FERTILE: self.tr("Self-fertile (no partner needed)"),
            PollinationType.PARTIALLY_SELF_FERTILE: self.tr("Partially self-fertile"),
            PollinationType.SELF_STERILE: self.tr("Self-sterile (needs partner)"),
            PollinationType.TRIPLOID: self.tr("Triploid (sterile pollen)"),
        })

    def _setup_soil(self) -> None:
        form = self._tab(self.tr("Growing Conditions"))
        unknown = self.tr("Unspecified")
        self._enum_choice(form, "sun_requirement", self.tr("Sun"), {
            SunRequirement.UNKNOWN: unknown,
            SunRequirement.FULL_SUN: self.tr("Full Sun"),
            SunRequirement.PARTIAL_SUN: self.tr("Partial Sun"),
            SunRequirement.PARTIAL_SHADE: self.tr("Partial Shade"),
            SunRequirement.FULL_SHADE: self.tr("Full Shade"),
        })
        low, medium, high = self.tr("Low"), self.tr("Medium"), self.tr("High")
        self._enum_choice(form, "water_needs", self.tr("Water"), {
            WaterNeeds.UNKNOWN: unknown, WaterNeeds.LOW: low, WaterNeeds.MEDIUM: medium,
            WaterNeeds.HIGH: high,
        })
        self._number_fields(form, [
            ("hardiness_zone_min", self.tr("Min Hardiness Zone")),
            ("hardiness_zone_max", self.tr("Max Hardiness Zone")),
        ], integer=True, bounds=(0, 14))
        self._choice(form, "frost_tolerance", self.tr("Frost Tolerance"), [
            (unknown, None), (self.tr("Hardy"), "hardy"),
            (self.tr("Half-hardy"), "half-hardy"), (self.tr("Tender"), "tender"),
        ])
        self._text_fields(form, [("soil_type", self.tr("Soil Type"))])
        self._number_fields(form, [
            ("ph_min", self.tr("Min pH")), ("ph_max", self.tr("Max pH")),
        ], bounds=(0, 14))
        fixer = self.tr("Fixer (legume)")
        self._choice(form, "nutrient_demand", self.tr("Overall demand"), [
            (unknown, None), (self.tr("Light feeder"), "light"),
            (self.tr("Medium feeder"), "medium"), (self.tr("Heavy feeder"), "heavy"),
            (fixer, "fixer"),
        ])
        demands = [(unknown, None), (low, "low"), (medium, "medium"), (high, "high"),
                   (fixer, "fixer")]
        self._choice(form, "n_demand", self.tr("N demand"), demands)
        self._choice(form, "p_demand", self.tr("P demand"), demands)
        self._choice(form, "k_demand", self.tr("K demand"), demands)

    def _setup_calendar(self) -> None:
        form = self._tab(self.tr("Planting Calendar"))
        help_text = QLabel(self.tr(
            "Enter calendar offsets in weeks from the last spring frost. "
            "Use negative numbers for dates before frost."
        ))
        help_text.setWordWrap(True)
        form.addRow(help_text)
        self._number_fields(form, [
            ("indoor_sow_start", self.tr("Indoor Sowing Start")),
            ("indoor_sow_end", self.tr("Indoor Sowing End")),
            ("direct_sow_start", self.tr("Direct Sowing Start")),
            ("direct_sow_end", self.tr("Direct Sowing End")),
            ("transplant_start", self.tr("Transplant Start")),
            ("transplant_end", self.tr("Transplant End")),
            ("harvest_start", self.tr("Harvest Start")),
            ("harvest_end", self.tr("Harvest End")),
        ], integer=True, bounds=(-104, 104))
        self._number_fields(form, [
            ("days_to_germination_min", self.tr("Min Days to Germination")),
            ("days_to_germination_max", self.tr("Max Days to Germination")),
            ("days_to_maturity_min", self.tr("Min Days to Maturity")),
            ("days_to_maturity_max", self.tr("Max Days to Maturity")),
            ("prick_out_after_days", self.tr("Prick Out After (days)")),
            ("harden_off_days", self.tr("Hardening Off (days)")),
        ], integer=True, bounds=(0, 3650))
        self._number_fields(form, [
            ("min_germination_temp_c", self.tr("Min Germination Temperature (°C)")),
        ], bounds=(-100, 100))
        self._number_fields(form, [
            ("seed_depth_cm", self.tr("Seed Depth (cm)")),
        ], bounds=(0, 1000))

    def _choose_photo(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, self.tr("Select photo"), str(default_dialog_dir()),
            self.tr("Images (*.png *.jpg *.jpeg *.gif *.webp)"),
        )
        if not filename:
            return
        try:
            self._set_photo(import_plant_photo(Path(filename)))
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            QMessageBox.warning(
                self, self.windowTitle(), self.tr("Could not read this photo."),
            )

    def _set_photo(self, url: str) -> None:
        self._data["image_url"] = url
        self._data["thumbnail_url"] = ""
        self._refresh_photo()

    def _refresh_photo(self) -> None:
        url = self._data["thumbnail_url"] or self._data["image_url"]
        pixmap = QPixmap()
        if url:
            content = embedded_photo_bytes(url)
            if content:
                pixmap.loadFromData(content, "PNG")
            elif not url.startswith("data:"):
                cached = get_cached_thumbnail(url)
                if cached is not None:
                    pixmap.load(str(cached))
        self._remove_photo_button.setEnabled(bool(url))
        if pixmap.isNull():
            self._preview.setText(self.tr("No preview available") if url else self.tr("(no photo)"))
        else:
            self._preview.setPixmap(pixmap.scaled(
                160, 120, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def accept(self) -> None:
        data = self._data.copy()
        for key, (read, editor, label, tab) in self._fields.items():
            try:
                data[key] = read()
            except ValueError:
                self._tabs.setCurrentIndex(tab)
                QMessageBox.warning(
                    self, self.windowTitle(),
                    self.tr("Enter a valid number for {field}.").format(field=label),
                )
                editor.setFocus()
                return
        if not (data["common_name"].strip() or data["scientific_name"].strip()):
            self._tabs.setCurrentIndex(0)
            QMessageBox.warning(
                self, self.windowTitle(), self.tr("Enter a common or scientific name."),
            )
            self._fields["common_name"][1].setFocus()
            return
        if not self._save_plant(PlantSpeciesData.from_dict(data)):
            QMessageBox.warning(
                self, self.windowTitle(), self.tr("Could not save changes to the plant library."),
            )
            return
        super().accept()
