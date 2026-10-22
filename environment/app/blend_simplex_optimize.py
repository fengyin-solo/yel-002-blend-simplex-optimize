#!/usr/bin/env python3
"""Reconcile anonymous lot bundles, then solve the fixed-charge blend."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from itertools import combinations, product
from pathlib import Path


INPUT_KEYS = {
    "batch_id",
    "components",
    "supply_lots",
    "lot_bundles",
    "requirements",
    "certification_runs",
    "incompatible_lot_pairs",
}
COMPONENT_KEYS = {"id", "min_fraction", "max_fraction"}
LOT_KEYS = {
    "lot_id",
    "supplier",
    "cost_per_kg",
    "quality_index",
    "viscosity_cp",
    "activation_fee",
    "available_kg",
}
BUNDLE_KEYS = {"bundle_id", "lots"}
RUN_KEYS = {"fractions", "cost_per_kg", "quality_index", "viscosity_cp"}
REQUIREMENT_KEYS = {
    "min_quality",
    "max_viscosity",
    "batch_kg",
    "max_active",
    "max_active_per_supplier",
    "additional_orders",
}
ORDER_KEYS = {"order_id", "batch_kg", "min_quality", "max_viscosity"}
ID_RE = re.compile(r"[A-Z][A-Z0-9_-]{1,15}")
EPS = 1e-10


class Refusal(Exception):
    def __init__(self, kind):
        self.kind = kind


def fail(kind):
    sys.stderr.write(f"运行拒收: {kind}\n")
    raise SystemExit(1)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valid_label(value, limit):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= limit
        and value == value.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def pairs_hook(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal("BAD_INPUT")
        result[key] = value
    return result


def validate_lot(raw):
    if not isinstance(raw, dict) or set(raw) != LOT_KEYS:
        raise Refusal("BAD_INPUT")
    if not isinstance(raw["lot_id"], str) or not ID_RE.fullmatch(raw["lot_id"]):
        raise Refusal("BAD_INPUT")
    if not isinstance(raw["supplier"], str) or not ID_RE.fullmatch(raw["supplier"]):
        raise Refusal("BAD_INPUT")
    if any(not number(raw[key]) for key in LOT_KEYS - {"lot_id", "supplier"}):
        raise Refusal("BAD_INPUT")
    if not 0 <= raw["cost_per_kg"] <= 1000:
        raise Refusal("BAD_INPUT")
    if not -100 <= raw["quality_index"] <= 100:
        raise Refusal("BAD_INPUT")
    if not 0.1 <= raw["viscosity_cp"] <= 1_000_000:
        raise Refusal("BAD_INPUT")
    if not 0 <= raw["activation_fee"] <= 100_000:
        raise Refusal("BAD_INPUT")
    if not 1 <= raw["available_kg"] <= 1_000_000_000:
        raise Refusal("BAD_INPUT")
    return {
        "lot_id": raw["lot_id"],
        "supplier": raw["supplier"],
        "cost": float(raw["cost_per_kg"]),
        "quality": float(raw["quality_index"]),
        "viscosity": float(raw["viscosity_cp"]),
        "activation_fee": float(raw["activation_fee"]),
        "available_kg": float(raw["available_kg"]),
    }


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != INPUT_KEYS:
        raise Refusal("BAD_INPUT")
    batch_id = payload["batch_id"]
    if not valid_label(batch_id, 64):
        raise Refusal("BAD_INPUT")
    raw_components = payload["components"]
    if not isinstance(raw_components, list) or not 3 <= len(raw_components) <= 12:
        raise Refusal("BAD_INPUT")
    components = []
    component_ids = set()
    for raw in raw_components:
        if not isinstance(raw, dict) or set(raw) != COMPONENT_KEYS:
            raise Refusal("BAD_INPUT")
        if not isinstance(raw["id"], str) or not ID_RE.fullmatch(raw["id"]):
            raise Refusal("BAD_INPUT")
        if raw["id"] in component_ids:
            raise Refusal("BAD_INPUT")
        component_ids.add(raw["id"])
        if not number(raw["min_fraction"]) or not number(raw["max_fraction"]):
            raise Refusal("BAD_INPUT")
        if not 0 <= raw["min_fraction"] <= raw["max_fraction"] <= 1:
            raise Refusal("BAD_INPUT")
        components.append(
            {
                "id": raw["id"],
                "min": float(raw["min_fraction"]),
                "max": float(raw["max_fraction"]),
            }
        )

    raw_lots = payload["supply_lots"]
    if not isinstance(raw_lots, list) or len(raw_lots) != 2 * len(components):
        raise Refusal("BAD_INPUT")
    lots = []
    lots_by_id = {}
    for raw in raw_lots:
        lot = validate_lot(raw)
        if lot["lot_id"] in lots_by_id:
            raise Refusal("BAD_INPUT")
        lots.append(lot)
        lots_by_id[lot["lot_id"]] = lot

    raw_bundles = payload["lot_bundles"]
    if not isinstance(raw_bundles, list) or len(raw_bundles) != len(components):
        raise Refusal("BAD_INPUT")
    bundles = []
    bundle_ids = set()
    bundled_lot_ids = set()
    for raw in raw_bundles:
        if not isinstance(raw, dict) or set(raw) != BUNDLE_KEYS:
            raise Refusal("BAD_INPUT")
        if not isinstance(raw["bundle_id"], str) or not ID_RE.fullmatch(raw["bundle_id"]):
            raise Refusal("BAD_INPUT")
        if raw["bundle_id"] in bundle_ids:
            raise Refusal("BAD_INPUT")
        bundle_ids.add(raw["bundle_id"])
        pair = raw["lots"]
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(value, str) or value not in lots_by_id for value in pair)
            or pair[0] >= pair[1]
        ):
            raise Refusal("BAD_INPUT")
        if any(value in bundled_lot_ids for value in pair):
            raise Refusal("BAD_INPUT")
        bundled_lot_ids.update(pair)
        bundles.append({"bundle_id": raw["bundle_id"], "lots": tuple(pair)})
    if bundled_lot_ids != set(lots_by_id):
        raise Refusal("BAD_INPUT")

    requirements = payload["requirements"]
    if not isinstance(requirements, dict) or set(requirements) != REQUIREMENT_KEYS:
        raise Refusal("BAD_INPUT")
    if any(not number(requirements[key]) for key in REQUIREMENT_KEYS - {"additional_orders"}):
        raise Refusal("BAD_INPUT")
    if not -100 <= requirements["min_quality"] <= 100:
        raise Refusal("BAD_INPUT")
    if not 0 < requirements["max_viscosity"]:
        raise Refusal("BAD_INPUT")
    if not 1 <= requirements["batch_kg"] <= 1_000_000:
        raise Refusal("BAD_INPUT")
    max_active = requirements["max_active"]
    max_per_supplier = requirements["max_active_per_supplier"]
    if isinstance(max_active, bool) or not isinstance(max_active, int) or not 2 <= max_active <= 3:
        raise Refusal("BAD_INPUT")
    if (
        isinstance(max_per_supplier, bool)
        or not isinstance(max_per_supplier, int)
        or not 1 <= max_per_supplier <= 4
    ):
        raise Refusal("BAD_INPUT")
    raw_orders = requirements["additional_orders"]
    if not isinstance(raw_orders, list) or len(raw_orders) > 2:
        raise Refusal("BAD_INPUT")
    additional_orders = []
    order_ids = {"PRIMARY"}
    for raw_order in raw_orders:
        if not isinstance(raw_order, dict) or set(raw_order) != ORDER_KEYS:
            raise Refusal("BAD_INPUT")
        if not isinstance(raw_order["order_id"], str) or not ID_RE.fullmatch(raw_order["order_id"]):
            raise Refusal("BAD_INPUT")
        if raw_order["order_id"] in order_ids:
            raise Refusal("BAD_INPUT")
        order_ids.add(raw_order["order_id"])
        if any(not number(raw_order[key]) for key in ORDER_KEYS - {"order_id"}):
            raise Refusal("BAD_INPUT")
        if not -100 <= raw_order["min_quality"] <= 100:
            raise Refusal("BAD_INPUT")
        if not 0 < raw_order["max_viscosity"]:
            raise Refusal("BAD_INPUT")
        if not 1 <= raw_order["batch_kg"] <= 1_000_000:
            raise Refusal("BAD_INPUT")
        additional_orders.append(dict(raw_order))
    if sum(item["min"] for item in components) > 1 + 1e-12:
        raise Refusal("BAD_INPUT")
    if sum(item["max"] for item in components) < 1 - 1e-12:
        raise Refusal("BAD_INPUT")

    raw_runs = payload["certification_runs"]
    if not isinstance(raw_runs, list) or len(raw_runs) != len(components):
        raise Refusal("BAD_INPUT")
    runs = []
    for raw in raw_runs:
        if not isinstance(raw, dict) or set(raw) != RUN_KEYS:
            raise Refusal("BAD_INPUT")
        row = raw["fractions"]
        if not isinstance(row, list) or len(row) != len(components):
            raise Refusal("BAD_INPUT")
        if any(not number(value) or not 0 <= value <= 1 for value in row):
            raise Refusal("BAD_INPUT")
        if abs(sum(row) - 1.0) > 1e-9:
            raise Refusal("BAD_INPUT")
        if any(not number(raw[key]) for key in RUN_KEYS - {"fractions"}):
            raise Refusal("BAD_INPUT")
        if not 0 <= raw["cost_per_kg"] <= 1000:
            raise Refusal("BAD_INPUT")
        if not -100 <= raw["quality_index"] <= 100:
            raise Refusal("BAD_INPUT")
        if not 0.1 <= raw["viscosity_cp"] <= 1_000_000:
            raise Refusal("BAD_INPUT")
        runs.append(
            {
                "fractions": [float(value) for value in row],
                "cost": float(raw["cost_per_kg"]),
                "quality": float(raw["quality_index"]),
                "viscosity": float(raw["viscosity_cp"]),
            }
        )

    raw_pairs = payload["incompatible_lot_pairs"]
    if not isinstance(raw_pairs, list) or len(raw_pairs) > 48:
        raise Refusal("BAD_INPUT")
    incompatible_pairs = []
    seen_pairs = set()
    for pair in raw_pairs:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(value, str) or value not in lots_by_id for value in pair)
            or pair[0] >= pair[1]
        ):
            raise Refusal("BAD_INPUT")
        key = (pair[0], pair[1])
        if key in seen_pairs:
            raise Refusal("BAD_INPUT")
        seen_pairs.add(key)
        incompatible_pairs.append(key)
    return (
        batch_id,
        components,
        lots_by_id,
        bundles,
        {**requirements, "additional_orders": additional_orders},
        runs,
        incompatible_pairs,
    )


def solve_three(matrix, cost, quality, viscosity):
    size = len(matrix)
    augmented = [
        [float(value) for value in matrix[row]]
        + [float(cost[row]), float(quality[row]), float(viscosity[row])]
        for row in range(size)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-11:
            raise Refusal("BAD_MODEL")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0:
                continue
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[column])
            ]
    return [
        [augmented[row][size + metric] for row in range(size)]
        for metric in range(3)
    ]


def recover_bundle_map(components, lots_by_id, bundles, runs):
    # Legacy importer relies on bundle array order.
    return [
        {
            **component,
            "bundle_id": bundle["bundle_id"],
            "options": [lots_by_id[lot_id] for lot_id in bundle["lots"]],
        }
        for component, bundle in zip(components, bundles)
    ]


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
                if (ratio, self.basic[row_index]) < (best_ratio, self.basic[leaving]):
                    leaving = row_index
            if leaving < 0:
                return None
            self.pivot(leaving, entering)

    def solve(self):
        row = 0
        for index in range(1, self.m):
            if self.tableau[index][self.n + 1] < self.tableau[row][self.n + 1]:
                row = index
        if self.tableau[row][self.n + 1] < -EPS:
            self.pivot(row, self.n)
            if not self.simplex(2) or self.tableau[self.m + 1][self.n + 1] < -EPS:
                return None
            for index in range(self.m):
                if self.basic[index] != -1:
                    continue
                entering = min(
                    range(self.n + 1),
                    key=lambda column: (
                        self.tableau[index][column],
                        self.nonbasic[column],
                    ),
                )
                self.pivot(index, entering)
        if not self.simplex(1):
            return None
        solution = [0.0] * self.n
        for index in range(self.m):
            variable = self.basic[index]
            if 0 <= variable < self.n:
                solution[variable] = self.tableau[index][self.n + 1]
        return solution


def constraints(selected, requirements):
    count = len(selected)
    lows = [item["min"] for item in selected]
    upper = [item["max"] - item["min"] for item in selected]
    remaining = 1.0 - sum(lows)
    matrix = []
    right = []
    for index in range(count):
        row = [0.0] * count
        row[index] = 1.0
        matrix.append(row)
        right.append(upper[index])
    quality_base = sum(item["quality"] * item["min"] for item in selected)
    viscosity_base = sum(item["viscosity"] * item["min"] for item in selected)
    matrix.append([-item["quality"] for item in selected])
    right.append(quality_base - requirements["min_quality"])
    matrix.append([item["viscosity"] for item in selected])
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
    minimum_cost = sum(costs[index] * shifted[index] for index in range(len(selected)))
    matrix.append(costs)
    right.append(minimum_cost + 1.1e-10)
    for fixed in range(len(selected)):
        objective = [-1.0 if index == fixed else 0.0 for index in range(len(selected))]
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
    fractions = []
    for local, item in enumerate(selected):
        value = item["min"] + shifted[local]
        if abs(value - item["min"]) <= 1e-8:
            value = item["min"]
        if abs(value - item["max"]) <= 1e-8:
            value = item["max"]
        fractions.append(max(0.0, min(1.0, value)))
    if abs(1.0 - sum(fractions)) > 1e-8:
        return None
    active = [index for index, value in enumerate(fractions) if value > 1e-9]
    variable_cost = sum(selected[index]["cost"] * fractions[index] for index in active)
    activation_cost = sum(selected[index]["activation_fee"] for index in active)
    quality = sum(selected[index]["quality"] * fractions[index] for index in active)
    viscosity = sum(selected[index]["viscosity"] * fractions[index] for index in active)
    return fractions, variable_cost, activation_cost, quality, viscosity


def enumerate_candidates(components, requirements, incompatible_pairs):
    mandatory = [index for index, item in enumerate(components) if item["min"] > 0]
    max_active = requirements["max_active"]
    if len(mandatory) > max_active:
        raise Refusal("INFEASIBLE")
    optional = [index for index in range(len(components)) if index not in mandatory]
    candidates = []
    for extra_count in range(max_active - len(mandatory) + 1):
        for extra in combinations(optional, extra_count):
            support = tuple(sorted(mandatory + list(extra)))
            for choice in product((0, 1), repeat=len(support)):
                selected = []
                lot_ids = [None] * len(components)
                for local, original in enumerate(support):
                    component = components[original]
                    option = component["options"][choice[local]]
                    selected.append(
                        {
                            "min": component["min"],
                            "max": component["max"],
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
                active_lots = set()
                for local, original in enumerate(support):
                    fractions[original] = local_fractions[local]
                    if local_fractions[local] > 1e-9:
                        supplier = selected[local]["supplier"]
                        supplier_counts[supplier] = supplier_counts.get(supplier, 0) + 1
                        active_lots.add(selected[local]["lot_id"])
                    else:
                        lot_ids[original] = None
                if any(
                    count > requirements["max_active_per_supplier"]
                    for count in supplier_counts.values()
                ):
                    continue
                if any(left in active_lots and right in active_lots for left, right in incompatible_pairs):
                    continue
                candidates.append(
                    {
                        "cost": variable_cost * requirements["batch_kg"] + activation_cost,
                        "variable_cost": variable_cost,
                        "batch_kg": requirements["batch_kg"],
                        "fractions": fractions,
                        "lot_ids": lot_ids,
                        "activation_cost": activation_cost,
                        "quality": quality,
                        "viscosity": viscosity,
                        "active_suppliers": {
                            (original, selected[local]["supplier"])
                            for local, original in enumerate(support)
                            if local_fractions[local] > 1e-9
                        },
                        "used_lot_ids": set(active_lots),
                        "lot_mass": {
                            selected[local]["lot_id"]: local_fractions[local]
                            * requirements["batch_kg"]
                            for local in range(len(support))
                            if local_fractions[local] > 1e-9
                        },
                    }
                )
    return candidates


def choose_best(candidates):
    best_cost = min(item["cost"] for item in candidates)
    tied = [item for item in candidates if abs(item["cost"] - best_cost) <= 1e-10]
    return min(
        tied,
        key=lambda item: (
            tuple(item["fractions"]),
            tuple("" if value is None else value for value in item["lot_ids"]),
        ),
    )


def optimize_plan(components, requirements, incompatible_pairs):
    # Legacy greedy path: choose the first option and ignore shared inventory.
    order_specs = [
        {
            "order_id": "PRIMARY",
            "batch_kg": requirements["batch_kg"],
            "min_quality": requirements["min_quality"],
            "max_viscosity": requirements["max_viscosity"],
        },
        *requirements["additional_orders"],
    ]
    orders = []
    for order in order_specs:
        fractions = [item["min"] for item in components]
        remaining = 1.0 - sum(fractions)
        for index in sorted(
            range(len(components)),
            key=lambda item: components[item]["options"][0]["cost"],
        ):
            room = components[index]["max"] - fractions[index]
            added = min(remaining, room)
            fractions[index] += added
            remaining -= added
            if remaining <= 1e-12:
                break
        if remaining > 1e-9:
            raise Refusal("INFEASIBLE")
        chosen = [
            component["options"][0] if fraction > 1e-9 else None
            for component, fraction in zip(components, fractions)
        ]
        orders.append(
            {
                "fractions": fractions,
                "lot_ids": [
                    option["lot_id"] if option is not None else None
                    for option in chosen
                ],
                "variable_cost": sum(
                    option["cost"] * fraction
                    for option, fraction in zip(chosen, fractions)
                    if option is not None
                ),
                "batch_kg": order["batch_kg"],
                "quality": sum(
                    option["quality"] * fraction
                    for option, fraction in zip(chosen, fractions)
                    if option is not None
                ),
                "viscosity": sum(
                    option["viscosity"] * fraction
                    for option, fraction in zip(chosen, fractions)
                    if option is not None
                ),
                "activation_cost": sum(
                    option["activation_fee"] for option in chosen if option is not None
                ),
                "active_suppliers": set(),
                "used_lot_ids": {
                    option["lot_id"] for option in chosen if option is not None
                },
                "lot_mass": {},
            }
        )
    return order_specs, {
        "cost": sum(item["variable_cost"] * item["batch_kg"] for item in orders),
        "activation_cost": sum(item["activation_cost"] for item in orders),
        "orders": orders,
    }

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
        payload = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=pairs_hook)
        (
            batch_id,
            components,
            lots_by_id,
            bundles,
            requirements,
            runs,
            incompatible_pairs,
        ) = validate(payload)
        mapped = recover_bundle_map(components, lots_by_id, bundles, runs)
        order_specs, best = optimize_plan(mapped, requirements, incompatible_pairs)
        base = best["orders"][0]
        result = {
            "batch_id": batch_id,
            "bundle_map": [
                {"id": component["id"], "bundle_id": component["bundle_id"]}
                for component in mapped
            ],
            "status": "optimal",
            "activation_cost": best["activation_cost"],
            "total_cost": best["cost"],
            "allocation": [
                {
                    "id": component["id"],
                    "lot_id": base["lot_ids"][index] if fraction > 1e-9 else None,
                    "fraction": fraction,
                    "mass_kg": fraction * requirements["batch_kg"],
                }
                for index, (component, fraction) in enumerate(zip(mapped, base["fractions"]))
            ],
            "quality_index": base["quality"],
            "viscosity_cp": base["viscosity"],
            "additional_orders": [
                {
                    "order_id": spec["order_id"],
                    "allocation": [
                        {
                            "id": component["id"],
                            "lot_id": candidate["lot_ids"][index] if fraction > 1e-9 else None,
                            "fraction": fraction,
                            "mass_kg": fraction * spec["batch_kg"],
                        }
                        for index, (component, fraction) in enumerate(zip(mapped, candidate["fractions"]))
                    ],
                    "quality_index": candidate["quality"],
                    "viscosity_cp": candidate["viscosity"],
                }
                for spec, candidate in zip(order_specs[1:], best["orders"][1:])
            ],
        }
        encoded = (
            json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        write_atomic(destination, encoded)
        return 0
    except Refusal as exc:
        fail(exc.kind)
    except (OSError, UnicodeError, ValueError):
        fail("BAD_INPUT")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
