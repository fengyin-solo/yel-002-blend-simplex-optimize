"""Acceptance tests for the constrained solvent blend optimizer."""

import json
import os
import subprocess
import sys
import time

import pytest


ENTRY = os.environ.get("BLEND_OPTIMIZER_ENTRY", "/app/blend_simplex_optimize.py")


def sample():
    return {
        "batch_id": "SPRAY-284",
        "components": [
            {
                "id": "SOL-A",
                "min_fraction": 0.0,
                "max_fraction": 0.2,
                "supplier": "SUP-A",
                "cost_per_kg": 1.0,
                "quality_index": 0.0,
                "viscosity_cp": 10.0,
                "activation_fee": 0.0,
                "alternate": {
                    "lot_id": "ALT-A",
                    "supplier": "SUP-C",
                    "cost_per_kg": 1.2,
                    "quality_index": 1.0,
                    "viscosity_cp": 10.0,
                    "activation_fee": 5.0,
                },
            },
            {
                "id": "SOL-B",
                "min_fraction": 0.0,
                "max_fraction": 1.0,
                "supplier": "SUP-B",
                "cost_per_kg": 2.0,
                "quality_index": 10.0,
                "viscosity_cp": 10.0,
                "activation_fee": 0.0,
                "alternate": {
                    "lot_id": "ALT-B",
                    "supplier": "SUP-D",
                    "cost_per_kg": 2.1,
                    "quality_index": 9.0,
                    "viscosity_cp": 10.0,
                    "activation_fee": 0.0,
                },
            },
            {
                "id": "SOL-C",
                "min_fraction": 0.0,
                "max_fraction": 1.0,
                "supplier": "SUP-C",
                "cost_per_kg": 5.0,
                "quality_index": 5.0,
                "viscosity_cp": 10.0,
                "activation_fee": 0.0,
                "alternate": {
                    "lot_id": "ALT-C",
                    "supplier": "SUP-E",
                    "cost_per_kg": 5.5,
                    "quality_index": 5.0,
                    "viscosity_cp": 10.0,
                    "activation_fee": 0.0,
                },
            },
        ],
        "requirements": {
            "min_quality": 5.0,
            "max_viscosity": 10.0,
            "batch_kg": 100.0,
            "max_active": 4,
            "max_active_per_supplier": 4,
        },
        "incompatible_lot_pairs": [["ALT-A", "ALT-B"]],
    }


def tie_sample():
    payload = sample()
    payload["components"][0]["max_fraction"] = 0.3
    payload["components"][0]["cost_per_kg"] = 2.0
    payload["components"][1]["max_fraction"] = 0.7
    payload["components"][2]["min_fraction"] = 0.4
    payload["components"][2]["max_fraction"] = 0.4
    for component in payload["components"]:
        component["alternate"]["cost_per_kg"] = 100.0
        component["alternate"]["activation_fee"] = 0.0
    payload["requirements"]["min_quality"] = 0.0
    return payload


def maximum_component_sample():
    payload = sample()
    base = payload["components"][1].copy()
    for index in range(4, 17):
        extra = base.copy()
        extra["alternate"] = {
            **base["alternate"],
            "lot_id": f"ALT-{index}",
            "supplier": f"SUP-{index:02d}",
        }
        extra["id"] = f"EXT-{index}"
        extra["max_fraction"] = 0.04
        payload["components"].append(extra)
    return payload


def fixed_fee_sample():
    payload = sample()
    payload["components"][0]["max_fraction"] = 1.0
    payload["components"][0]["activation_fee"] = 150.0
    payload["components"][0]["alternate"]["cost_per_kg"] = 9.0
    payload["components"][0]["alternate"]["activation_fee"] = 0.0
    payload["components"][1]["cost_per_kg"] = 2.4
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_active"] = 2
    return payload


def write(path, document, raw=None):
    if raw is None:
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_bytes(raw.encode("utf-8"))
    return path


def run_task(tmp_path, document, *, stem="output", raw=None):
    source = write(tmp_path / "blend.json", document, raw=raw)
    output = tmp_path / f"{stem}.json"
    result = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, source, output


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_success_is_quiet(tmp_path):
    """题面：「计算成功时，终端不接收任何标准输出或错误字节。」"""
    result, _, output = run_task(tmp_path, sample())
    assert result.returncode == 0 and result.stdout == "" and result.stderr == "" and output.exists()


