"""Guard the moved map controls against duplicate ids and sidebar regressions."""

from html.parser import HTMLParser
from pathlib import Path


HTML = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
APP = Path(__file__).resolve().parents[1] / "frontend" / "app.js"


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
    for control in ("radar-toggle", "flood-toggle", "flood-window", "flood-freq-toggle", "terrain-toggle", "risk-only-toggle"):
        assert len(parser.owners[control]) == 1
        assert "map-layer-panel" in parser.owners[control][0]
    # Radar time controls live in the Windy-style timeline on the map: still exactly one of each,
    # and never back in the sidebar.
    for control in ("radar-range", "radar-play", "radar-speed"):
        assert len(parser.owners[control]) == 1
        assert "radar-timeline" in parser.owners[control][0]
        assert "sidebar" not in parser.owners[control][0]


def test_map_panel_toggle_targets_panel():
    text = HTML.read_text(encoding="utf-8")
    assert 'id="map-layer-toggle"' in text
    assert 'aria-controls="map-layer-panel"' in text


def test_social_links_are_separate_from_official_news():
    text = HTML.read_text(encoding="utf-8")
    assert 'id="local-news-section"' in text
    assert 'id="local-social-section"' in text
    assert 'id="local-social-list"' in text
    assert "<iframe" not in text


def test_news_is_visible_near_dashboard_overview_and_has_province_picker():
    text = HTML.read_text(encoding="utf-8")
    assert 'id="local-news-section" hidden' not in text
    assert 'id="local-news-select"' in text
    assert 'data-jump="local-news-title"' in text
    assert text.index('id="overview-title"') < text.index('id="local-news-section"') < text.index('id="priority-title"')


def test_elevation_overlay_has_independent_legend_and_is_off_by_default():
    text = HTML.read_text(encoding="utf-8")
    assert 'id="terrain-toggle" type="checkbox" />' in text
    assert 'id="terrain-legend"' in text
    assert 'id="terrain-legend" class="terrain-legend" role="note" aria-label="คำอธิบายสีความสูงภูมิประเทศ" hidden' in text
    assert 'src="/terrain.js"' in text


def test_map_tile_requests_and_viewport_queries_stay_within_thailand():
    text = APP.read_text(encoding="utf-8")
    assert "map.setMaxBounds(THAILAND_BOUNDS)" in text
    assert "window.ThailandClip.intersectsRect" in text
    assert "L.polygon(window.ThailandClip.maskRings()" in text
    assert text.count("bounds: THAILAND_BOUNDS") == text.count("new ThailandTileLayer(") + text.count("new TerrainTileLayer(")
    assert text.count("thailandViewport()") == 5  # helper plus four viewport API requests
    html = HTML.read_text(encoding="utf-8")
    assert html.index('src="/thailand-boundary.js"') < html.index('src="/country-clip.js"') < html.index('src="/app.js"')
