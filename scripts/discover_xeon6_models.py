#!/usr/bin/env python3
"""Discover every Recipes model that currently exposes a Xeon 6 rendering."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def fetch_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "xeon-recipes-weekly-ci/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def xeon_rendering(base_url: str, hf_id: str, timeout: int = 20):
    url = f"{base_url.rstrip('/')}/{hf_id}/hw/xeon6.json"
    try:
        data = fetch_json(url, timeout)
        return {"hf_id": hf_id, "recipe_url": url, "recipe": data}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="https://recipes.vllm.ai")
    ap.add_argument("--output", required=True)
    ap.add_argument("--jobs", type=int, default=16)
    args = ap.parse_args()

    catalog_url = f"{args.base_url.rstrip('/')}/models.json"
    catalog = fetch_json(catalog_url)
    entries = [x for x in catalog if x.get("hf_id")]

    discovered = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(xeon_rendering, args.base_url, e["hf_id"]): e
            for e in entries
        }
        for future in concurrent.futures.as_completed(futures):
            entry = futures[future]
            try:
                result = future.result()
                if result:
                    # Keep discovery output compact; the exact JSON is re-fetched and
                    # archived by the per-model test immediately before conversion.
                    discovered.append({
                        "hf_id": entry["hf_id"],
                        "title": entry.get("title", entry["hf_id"]),
                        "provider": entry.get("provider"),
                        "recipe_url": result["recipe_url"],
                    })
            except Exception as exc:  # record transient discovery errors separately
                errors.append({"hf_id": entry["hf_id"], "error": str(exc)})

    discovered.sort(key=lambda x: x["hf_id"].lower())
    out = {
        "catalog_url": catalog_url,
        "hardware": "xeon6",
        "count": len(discovered),
        "models": discovered,
        "discovery_errors": errors,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"Discovered {len(discovered)} Xeon 6 recipes")
    if errors:
        print(f"WARNING: {len(errors)} catalog entries had discovery errors", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