def test_absent_batch_id_is_bad_input(tmp_path):
    """题面：「退回原因是输入结构、键、类型、范围或总占比不合规；此时进程返回 1，标准错误内容为 `运行拒收: BAD_INPUT`。」"""
    payload = sample()
    payload.pop("batch_id")
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_absent_components_is_bad_input(tmp_path):
    """题面：「顶层只有 `batch_id`、`components`、`requirements` 三项。」"""
    payload = sample()
    payload.pop("components")
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_absent_requirements_is_bad_input(tmp_path):
    """题面：「顶层只有 `batch_id`、`components`、`requirements` 三项。」"""
    payload = sample()
    payload.pop("requirements")
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_extra_component_key_is_bad_input(tmp_path):
    """题面：「每条记录只放下面九个键：」。"""
    payload = sample()
    payload["components"][0]["supplier"] = "north"
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_absent_activation_fee_is_bad_input(tmp_path):
    """题面：「每条记录只放下面九个键：」。"""
    payload = sample()
    payload["components"][0].pop("activation_fee")
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_extra_top_level_key_is_bad_input(tmp_path):
    """题面：「顶层只有 `batch_id`、`components`、`requirements` 三项。」"""
    payload = sample()
    payload["operator"] = "night"
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_extra_requirement_key_is_bad_input(tmp_path):
    """题面：「`requirements` 的键集合恰为 `min_quality`、`max_viscosity`、`batch_kg`。」"""
    payload = sample()
    payload["requirements"]["target_color"] = 2
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_duplicate_json_key_is_bad_input(tmp_path):
    """题面：「退回原因是输入结构、键、类型、范围或总占比不合规；此时进程返回 1，标准错误内容为 `运行拒收: BAD_INPUT`。」"""
    raw = (
        '{"batch_id":"A","components":['
        '{"id":"A-1","min_fraction":0,"max_fraction":1,"cost_per_kg":1,"quality_index":1,"viscosity_cp":1},'
        '{"id":"B-1","min_fraction":0,"max_fraction":1,"cost_per_kg":1,"quality_index":1,"viscosity_cp":1},'
        '{"id":"C-1","min_fraction":0,"max_fraction":1,"cost_per_kg":1,"quality_index":1,"viscosity_cp":1}],'
        '"requirements":{"min_quality":0,"max_viscosity":2,"batch_kg":1},'
        '"requirements":{"min_quality":0,"max_viscosity":2,"batch_kg":1}}'
    )
    result, _, output = run_task(tmp_path, None, raw=raw)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_two_components_are_refused(tmp_path):
    """题面：「组分表由 3 到 16 条记录组成。」"""
    payload = sample()
    payload["components"] = payload["components"][:2]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_sixteen_components_are_accepted(tmp_path):
    """题面：「组分表由 3 到 16 条记录组成。」"""
    result, _, output = run_task(tmp_path, maximum_component_sample())
    assert result.returncode == 0 and read(output)["status"] == "optimal"


def test_maximum_component_batch_finishes_within_public_limit(tmp_path):
    """题面：「从启动到凭证发布须在 20 秒内结束。」"""
    source = write(tmp_path / "blend.json", maximum_component_sample())
    output = tmp_path / "output.json"
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=25,
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == ""
    assert elapsed < 20.0
    assert read(output)["status"] == "optimal"


def test_duplicate_component_id_is_refused(tmp_path):
    """题面：「`id` 匹配 `[A-Z][A-Z0-9_-]{1,15}` 且不得重复」。"""
    payload = sample()
    payload["components"][1]["id"] = payload["components"][0]["id"]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


@pytest.mark.parametrize("identifier", ["a-1", "1A", "A", "A B", "A" * 17])
def test_component_id_syntax_is_strict(tmp_path, identifier):
    """题面：「`id` 匹配 `[A-Z][A-Z0-9_-]{1,15}` 且不得重复」。"""
    payload = sample()
    payload["components"][0]["id"] = identifier
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_boolean_fraction_is_refused(tmp_path):
    """题面：「布尔值不算数字。」"""
    payload = sample()
    payload["components"][0]["min_fraction"] = False
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_boolean_max_active_is_refused(tmp_path):
    """题面：「`max_active` 是 2 到 4 的整数。」"""
    payload = sample()
    payload["requirements"]["max_active"] = True
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_reversed_fraction_bounds_are_refused(tmp_path):
    """题面：「两个占比都是 0 到 1 的有限数，且 `min_fraction` 不大于 `max_fraction`」。"""
    payload = sample()
    payload["components"][0]["min_fraction"] = 0.4
    payload["components"][0]["max_fraction"] = 0.2
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_minimum_fraction_sum_above_one_is_refused(tmp_path):
    """题面：「最小占比之和不超过 1，最大占比之和不小于 1。」"""
    payload = sample()
    for item in payload["components"]:
        item["min_fraction"] = 0.4
        item["max_fraction"] = 0.6
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_global_optimum_is_the_cheapest_vertex(tmp_path):
    """题面：「只存在一个配比能让总成本最低时直接输出它。」"""
    result, _, output = run_task(tmp_path, sample())
    document = read(output)
    fractions = [item["fraction"] for item in document["allocation"]]
    assert result.returncode == 0
    assert fractions == pytest.approx([0.2, 0.8, 0.0])
    assert document["total_cost"] == pytest.approx(180.0)


