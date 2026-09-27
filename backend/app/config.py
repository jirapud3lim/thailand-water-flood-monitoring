from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Cache TTL per source, in seconds. Rule: clamp(median upstream cadence / 3, 60, 600).
# Measured with scripts/probe_cadence.py on 2026-09-27 15:23–15:43 (21 rounds, 0 errors):
#   canal 5 min · water-level / watergate / radar 10 min · DPM road flood ~15 min.
# rain (hourly), DPM river / RID dams / GloFAS (daily) were not observed changing in that
# window; their values are conservative until a run spans at least two top-of-hour updates.
DEFAULT_REFRESH_TTL: dict[str, int] = {
    "radar": 200,
    "flood-points": 300,
    "thaiwater-canal": 100,
    "thaiwater-watergate": 200,
    "thaiwater-water-level": 200,
    "thaiwater-rain": 600,
    "gistda": 900,
    "weather": 300,
    "river": 3600,
    "dams": 3600,
}


class Settings(BaseSettings):
    app_env: str = "development"
    http_timeout_seconds: float = 12.0
    # Fallback TTL for any source missing from refresh_ttl.
    cache_ttl_seconds: int = 300
    # Partial overrides merge over the defaults, e.g. REFRESH_TTL='{"thaiwater-canal": 60}'.
    refresh_ttl: dict[str, int] = Field(default_factory=dict)
    cache_max_entries: int = 512
    upstream_retries: int = 1

    gistda_flood_url: str = ""
    gistda_api_key: str = ""
    gistda_api_key_header: str = "api-key"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def ttl(self, source: str) -> int:
        value = self.refresh_ttl.get(source, DEFAULT_REFRESH_TTL.get(source, self.cache_ttl_seconds))
        return max(30, int(value))  # guard against hammering upstreams via a typo


@lru_cache
def get_settings() -> Settings:
    return Settings()
