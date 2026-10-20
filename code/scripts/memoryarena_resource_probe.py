"""Probe an explicit official checkout; no paid calls or synthetic benchmark score.

The upstream tool is loaded in place, not copied into MAPAS. Its CSVs remain
external research inputs. Full travel evaluation also requires the flight DB.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import urllib.request
from pathlib import Path


def probe(checkout: Path, *, fetch_dataset: bool = False) -> dict:
    checkout = checkout.resolve()
    base = checkout / "env/env_systems/travel_planner_env"
    required = [
        "flights/clean_Flights_2022.csv",
        "restaurants/clean_restaurant_2022.csv",
        "accommodations/clean_accommodations_2022.csv",
        "attractions/attractions.csv",
        "googleDistanceMatrix/distance.csv",
        "background/citySet_with_states.txt",
    ]
    files = []
    for name in required:
        file = base / "database" / name
        files.append(
            {
                "path": name,
                "present": file.is_file(),
                "sha256": (
                    hashlib.sha256(file.read_bytes()).hexdigest()
                    if file.is_file()
                    else None
                ),
            }
        )
    revision = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    result = {
        "upstream": "https://github.com/ZexueHe/MemoryArena",
        "revision": revision,
        "database_files": files,
        "full_travel_ready": all(row["present"] for row in files),
        "official_group_evaluation_run": False,
        "score": None,
        "redistribution_license_verified": False,
    }
    spec = importlib.util.spec_from_file_location(
        "memoryarena_official_city_tool", base / "tools/cities.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("upstream CitySearch module not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with contextlib.redirect_stdout(io.StringIO()):
        tool = module.Cities(
            path=str(base / "database/background/citySet_with_states.txt")
        )
    state = next(iter(tool.data))
    cities = tool.run(state)
    if not isinstance(cities, list) or not cities:
        raise RuntimeError("official CitySearch returned no valid data")
    result["minimal_external_tool"] = {
        "name": "CitySearch",
        "state": state,
        "result": cities,
        "executed": True,
    }
    if fetch_dataset:
        url = "https://datasets-server.huggingface.co/rows?dataset=ZexueHe%2Fmemoryarena&config=group_travel_planner&split=test&offset=0&length=1"
        result["dataset_probe"] = {"url": url}
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("dataset preview exceeds bound")
            data = json.loads(raw)
            row = data["rows"][0]["row"]
            result["dataset_probe"].update(
                status="AVAILABLE",
                response_sha256=hashlib.sha256(raw).hexdigest(),
                group_id=row["id"],
                question_count=len(row["questions"]),
                row_keys=sorted(row),
                total_rows=data.get("num_rows_total"),
                agent_access_to_gold=False,
            )
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            result["dataset_probe"].update(
                status="UNAVAILABLE",
                error_type=type(exc).__name__,
                http_status=getattr(exc, "code", None),
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fetch-dataset", action="store_true")
    args = parser.parse_args()
    report = probe(args.checkout, fetch_dataset=args.fetch_dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "external_tool_executed": True,
                "full_travel_ready": report["full_travel_ready"],
                "dataset_status": report.get("dataset_probe", {}).get("status"),
                "report": str(args.output),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
