#!/usr/bin/env python3
"""Legacy cheapest-first solvent blender."""

import json
import sys
from pathlib import Path


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("运行拒收: USAGE\n")
        return 2
    source, destination = map(Path, argv[1:])
    payload = json.loads(source.read_text(encoding="utf-8"))
    components = payload["components"]
    fractions = {item["id"]: item["min_fraction"] for item in components}
    remaining = 1.0 - sum(fractions.values())
    for component in sorted(components, key=lambda item: item["cost_per_kg"]):
        room = component["max_fraction"] - fractions[component["id"]]
        added = min(remaining, room)
        fractions[component["id"]] += added
        remaining -= added
    allocation = [
        {
            "id": item["id"],
            "fraction": fractions[item["id"]],
            "mass_kg": fractions[item["id"]] * payload["requirements"]["batch_kg"],
        }
        for item in components
    ]
    document = {
        "batch_id": payload["batch_id"],
        "status": "optimal",
        "total_cost": sum(
            fractions[item["id"]] * item["cost_per_kg"] * payload["requirements"]["batch_kg"]
            for item in components
        ),
        "allocation": allocation,
        "quality_index": sum(fractions[item["id"]] * item["quality_index"] for item in components),
        "viscosity_cp": sum(fractions[item["id"]] * item["viscosity_cp"] for item in components),
    }
    destination.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
