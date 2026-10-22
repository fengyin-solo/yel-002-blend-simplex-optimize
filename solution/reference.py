#!/usr/bin/env python3
"""Exact support search for a fixed-charge solvent blend."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from itertools import combinations, product
from pathlib import Path


INPUT_KEYS = {"batch_id", "components", "requirements", "incompatible_lot_pairs"}
COMPONENT_KEYS = {
    "id",
    "min_fraction",
    "max_fraction",
    "supplier",
    "cost_per_kg",
    "quality_index",
    "viscosity_cp",
    "activation_fee",
    "alternate",
}
ALTERNATE_KEYS = {
    "lot_id",
    "supplier",
    "cost_per_kg",
    "quality_index",
    "viscosity_cp",
    "activation_fee",
}
REQUIREMENT_KEYS = {
    "min_quality",
    "max_viscosity",
    "batch_kg",
    "max_active",
    "max_active_per_supplier",
}


class Refusal(Exception):
    def __init__(self, kind):
        self.kind = kind


def fail(kind):
    sys.stderr.write(f"运行拒收: {kind}\n")
    raise SystemExit(1 if kind != "USAGE" else 2)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def pairs_hook(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal("BAD_INPUT")
        result[key] = value
    return result


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != INPUT_KEYS:
        raise Refusal("BAD_INPUT")
    batch_id = payload["batch_id"]
    if (
        not isinstance(batch_id, str)
        or not batch_id
        or len(batch_id) > 64
        or batch_id != batch_id.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in batch_id)
    ):
        raise Refusal("BAD_INPUT")
    components = payload["components"]
    if not isinstance(components, list) or not 3 <= len(components) <= 24:
        raise Refusal("BAD_INPUT")
    seen = set()
    seen_lots = set()
    cleaned = []
    for item in components:
        if not isinstance(item, dict) or set(item) != COMPONENT_KEYS:
            raise Refusal("BAD_INPUT")
        identifier = item["id"]
        if not isinstance(identifier, str) or not identifier or not identifier.isascii():
            raise Refusal("BAD_INPUT")
        if not identifier[0].isalpha() or identifier[0].isupper():
            pass
        if not (identifier[0].isalpha() and identifier[0].isupper()):
            raise Refusal("BAD_INPUT")
        if len(identifier) < 2 or len(identifier) > 16:
            raise Refusal("BAD_INPUT")
        if not all(char.isupper() or char.isdigit() or char in "_-" for char in identifier):
            raise Refusal("BAD_INPUT")
        if identifier in seen:
            raise Refusal("BAD_INPUT")
        seen.add(identifier)
        if any(
            not number(item[key])
            for key in COMPONENT_KEYS - {"id", "supplier", "alternate"}
        ):
            raise Refusal("BAD_INPUT")
        low, high = item["min_fraction"], item["max_fraction"]
        if not 0 <= low <= high <= 1:
            raise Refusal("BAD_INPUT")
        if not 0 <= item["cost_per_kg"] <= 1000:
            raise Refusal("BAD_INPUT")
        if not -100 <= item["quality_index"] <= 100:
            raise Refusal("BAD_INPUT")
        if not 0.1 <= item["viscosity_cp"] <= 1_000_000:
            raise Refusal("BAD_INPUT")
        if not 0 <= item["activation_fee"] <= 100_000:
            raise Refusal("BAD_INPUT")
        supplier = item["supplier"]
        alternate = item["alternate"]
        if (
            not isinstance(supplier, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,15}", supplier)
            or not isinstance(alternate, dict)
            or set(alternate) != ALTERNATE_KEYS
        ):
            raise Refusal("BAD_INPUT")
        if (
            not isinstance(alternate["lot_id"], str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,15}", alternate["lot_id"])
            or alternate["lot_id"] in seen_lots
            or not isinstance(alternate["supplier"], str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,15}", alternate["supplier"])
        ):
            raise Refusal("BAD_INPUT")
        seen_lots.add(alternate["lot_id"])
        if any(not number(alternate[key]) for key in ALTERNATE_KEYS - {"lot_id", "supplier"}):
            raise Refusal("BAD_INPUT")
        if not 0 <= alternate["cost_per_kg"] <= 1000:
            raise Refusal("BAD_INPUT")
        if not -100 <= alternate["quality_index"] <= 100:
            raise Refusal("BAD_INPUT")
        if not 0.1 <= alternate["viscosity_cp"] <= 1_000_000:
            raise Refusal("BAD_INPUT")
        if not 0 <= alternate["activation_fee"] <= 100_000:
            raise Refusal("BAD_INPUT")
        cleaned.append(
            {
                "id": identifier,
                "min": float(low),
                "max": float(high),
                "options": [
                    {
                        "lot_id": "primary",
                        "supplier": supplier,
                        "cost": float(item["cost_per_kg"]),
                        "quality": float(item["quality_index"]),
                        "viscosity": float(item["viscosity_cp"]),
                        "activation_fee": float(item["activation_fee"]),
                    },
                    {
                        "lot_id": alternate["lot_id"],
                        "supplier": alternate["supplier"],
                        "cost": float(alternate["cost_per_kg"]),
                        "quality": float(alternate["quality_index"]),
                        "viscosity": float(alternate["viscosity_cp"]),
                        "activation_fee": float(alternate["activation_fee"]),
                    },
                ],
            }
        )
    requirements = payload["requirements"]
    if not isinstance(requirements, dict) or set(requirements) != REQUIREMENT_KEYS:
        raise Refusal("BAD_INPUT")
    if any(not number(requirements[key]) for key in REQUIREMENT_KEYS):
        raise Refusal("BAD_INPUT")
    if not -100 <= requirements["min_quality"] <= 100:
        raise Refusal("BAD_INPUT")
    if not 0 < requirements["max_viscosity"]:
        raise Refusal("BAD_INPUT")
    if not 1 <= requirements["batch_kg"] <= 1_000_000:
        raise Refusal("BAD_INPUT")
    max_active = requirements["max_active"]
    if (
        isinstance(max_active, bool)
        or not isinstance(max_active, int)
        or not 2 <= max_active <= 4
    ):
        raise Refusal("BAD_INPUT")
    supplier_limit = requirements["max_active_per_supplier"]
    if (
        isinstance(supplier_limit, bool)
        or not isinstance(supplier_limit, int)
        or not 1 <= supplier_limit <= 4
    ):
        raise Refusal("BAD_INPUT")
    if sum(item["min"] for item in cleaned) > 1 + 1e-12:
        raise Refusal("BAD_INPUT")
    if sum(item["max"] for item in cleaned) < 1 - 1e-12:
        raise Refusal("BAD_INPUT")
    pairs = payload["incompatible_lot_pairs"]
    if not isinstance(pairs, list) or len(pairs) > 48:
        raise Refusal("BAD_INPUT")
    cleaned_pairs = []
    seen_pairs = set()
    for pair in pairs:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(value, str) or value not in seen_lots for value in pair)
            or pair[0] >= pair[1]
        ):
            raise Refusal("BAD_INPUT")
        key = (pair[0], pair[1])
        if key in seen_pairs:
            raise Refusal("BAD_INPUT")
        seen_pairs.add(key)
        cleaned_pairs.append(key)
    return batch_id, cleaned, requirements, cleaned_pairs


EPS = 1e-10


class SimplexMax:
    """Maximize c*x subject to A*x <= b and x >= 0."""

    def __init__(self, matrix, right, objective):
        self.m = len(right)
        self.n = len(objective)
        self.nonbasic = list(range(self.n)) + [-1]
        self.basic = [self.n + index for index in range(self.m)]
        self.tableau = [[0.0] * (self.n + 2) for _ in range(self.m + 2)]
        for row_index in range(self.m):
            for column in range(self.n):
                self.tableau[row_index][column] = matrix[row_index][column]
            self.tableau[row_index][self.n] = -1.0
            self.tableau[row_index][self.n + 1] = right[row_index]
        for column in range(self.n):
            self.tableau[self.m][column] = -objective[column]
        self.tableau[self.m + 1][self.n] = 1.0

    def pivot(self, row_index, column):
        tableau = self.tableau
        size = self.n + 2
        pivot_row = tableau[row_index]
        inverse = 1.0 / pivot_row[column]
        for other in range(self.m + 2):
            if other == row_index or abs(tableau[other][column]) <= EPS:
                continue
            current = tableau[other]
            factor = current[column] * inverse
            for index in range(size):
                current[index] -= pivot_row[index] * factor
            current[column] = pivot_row[column] * factor
        for index in range(size):
            if index != column:
                pivot_row[index] *= inverse
        for other in range(self.m + 2):
            if other != row_index:
                tableau[other][column] *= -inverse
        pivot_row[column] = inverse
        self.basic[row_index], self.nonbasic[column] = (
            self.nonbasic[column],
            self.basic[row_index],
        )

    def simplex(self, phase):
        objective_row = self.m + phase - 1
        while True:
            entering = -1
            for column in range(self.n + 1):
                if self.nonbasic[column] == -phase:
                    continue
                if entering < 0 or (
                    self.tableau[objective_row][column],
                    self.nonbasic[column],
                ) < (
                    self.tableau[objective_row][entering],
                    self.nonbasic[entering],
                ):
                    entering = column
            if entering < 0 or self.tableau[objective_row][entering] >= -EPS:
                return True
            leaving = -1
            for row_index in range(self.m):
                coefficient = self.tableau[row_index][entering]
                if coefficient <= EPS:
                    continue
                ratio = self.tableau[row_index][self.n + 1] / coefficient
                if leaving < 0:
                    leaving = row_index
                    continue
                best_coefficient = self.tableau[leaving][entering]
                best_ratio = self.tableau[leaving][self.n + 1] / best_coefficient
                if (ratio, self.basic[row_index]) < (
                    best_ratio,
                    self.basic[leaving],
                ):
                    leaving = row_index
            if leaving < 0:
                raise Refusal("INFEASIBLE")
            self.pivot(leaving, entering)

    def solve(self):
        row = 0
        for index in range(1, self.m):
            if self.tableau[index][self.n + 1] < self.tableau[row][self.n + 1]:
                row = index
        if self.tableau[row][self.n + 1] < -EPS:
            self.pivot(row, self.n)
            if (
                not self.simplex(2)
                or self.tableau[self.m + 1][self.n + 1] < -EPS
            ):
                return None
            for index in range(self.m):
                if self.basic[index] != -1:
                    continue
                entering = 0
                for column in range(1, self.n + 1):
                    if (
                        self.tableau[index][column],
                        self.nonbasic[column],
                    ) < (
                        self.tableau[index][entering],
                        self.nonbasic[entering],
                    ):
                        entering = column
                self.pivot(index, entering)
        if not self.simplex(1):
            return None
        solution = [0.0] * self.n
        for index in range(self.m):
            variable = self.basic[index]
            if 0 <= variable < self.n:
                solution[variable] = self.tableau[index][self.n + 1]
        return solution


def constraints(components, requirements):
    count = len(components)
    lows = [item["min"] for item in components]
    upper = [item["max"] - item["min"] for item in components]
    remaining = 1.0 - sum(lows)
    matrix = []
    right = []
    for index in range(count):
        row = [0.0] * count
        row[index] = 1.0
        matrix.append(row)
        right.append(upper[index])
    quality_base = sum(item["quality"] * item["min"] for item in components)
    viscosity_base = sum(
        item["viscosity"] * item["min"] for item in components
    )
    matrix.append([-item["quality"] for item in components])
    right.append(quality_base - requirements["min_quality"])
    matrix.append([item["viscosity"] for item in components])
    right.append(requirements["max_viscosity"] - viscosity_base)
    matrix.append([1.0] * count)
    right.append(remaining)
    matrix.append([-1.0] * count)
    right.append(-remaining)
    return matrix, right


def optimize_support(selected, requirements):
    matrix, right = constraints(selected, requirements)
    costs = [item["cost"] for item in selected]
    shifted = SimplexMax(matrix, right, [-cost for cost in costs]).solve()
    if shifted is None:
        return None
    best_shifted_cost = sum(costs[index] * shifted[index] for index in range(len(selected)))
    matrix.append(costs)
    right.append(best_shifted_cost + 1.1e-10)

    for fixed in range(len(selected)):
        objective = [
            -1.0 if index == fixed else 0.0 for index in range(len(selected))
        ]
        shifted = SimplexMax(matrix, right, objective).solve()
        if shifted is None:
            return None
        minimum = shifted[fixed]
        row = [0.0] * len(selected)
        row[fixed] = 1.0
        matrix.append(row)
        right.append(minimum + 1e-9)
        row = [0.0] * len(selected)
        row[fixed] = -1.0
        matrix.append(row)
        right.append(-minimum + 1e-9)

    fractions = [0.0] * len(selected)
    for local, item in enumerate(selected):
        value = item["min"] + shifted[local]
        if abs(value - item["min"]) <= 1e-8:
            value = item["min"]
        if abs(value - item["max"]) <= 1e-8:
            value = item["max"]
        fractions[local] = max(0.0, min(1.0, value))
    residual = 1.0 - sum(fractions)
    if abs(residual) > 1e-8:
        return None
    active = [index for index, value in enumerate(fractions) if value > 1e-9]
    variable_cost = sum(selected[index]["cost"] * fractions[index] for index in active)
    activation_cost = sum(selected[index]["activation_fee"] for index in active)
    quality = sum(selected[index]["quality"] * fractions[index] for index in active)
    viscosity = sum(selected[index]["viscosity"] * fractions[index] for index in active)
    return fractions, variable_cost, activation_cost, quality, viscosity


def optimize(components, requirements, incompatible_pairs):
    mandatory = [index for index, item in enumerate(components) if item["min"] > 0]
    max_active = requirements["max_active"]
    if len(mandatory) > max_active:
        raise Refusal("INFEASIBLE")
    candidates = []
    optional = [index for index in range(len(components)) if index not in mandatory]
    for extra_count in range(max_active - len(mandatory) + 1):
        for extra in combinations(optional, extra_count):
            support = tuple(sorted(mandatory + list(extra)))
            for choice in product((0, 1), repeat=len(support)):
                selected = []
                lot_ids = ["primary"] * len(components)
                for local, original in enumerate(support):
                    item = components[original]
                    option = item["options"][choice[local]]
                    selected.append(
                        {
                            "id": item["id"],
                            "min": item["min"],
                            "max": item["max"],
                            "cost": option["cost"],
                            "quality": option["quality"],
                            "viscosity": option["viscosity"],
                            "activation_fee": option["activation_fee"],
                            "supplier": option["supplier"],
                            "lot_id": option["lot_id"],
                        }
                    )
                    lot_ids[original] = option["lot_id"]
                solved = optimize_support(selected, requirements)
                if solved is None:
                    continue
                local_fractions, variable_cost, activation_cost, quality, viscosity = solved
                fractions = [0.0] * len(components)
                supplier_counts = {}
                for local, original in enumerate(support):
                    fractions[original] = local_fractions[local]
                    if local_fractions[local] > 1e-9:
                        supplier = selected[local]["supplier"]
                        supplier_counts[supplier] = supplier_counts.get(supplier, 0) + 1
                if (
                    sum(value > 1e-9 for value in fractions) > max_active
                    or any(
                        count > requirements["max_active_per_supplier"]
                        for count in supplier_counts.values()
                    )
                ):
                    continue
                chosen_lots = {
                    selected[local]["lot_id"]
                    for local, value in enumerate(local_fractions)
                    if value > 1e-9
                }
                if any(left in chosen_lots and right in chosen_lots for left, right in incompatible_pairs):
                    continue
                candidate = (
                    fractions,
                    variable_cost,
                    activation_cost,
                    quality,
                    viscosity,
                    lot_ids,
                )
                candidates.append(
                    (
                        (
                            variable_cost * requirements["batch_kg"] + activation_cost,
                            tuple(fractions),
                            tuple(lot_ids),
                        ),
                        candidate,
                    )
                )
    if not candidates:
        raise Refusal("INFEASIBLE")
    best_key, best = min(candidates, key=lambda pair: pair[0])
    for key, candidate in candidates:
        if abs(key[0] - best_key[0]) <= 1e-10 and key[1] < best_key[1]:
            best_key, best = key, candidate
    return best


def write_atomic(target, payload):
    if not target.parent.is_dir() or target.is_dir() or not os.access(target.parent, os.W_OK):
        raise Refusal("IO_FAILURE")
    old = target.read_bytes() if target.exists() else None
    staged = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".blend-", dir=target.parent)
        staged = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, target)
        staged = None
    except OSError:
        if old is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(old)
        raise Refusal("IO_FAILURE")
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def main(argv):
    if len(argv) != 3:
        fail("USAGE")
    source, destination = Path(argv[1]), Path(argv[2])
    try:
        raw = source.read_text(encoding="utf-8")
        payload = json.loads(raw, object_pairs_hook=pairs_hook)
        batch_id, components, requirements, incompatible_pairs = validate(payload)
        fractions, variable_cost, activation_cost, quality, viscosity, lot_ids = optimize(
            components, requirements, incompatible_pairs
        )
        payload = {
            "batch_id": batch_id,
            "status": "optimal",
            "activation_cost": activation_cost,
            "total_cost": variable_cost * requirements["batch_kg"] + activation_cost,
            "allocation": [
                {
                    "id": item["id"],
                    "lot_id": lot_ids[index] if fraction > 1e-9 else None,
                    "fraction": fraction,
                    "mass_kg": fraction * requirements["batch_kg"],
                }
                for index, (item, fraction) in enumerate(zip(components, fractions))
            ],
            "quality_index": quality,
            "viscosity_cp": viscosity,
        }
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        write_atomic(destination, encoded)
        return 0
    except Refusal as exc:
        fail(exc.kind)
    except (OSError, UnicodeError, ValueError):
        fail("BAD_INPUT")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