def test_fixed_activation_fee_changes_global_choice(tmp_path):
    """题面：「每只已开桶组分按所选批再加一笔 `activation_fee`。」"""
    result, _, output = run_task(tmp_path, fixed_fee_sample())
    document = read(output)
    fractions = [item["fraction"] for item in document["allocation"]]
    assert result.returncode == 0
    assert fractions == pytest.approx([0.0, 1.0, 0.0])
    assert document["activation_cost"] == pytest.approx(0.0)
    assert document["allocation"][1]["lot_id"] == "ALT-B"
    assert document["total_cost"] == pytest.approx(210.0)


def test_global_fixed_fee_choice_differs_from_unit_price_order(tmp_path):
    """题面：「固定费用必须计入全局成本。」"""
    payload = fixed_fee_sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    fractions = [item["fraction"] for item in document["allocation"]]
    unit_cost_a = payload["components"][0]["cost_per_kg"]
    unit_cost_b = payload["components"][1]["cost_per_kg"]
    assert result.returncode == 0
    assert unit_cost_a < unit_cost_b and fractions[1] == pytest.approx(1.0)


def test_fixed_fee_optimum_cannot_come_from_continuous_lp_alone(tmp_path):
    """题面：「单靠每千克单价排序，或单靠一个连续线性规划，都不足以覆盖这些选择。」"""
    payload = fixed_fee_sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    variable_cost = sum(
        component["cost_per_kg"] * item["fraction"]
        for component, item in zip(payload["components"], document["allocation"])
    )
    continuous_lp_only = min(item["cost_per_kg"] for item in payload["components"])
    assert result.returncode == 0
    assert variable_cost > continuous_lp_only


def test_active_count_respects_max_active(tmp_path):
    """题面：「占比大于零的组分视为已开桶，数量不得超过 `max_active`。」"""
    payload = sample()
    payload["components"][0]["max_fraction"] = 0.3
    payload["components"][1]["max_fraction"] = 0.3
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_active"] = 2
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert sum(item["fraction"] > 0 for item in document["allocation"]) <= 2


def test_alternate_lot_can_win_global_cost(tmp_path):
    """题面：「已启用组分必须从主供货批和备用批中二选一，不能两批混合。」"""
    payload = sample()
    payload["components"][0]["max_fraction"] = 1.0
    payload["components"][0]["cost_per_kg"] = 10.0
    payload["components"][0]["alternate"]["cost_per_kg"] = 0.5
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload["requirements"]["max_active"] = 2
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert document["allocation"][0]["lot_id"] == "ALT-A"
    assert document["activation_cost"] == pytest.approx(5.0)


def test_active_component_uses_one_lot_only(tmp_path):
    """题面：「已启用组分必须从主供货批和备用批中二选一，不能两批混合。」"""
    result, _, output = run_task(tmp_path, sample())
    document = read(output)
    active = [item for item in document["allocation"] if item["fraction"] > 0]
    assert result.returncode == 0
    assert active and all(isinstance(item["lot_id"], str) for item in active)


def test_alternate_lot_identifier_is_strict(tmp_path):
    """题面：「`lot_id` 全批唯一，且不得为 `primary`。」"""
    payload = sample()
    payload["components"][0]["alternate"]["lot_id"] = "lower-case"
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_alternate_lot_value_range_is_refused(tmp_path):
    """题面：「备用批沿用同名字段的数值口径。」"""
    payload = sample()
    payload["components"][0]["alternate"]["cost_per_kg"] = -0.1
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_supplier_active_limit_is_joint_constraint(tmp_path):
    """题面：「所选批的 `supplier` 计数不得超过 `max_active_per_supplier`。」"""
    payload = sample()
    for component in payload["components"]:
        component["supplier"] = "SHARED"
        component["alternate"]["supplier"] = "SHARED"
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload["requirements"]["max_active_per_supplier"] = 1
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert sum(item["fraction"] > 0 for item in document["allocation"]) == 1


