import pytest

from backend.app.config import Settings, get_settings
from backend.app.main import app


@pytest.fixture(autouse=True)
def isolated_settings():
    # Ignore the developer's .env so real API keys never make tests call live upstreams.
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture(autouse=True)
def offline_thaiwater(monkeypatch):
    # /flood-points links ThaiWater twins; keep that lookup off the network unless a test fakes it.
    from backend.app.api import routes

    async def offline(*args, **kwargs):
        raise RuntimeError("ThaiWater offline in tests")

    monkeypatch.setattr(routes, "fetch_thaiwater_layer", offline)
