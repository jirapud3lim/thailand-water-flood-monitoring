"""Measure how often each upstream actually publishes new data (Phase 0).

Polls every source on a fixed interval and appends one CSV row per source per
round. A second mode summarises the CSV into a per-source cadence table and a
suggested cache TTL.

Run from the project root, on a machine inside Thailand (some sources are
geo-restricted):

    python scripts/probe_cadence.py --interval 60 --duration 3h --out data/cadence.csv
    python scripts/probe_cadence.py --summarize data/cadence.csv
"""

import argparse
import asyncio
import csv
import hashlib
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.freshness import BANGKOK_TZ, max_observed, parse_observed  # noqa: E402
from backend.app.services.thaiwater import LAYERS, THAIWATER_BASE_URL  # noqa: E402
from backend.app.services import upstreams  # noqa: E402

THAILAND_BBOX = (97.0, 5.0, 106.0, 21.0)
FIELDNAMES = [
    "probed_at", "source", "http_status", "elapsed_ms", "bytes",
    "features", "observed_at_max", "payload_sha1", "error",
]


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    params: dict[str, Any] | None
    to_observed: Callable[[Any], tuple[int, str | None]]  # payload -> (feature count, max observed)


def _thaiwater(layer: str) -> Source:
    spec = LAYERS[layer]

    def extract(payload: Any) -> tuple[int, str | None]:
        features = spec.normalizer(payload)
        return len(features), max_observed({"features": features})

    return Source(f"thaiwater:{layer}", f"{THAIWATER_BASE_URL}/{spec.path}", None, extract)


def _arcgis(name: str, url: str, to_local: Callable[[Any], Any] = lambda v: v) -> Source:
    params = upstreams._arcgis_bbox_params(THAILAND_BBOX, "DATA_DT")

    def extract(payload: Any) -> tuple[int, str | None]:
        features = payload.get("features", [])
        return len(features), max_observed({"features": [
            {"properties": {"observed_at": to_local((f.get("properties") or {}).get("DATA_DT"))}} for f in features
        ]})

    return Source(name, url, params, extract)


def _radar(payload: Any) -> tuple[int, str | None]:
    frames = (payload.get("radar") or {}).get("past") or []
    latest = max((f.get("time", 0) for f in frames), default=None)
    return len(frames), max_observed({"observed_at": latest})


def _dams(payload: Any) -> tuple[int, str | None]:
    count = sum(len(group.get("dam", [])) for group in payload.get("data", []))
    return count, max_observed({"observed_at": payload.get("date")})


SOURCES = [
    *(_thaiwater(layer) for layer in LAYERS),
    _arcgis("dpm:river", upstreams.DPM_RIVER_STATION_URL),
    _arcgis("dpm:road-flood", upstreams.DPM_ROAD_FLOOD_URL, upstreams.dpm_utc_to_local),  # DATA_DT is UTC
    Source("rainviewer", upstreams.RAINVIEWER_METADATA_URL, None, _radar),
    Source("rid:dams", upstreams.RID_DAM_URL, None, _dams),
]


async def probe(client: httpx.AsyncClient, source: Source) -> dict[str, Any]:
    row: dict[str, Any] = {"probed_at": datetime.now(BANGKOK_TZ).isoformat(timespec="seconds"),
                           "source": source.name}
    started = time.perf_counter()
    try:
        response = await client.get(source.url, params=source.params)
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        row["http_status"] = response.status_code
        row["bytes"] = len(response.content)
        row["payload_sha1"] = hashlib.sha1(response.content).hexdigest()[:12]
        response.raise_for_status()
        row["features"], row["observed_at_max"] = source.to_observed(response.json())
    except Exception as exc:  # record and keep probing; one bad source must not stop the run
        row.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000))
        row["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return row


def parse_duration(text: str) -> float:
    units = {"s": 1, "m": 60, "h": 3600}
    return float(text[:-1]) * units[text[-1]] if text[-1] in units else float(text)


async def run(interval: float, duration: float, out: Path, timeout: float) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    deadline = time.monotonic() + duration
    headers = {"User-Agent": "thailand-flood-monitor-cadence-probe/0.1"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        with out.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            if new_file:
                writer.writeheader()
            rounds = 0
            while time.monotonic() < deadline:
                started = time.monotonic()
                rows = await asyncio.gather(*(probe(client, s) for s in SOURCES))
                writer.writerows(rows)
                handle.flush()
                rounds += 1
                failed = [r["source"] for r in rows if r.get("error")]
                print(f"[{rows[0]['probed_at']}] round {rounds}: {len(rows) - len(failed)}/{len(rows)} ok"
                      + (f" · failed: {', '.join(failed)}" if failed else ""), flush=True)
                await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))


def summarize(path: Path) -> None:
    by_source: dict[str, list[dict[str, str]]] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            by_source.setdefault(row["source"], []).append(row)

    header = (f"{'source':<22}{'rounds':>7}{'errors':>7}{'changes':>8}"
              f"{'cadence p50':>12}{'p90':>8}{'lag p50':>9}{'KB p50':>8}{'TTL':>7}")
    print(header)
    print("-" * len(header))
    for name, rows in sorted(by_source.items()):
        errors = sum(1 for r in rows if r["error"])
        ok_rows = [r for r in rows if not r["error"] and r["observed_at_max"]]
        # A "change" is the first round that saw a new max observation time.
        changes: list[tuple[datetime, datetime]] = []
        last_seen = None
        for row in ok_rows:
            if row["observed_at_max"] != last_seen:
                observed = parse_observed(row["observed_at_max"])
                probed = parse_observed(row["probed_at"])
                if last_seen is not None and observed and probed:
                    changes.append((observed, probed))
                last_seen = row["observed_at_max"]
        gaps = [(b[0] - a[0]).total_seconds() / 60 for a, b in zip(changes, changes[1:])]
        lags = [(probed - observed).total_seconds() / 60 for observed, probed in changes]
        sizes = [int(r["bytes"]) / 1024 for r in rows if r["bytes"]]

        def pct(values: list[float], q: float, unit: str = "") -> str:
            if not values:
                return "—"
            ordered = sorted(values)
            return f"{ordered[min(len(ordered) - 1, int(q * len(ordered)))]:.0f}{unit}"

        cadence_min = statistics.median(gaps) if gaps else None
        ttl = f"{max(60, min(600, round(cadence_min * 60 / 3)))}s" if cadence_min else "—"
        print(f"{name:<22}{len(rows):>7}{errors:>7}{len(changes):>8}"
              f"{pct(gaps, .5, ' min'):>12}{pct(gaps, .9):>8}{pct(lags, .5, ' min'):>9}"
              f"{pct(sizes, .5):>8}{ttl:>7}")
    print("\ncadence = minutes between new observation times; lag = seen minus observed;"
          "\nTTL = clamp(cadence p50 / 3, 60 s, 600 s). Need ≥3 changes per source to trust a row.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=float, default=60, help="seconds between rounds (default 60)")
    parser.add_argument("--duration", default="3h", help="total run time, e.g. 90m, 3h (default 3h)")
    parser.add_argument("--timeout", type=float, default=20, help="per-request timeout in seconds")
    parser.add_argument("--out", type=Path, default=Path("data/cadence.csv"))
    parser.add_argument("--summarize", type=Path, metavar="CSV", help="summarise an existing CSV and exit")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
        return
    try:
        asyncio.run(run(args.interval, parse_duration(args.duration), args.out, args.timeout))
    except KeyboardInterrupt:
        print("\nstopped; rows so far are saved in", args.out)


if __name__ == "__main__":
    main()