def test_lot_choice_and_supplier_limit_are_joint(tmp_path):
    """题面：「供货批选择和供应商启用上限也必须与开桶集合共同决定最低成本。」"""
    payload = sample()
    payload["components"][0]["supplier"] = "SHARED-A"
    payload["components"][1]["supplier"] = "SHARED-A"
    payload["components"][0]["alternate"]["supplier"] = "SHARED-A"
    payload["components"][1]["alternate"]["supplier"] = "SHARED-A"
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload["requirements"]["max_active_per_supplier"] = 1
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert sum(item["fraction"] > 0 for item in document["allocation"]) == 1


def test_equal_cost_uses_lot_id_tiebreak(tmp_path):
    """题面：「若占比向量仍相同，再按同一组分顺序比较 `lot_id`，主批字符串 `primary` 也参加比较。」"""
    payload = sample()
    payload["components"][0]["max_fraction"] = 1.0
    payload["components"][0]["activation_fee"] = 0.0
    payload["components"][0]["alternate"]["cost_per_kg"] = 1.0
    payload["components"][0]["alternate"]["activation_fee"] = 0.0
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert document["allocation"][0]["lot_id"] == "ALT-A"


def test_incompatible_alternate_lots_are_not_combined(tmp_path):
    """题面：「同一条中的两批不得同时出现在最终配方里。」"""
    payload = sample()
    payload["components"][0]["max_fraction"] = 1.0
    payload["components"][1]["max_fraction"] = 1.0
    payload["components"][0]["alternate"]["cost_per_kg"] = 0.5
    payload["components"][1]["alternate"]["cost_per_kg"] = 0.6
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    lots = {item["lot_id"] for item in document["allocation"]}
    assert result.returncode == 0
    assert not {"ALT-A", "ALT-B"}.issubset(lots)


def test_incompatible_pair_schema_is_strict(tmp_path):
    """题面：「每项是两个备用批 `lot_id` 组成的二元素数组，两个编号必须按字典序升序、指向不同备用批，且整组不得重复」"""
    payload = sample()
    payload["incompatible_lot_pairs"] = [["ALT-B", "ALT-A"]]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_activation_fee_is_added_to_total_cost(tmp_path):
    """题面：「`batch_kg * Σ 所选批 cost_per_kg_i * x_i + activation_cost`。」"""
    payload = fixed_fee_sample()
    payload["components"][0]["activation_fee"] = 5.0
    payload["components"][1]["cost_per_kg"] = 2.1
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    variable_cost = sum(
        component["cost_per_kg"] * item["fraction"]
        for component, item in zip(payload["components"], document["allocation"])
    )
    expected = variable_cost * payload["requirements"]["batch_kg"] + document["activation_cost"]
    assert result.returncode == 0 and document["total_cost"] == pytest.approx(expected)


def test_too_many_mandatory_components_is_infeasible(tmp_path):
    """题面：「占比大于零的组分视为已开桶，数量不得超过 `max_active`。」"""
    payload = sample()
    payload["requirements"]["max_active"] = 2
    for item in payload["components"]:
        item["min_fraction"] = 0.1
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: INFEASIBLE\n" and not output.exists()


def test_quality_and_viscosity_constraints_hold(tmp_path):
    """题面：「质量不得低于 `min_quality`，黏度不得超过 `max_viscosity`。」"""
    payload = sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert document["quality_index"] >= payload["requirements"]["min_quality"]
    assert document["viscosity_cp"] <= payload["requirements"]["max_viscosity"]


def test_quality_index_is_weighted_sum(tmp_path):
    """题面：「质量不得低于 `min_quality`」。"""
    payload = sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    expected = sum(
        item["quality_index"] * allocation["fraction"]
        for item, allocation in zip(payload["components"], document["allocation"])
    )
    assert result.returncode == 0 and document["quality_index"] == pytest.approx(expected)


def test_viscosity_is_weighted_sum(tmp_path):
    """题面：「黏度不得超过 `max_viscosity`。」"""
    payload = sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    expected = sum(
        item["viscosity_cp"] * allocation["fraction"]
        for item, allocation in zip(payload["components"], document["allocation"])
    )
    assert result.returncode == 0 and document["viscosity_cp"] == pytest.approx(expected)


def test_mass_is_fraction_times_batch(tmp_path):
    """题面：「`mass_kg` 等于占比乘 `batch_kg`。」"""
    result, _, output = run_task(tmp_path, sample())
    document = read(output)
    masses = [item["mass_kg"] for item in document["allocation"]]
    assert result.returncode == 0 and masses == pytest.approx([20.0, 80.0, 0.0])


