"""Bounded SVG artwork for the 2D plant map."""

from functools import lru_cache
from xml.etree import ElementTree

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtSvg import QSvgRenderer, QtSvg

MAX_SVG_BYTES = 256 * 1024


def plant_svg_renderer(svg: object) -> QSvgRenderer | None:
    if not isinstance(svg, str) or not svg or len(svg) > MAX_SVG_BYTES:
        return None
    return _parse_plant_svg(svg)


@lru_cache(maxsize=24)
def _parse_plant_svg(svg: str) -> QSvgRenderer | None:
    content = svg.encode("utf-8", errors="replace")
    if len(content) > MAX_SVG_BYTES or b"<!" in content:
        return None
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return None
    for element in root.iter():
        if any(
            key.rsplit("}", 1)[-1] == "href" and not value.startswith("#")
            for key, value in element.attrib.items()
        ):
            return None
    renderer = QSvgRenderer()
    renderer.setOptions(QtSvg.Option.Tiny12FeaturesOnly)
    renderer.setAnimationEnabled(False)
    if not renderer.load(QByteArray(content)):
        return None
    renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
    return renderer


def paint_map_svg(painter: QPainter, rect: QRectF, svg: str) -> bool:
    renderer = plant_svg_renderer(svg)
    if renderer is None:
        return False
    painter.save()
    painter.translate(rect.center())
    if painter.worldTransform().determinant() < 0:
        painter.scale(1, -1)
    renderer.render(painter, QRectF(-rect.width() / 2, -rect.height() / 2,
                                    rect.width(), rect.height()))
    painter.restore()
    return True
