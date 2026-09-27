"""Guard the moved map controls against duplicate ids and sidebar regressions."""

from html.parser import HTMLParser
from pathlib import Path


HTML = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


class _Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.owners = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.owners.setdefault(values["id"], []).append(tuple(self.stack))
        if tag not in {"input", "img", "br", "hr", "meta", "link", "source", "path", "circle", "rect"}:
            self.stack.append(values.get("id") or tag)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.owners.setdefault(values["id"], []).append(tuple(self.stack))


def test_primary_map_overlays_have_one_control_in_map_panel():
    parser = _Controls()
    parser.feed(HTML.read_text(encoding="utf-8"))
    for control in ("radar-toggle", "radar-range", "radar-play", "flood-toggle", "flood-window", "flood-freq-toggle", "risk-only-toggle"):
        assert len(parser.owners[control]) == 1
        assert "map-layer-panel" in parser.owners[control][0]


def test_map_panel_toggle_targets_panel():
    text = HTML.read_text(encoding="utf-8")
    assert 'id="map-layer-toggle"' in text
    assert 'aria-controls="map-layer-panel"' in text