def test_allocation_has_exact_keys_and_input_order(tmp_path):
    """题面：「`allocation` 按输入组分顺序排列，每项恰好有 `id`、`lot_id`、`fraction`、`mass_kg` 四个键」。"""
    payload = sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert [item["id"] for item in document["allocation"]] == [item["id"] for item in payload["components"]]
    assert all(set(item) == {"id", "lot_id", "fraction", "mass_kg"} for item in document["allocation"])


def test_inactive_component_has_null_lot_id(tmp_path):
    """题面：「未启用组分的 `lot_id` 写 `null`，启用组分写所选备用批的实际 `lot_id`，选主批时写 `primary`。」"""
    result, _, output = run_task(tmp_path, sample())
    document = read(output)
    assert result.returncode == 0
    assert document["allocation"][2]["lot_id"] is None


def test_equal_cost_tie_uses_lexicographic_fraction_vector(tmp_path):
    """题面：「并列解按 `components` 顺序做字典序比较。」"""
    result, _, output = run_task(tmp_path, tie_sample())
    document = read(output)
    fractions = [item["fraction"] for item in document["allocation"]]
    assert result.returncode == 0 and fractions == pytest.approx([0.0, 0.6, 0.4])


def test_near_equal_cost_uses_same_lexicographic_tiebreak(tmp_path):
    """题面：「若若干顶点的成本相差不超过 1e-10，把它们视为同一个最低成本。」"""
    payload = tie_sample()
    payload["components"][1]["cost_per_kg"] += 1e-12
    result, _, output = run_task(tmp_path, payload)
    fractions = [item["fraction"] for item in read(output)["allocation"]]
    assert result.returncode == 0 and fractions == pytest.approx([0.0, 0.6, 0.4])


def test_tiebreak_repeat_is_stable(tmp_path):
    """题面：「处理并列解时，再按 `components` 输入顺序比较占比序列。」"""
    result, source, output = run_task(tmp_path, tie_sample())
    first = output.read_bytes()
    second = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and second.returncode == 0 and output.read_bytes() == first


def test_infeasible_constraints_are_refused(tmp_path):
    """题面：「标准错误仅写 `运行拒收: INFEASIBLE`。」"""
    payload = sample()
    payload["requirements"]["min_quality"] = 100.0
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: INFEASIBLE\n" and not output.exists()


def test_result_key_order_and_compact_bytes(tmp_path):
    """题面：「配料结果压成紧凑的一行 JSON，末尾保留一个换行。」"""
    result, _, output = run_task(tmp_path, sample())
    raw = output.read_bytes()
    document = json.loads(raw)
    assert result.returncode == 0
    assert list(document) == ["batch_id", "status", "activation_cost", "total_cost", "allocation", "quality_index", "viscosity_cp"]
    assert b": " not in raw and raw.endswith(b"\n") and raw.count(b"\n") == 1


def test_absent_parent_is_io_failure(tmp_path):
    """题面：「这种磁盘故障以退出码 1 结束，标准错误只报告 `运行拒收: IO_FAILURE`。」"""
    source = write(tmp_path / "blend.json", sample())
    output = tmp_path / "absent-parent" / "result.json"
    result = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1 and result.stderr == "运行拒收: IO_FAILURE\n" and not output.exists()


def test_bad_input_preserves_existing_result(tmp_path):
    """题面：「任何写入或替换失败都要保留旧结果。」"""
    result, source, output = run_task(tmp_path, sample())
    before = output.read_bytes()
    payload = sample()
    payload["requirements"]["batch_kg"] = True
    write(source, payload)
    rejected = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and rejected.returncode == 1
    assert output.read_bytes() == before and "traceback" not in rejected.stderr.lower()


def test_success_leaves_no_temporary_files(tmp_path):
    """题面：「发布结束不能留下临时文件。」"""
    result, _, output = run_task(tmp_path, sample())
    leftovers = [path.name for path in tmp_path.iterdir() if path.name.startswith(".blend-")]
    assert result.returncode == 0 and output.exists() and leftovers == []


def test_repeat_run_is_byte_identical(tmp_path):
    """题面：「用原配料单重跑时，凭证应保持原有字节。」"""
    result, source, output = run_task(tmp_path, sample())
    first = output.read_bytes()
    second = subprocess.run(
        [sys.executable, ENTRY, str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0 and second.returncode == 0 and output.read_bytes() == first


def test_two_file_arguments_are_accepted(tmp_path):
    """题面：「程序调用时只接收输入和结果两个路径：」。"""
    result, _, output = run_task(tmp_path, sample())
    assert result.returncode == 0 and output.is_file()
