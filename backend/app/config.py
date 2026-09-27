from functools import lru_cache

from pydantic import AliasChoices, Field
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
    # TMD NWP runs a few times a day and every call spends datapoint quota.
    "tmd-provinces": 10800,
    "tmd-tambon": 10800,
    "tmd-hourly": 3600,
    "dam-photos": 604800,  # curated Commons files; only credits could change
    "local-news": 900,
    "local-social": 600,
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

    # GISTDA Disaster Platform key (api-gateway.gistda.or.th); flood layers stay off until set.
    gistda_api_key: str = ""

    # TomTom Traffic Flow tiles; the traffic layer stays off until this is set.
    tomtom_api_key: str = ""

    # TMD NWP forecast API bearer token (data.tmd.go.th/nwpapi). TDM_API_KEY is accepted
    # too because that is how the key was first written into .env.
    tmd_api_key: str = Field("", validation_alias=AliasChoices("tmd_api_key", "TMD_API_KEY", "TDM_API_KEY"))
    # Leave quota for the national forecast and high-priority flood-area lookups.
    tmd_quota_reserve: int = 5000
    # Stay below TMD's observed 60 requests/minute ceiling.
    tmd_requests_per_minute: int = 50

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    def ttl(self, source: str) -> int:
        value = self.refresh_ttl.get(source, DEFAULT_REFRESH_TTL.get(source, self.cache_ttl_seconds))
        return max(30, int(value))  # guard against hammering upstreams via a typo


@lru_cache
def get_settings() -> Settings:
    return Settings()
