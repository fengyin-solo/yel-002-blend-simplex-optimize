"""Acceptance tests for the constrained solvent blend optimizer."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


ENTRY = os.environ.get("BLEND_OPTIMIZER_ENTRY", "/app/blend_simplex_optimize.py")
TRUSTED_PYTHON = Path("/usr/local/bin/python3")
SETPRIV = Path("/usr/bin/setpriv")


def candidate_argv(*arguments):
    values = [str(value) for value in arguments]
    if SETPRIV.is_file() and TRUSTED_PYTHON.is_file():
        return [
            str(SETPRIV),
            "--reuid=65534",
            "--regid=65534",
            "--clear-groups",
            str(TRUSTED_PYTHON),
            "-I",
            "-B",
            *values,
        ]
    return [sys.executable, "-I", "-B", *values]


def expose_to_candidate(path):
    if not SETPRIV.is_file():
        return
    path.chmod(0o777)
    for parent in path.parents:
        if parent == Path("/tmp"):
            break
        parent.chmod(parent.stat().st_mode | 0o001)


def invoke_candidate(path, *arguments, timeout=30):
    expose_to_candidate(Path(path))
    return subprocess.run(
        candidate_argv(*arguments),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def certified_fractions(count):
    if count == 3:
        return [
            [0.6, 0.3, 0.1],
            [0.2, 0.5, 0.3],
            [0.1, 0.2, 0.7],
        ]
    weights = (0.7, 0.2, 0.1)
    rows = []
    for row in range(count):
        values = [
            weights[delta] if delta < len(weights) else 0.0
            for column in range(count)
            for delta in ((column - row) % count,)
        ]
        total = sum(values)
        rows.append([value / total for value in values])
    return rows


def rebuild_certification(payload, selected_lot_ids):
    lots = {item["lot_id"]: item for item in payload["supply_lots"]}
    selected = [lots[lot_id] for lot_id in selected_lot_ids]
    runs = []
    for fractions in certified_fractions(len(payload["components"])):
        metrics = {}
        for key in ("cost_per_kg", "quality_index", "viscosity_cp"):
            metrics[key] = sum(
                share * lot[key] for share, lot in zip(fractions, selected)
            )
        runs.append({"fractions": fractions, **metrics})
    payload["certification_runs"] = runs
    return payload


def option(payload, component_index, alternate=False):
    bundle_id = ("BND-A", "BND-B", "BND-C")[component_index]
    bundle = next(item for item in payload["lot_bundles"] if item["bundle_id"] == bundle_id)
    suffix = "ALT" if alternate else "PRM"
    lot_id = next(value for value in bundle["lots"] if value.endswith(suffix))
    return next(item for item in payload["supply_lots"] if item["lot_id"] == lot_id)


def sample():
    payload = {
        "batch_id": "SPRAY-284",
        "components": [
            {
                "id": "SOL-A",
                "min_fraction": 0.0,
                "max_fraction": 0.2,
            },
            {
                "id": "SOL-B",
                "min_fraction": 0.0,
                "max_fraction": 1.0,
            },
            {
                "id": "SOL-C",
                "min_fraction": 0.0,
                "max_fraction": 1.0,
            },
        ],
        "supply_lots": [
            {"lot_id": "L-A-PRM", "supplier": "SUP-A", "cost_per_kg": 1.0, "quality_index": 0.0, "viscosity_cp": 10.0, "activation_fee": 0.0},
            {"lot_id": "L-A-ALT", "supplier": "SUP-C", "cost_per_kg": 1.2, "quality_index": 1.0, "viscosity_cp": 10.0, "activation_fee": 5.0},
            {"lot_id": "L-B-PRM", "supplier": "SUP-B", "cost_per_kg": 2.0, "quality_index": 10.0, "viscosity_cp": 10.0, "activation_fee": 0.0},
            {"lot_id": "L-B-ALT", "supplier": "SUP-D", "cost_per_kg": 2.1, "quality_index": 9.0, "viscosity_cp": 10.0, "activation_fee": 0.0},
            {"lot_id": "L-C-PRM", "supplier": "SUP-C", "cost_per_kg": 5.0, "quality_index": 5.0, "viscosity_cp": 10.0, "activation_fee": 0.0},
            {"lot_id": "L-C-ALT", "supplier": "SUP-E", "cost_per_kg": 5.5, "quality_index": 5.0, "viscosity_cp": 10.0, "activation_fee": 0.0},
        ],
        "lot_bundles": [
            {"bundle_id": "BND-C", "lots": ["L-C-ALT", "L-C-PRM"]},
            {"bundle_id": "BND-A", "lots": ["L-A-ALT", "L-A-PRM"]},
            {"bundle_id": "BND-B", "lots": ["L-B-ALT", "L-B-PRM"]},
        ],
        "requirements": {
            "min_quality": 5.0,
            "max_viscosity": 10.0,
            "batch_kg": 100.0,
            "max_active": 3,
            "max_active_per_supplier": 3,
            "additional_orders": [],
        },
        "certification_runs": [],
        "incompatible_lot_pairs": [["L-A-ALT", "L-B-ALT"]],
    }
    for lot in payload["supply_lots"]:
        lot["available_kg"] = 1000000.0
    return rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])


def tie_sample():
    payload = sample()
    payload["components"][0]["max_fraction"] = 0.3
    payload["components"][1]["max_fraction"] = 0.7
    payload["components"][2]["min_fraction"] = 0.4
    payload["components"][2]["max_fraction"] = 0.4
    for lot in payload["supply_lots"]:
        if lot["lot_id"].endswith("ALT"):
            lot["cost_per_kg"] = 100.0
            lot["activation_fee"] = 0.0
    payload["supply_lots"][0]["cost_per_kg"] = 2.0
    payload["supply_lots"][2]["cost_per_kg"] = 2.0
    payload["requirements"]["min_quality"] = 5.0
    return rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])


def maximum_component_sample():
    payload = sample()
    selected = ["L-A-PRM", "L-B-PRM", "L-C-PRM"]
    for index in range(4, 13):
        payload["components"].append(
            {"id": f"EXT-{index}", "min_fraction": 0.0, "max_fraction": 0.04}
        )
        primary = f"L-{index}-PRM"
        alternate = f"L-{index}-ALT"
        payload["supply_lots"].extend(
            [
                {"lot_id": primary, "supplier": f"SUP-{index:02d}", "cost_per_kg": 2.2, "quality_index": 3.0 + index / 10, "viscosity_cp": 8.0, "activation_fee": 0.0},
                {"lot_id": alternate, "supplier": f"SUP-{index + 20:02d}", "cost_per_kg": 2.5, "quality_index": 4.0 + index / 10, "viscosity_cp": 9.0, "activation_fee": 1.0},
            ]
        )
        payload["lot_bundles"].append(
            {"bundle_id": f"BND-{index}", "lots": sorted([primary, alternate])}
        )
        selected.append(primary)
    for lot in payload["supply_lots"]:
        lot["available_kg"] = 1000000.0
    return rebuild_certification(payload, selected)


def fixed_fee_sample():
    payload = sample()
    payload["components"][0]["max_fraction"] = 1.0
    payload["supply_lots"][0]["activation_fee"] = 150.0
    payload["supply_lots"][1]["cost_per_kg"] = 9.0
    payload["supply_lots"][1]["activation_fee"] = 0.0
    payload["supply_lots"][2]["cost_per_kg"] = 2.4
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_active"] = 2
    return rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])


def write(path, document, raw=None):
    if raw is None:
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_bytes(raw.encode("utf-8"))
    return path


def run_task(tmp_path, document, *, stem="output", raw=None):
    source = write(tmp_path / "blend.json", document, raw=raw)
    output = tmp_path / f"{stem}.json"
    result = invoke_candidate(tmp_path, ENTRY, source, output)
    return result, source, output


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_success_is_quiet(tmp_path):
    """题面：「bundle 记录只保留 `bundle_id` 与 `lots`，不接受补充说明」。"""
    result, _, output = run_task(tmp_path, sample())
    assert result.returncode == 0 and result.stdout == "" and result.stderr == "" and output.exists()


def test_absent_batch_id_is_bad_input(tmp_path):
    """题面：「工单号写入 `batch_id`；可见字符数须为 1–64，两端不留空白，内部控制码不收。」"""
    payload = sample()
    payload.pop("batch_id")
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_batch_id_visible_character_boundaries_are_accepted(tmp_path):
    """题面：「工单号写入 `batch_id`；可见字符数须为 1–64，两端不留空白，内部控制码不收。」"""
    for index, batch_id in enumerate(("A", "A" * 64, "A B")):
        payload = sample()
        payload["batch_id"] = batch_id
        result, _, output = run_task(tmp_path, payload, stem=f"batch-{index}")
        assert result.returncode == 0 and read(output)["batch_id"] == batch_id


def test_absent_components_is_bad_input(tmp_path):
    """题面：「配料单先按 UTF-8 字节流读取；若 JSON 顶层出现下列键名之外的内容，直接视为坏单：」"""
    payload = sample()
    payload.pop("components")
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_absent_requirements_is_bad_input(tmp_path):
    """题面：「配料单先按 UTF-8 字节流读取；若 JSON 顶层出现下列键名之外的内容，直接视为坏单：」"""
    payload = sample()
    payload.pop("requirements")
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_extra_component_key_is_bad_input(tmp_path):
    """题面：「配方由 3–12 个组分组成，每条记录仅接受：」"""
    payload = sample()
    payload["components"][0]["supplier"] = "north"
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_absent_activation_fee_is_bad_input(tmp_path):
    """题面：「每条供货批只含：」"""
    payload = sample()
    payload["supply_lots"][0].pop("activation_fee")
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_extra_top_level_key_is_bad_input(tmp_path):
    """题面：「读入值不合约定时，终端只回 `运行拒收: BAD_INPUT`（退出码 1）。」"""
    payload = sample()
    payload["operator"] = "night"
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_extra_requirement_key_is_bad_input(tmp_path):
    """题面：「其余字段沿用上面的值域。」"""
    payload = sample()
    payload["requirements"]["target_color"] = 2
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_duplicate_json_key_is_bad_input(tmp_path):
    """题面：「拒绝请求时不要显示 traceback 文本。」"""
    encoded = json.dumps(sample(), ensure_ascii=False, separators=(",", ":"))
    raw = encoded[:-1] + ',"batch_id":"DUPLICATE"}\n'
    result, _, output = run_task(tmp_path, None, raw=raw)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_two_components_are_refused(tmp_path):
    """题面：「配方由 3–12 个组分组成，每条记录仅接受：」"""
    payload = sample()
    payload["components"] = payload["components"][:2]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_twelve_components_are_accepted(tmp_path):
    """题面：「工艺组送来的配方单只有组分比例。」"""
    result, _, output = run_task(tmp_path, maximum_component_sample())
    assert result.returncode == 0 and read(output)["status"] == "optimal"


def test_maximum_component_batch_finishes_within_public_limit(tmp_path):
    """题面：「试配记录数与配方组分数相同。」"""
    source = write(tmp_path / "blend.json", maximum_component_sample())
    output = tmp_path / "output.json"
    started = time.monotonic()
    result = invoke_candidate(tmp_path, ENTRY, source, output, timeout=25)
    elapsed = time.monotonic() - started
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == ""
    assert elapsed < 20.0
    assert read(output)["status"] == "optimal"


def test_duplicate_component_id_is_refused(tmp_path):
    """题面：「每只组分配一个 bundle，数量必须与配方组分数一致。」"""
    payload = sample()
    payload["components"][1]["id"] = payload["components"][0]["id"]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


@pytest.mark.parametrize("identifier", ["a-1", "1A", "A", "A B", "A" * 17])
def test_component_id_syntax_is_strict(tmp_path, identifier):
    """题面：「试配系数矩阵必须满秩。」"""
    payload = sample()
    payload["components"][0]["id"] = identifier
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_boolean_fraction_is_refused(tmp_path):
    """题面：「试配矩阵奇异、性质三元组无法唯一匹配供货批，或 bundle 认领不唯一时，标准错误只写 `运行拒收: BAD_MODEL`，退出码为 1。」"""
    payload = sample()
    payload["components"][0]["min_fraction"] = False
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_boolean_max_active_is_refused(tmp_path):
    """题面：「`order_id` 不得等于 `PRIMARY`。」"""
    payload = sample()
    payload["requirements"]["max_active"] = True
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_reversed_fraction_bounds_are_refused(tmp_path):
    """题面：「两个占比都是 0 到 1 的有限数，前项不大于后项。」"""
    payload = sample()
    payload["components"][0]["min_fraction"] = 0.4
    payload["components"][0]["max_fraction"] = 0.2
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_minimum_fraction_sum_above_one_is_refused(tmp_path):
    """题面：「占比域还须满足：」"""
    payload = sample()
    for item in payload["components"]:
        item["min_fraction"] = 0.4
        item["max_fraction"] = 0.6
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_global_optimum_is_the_cheapest_vertex(tmp_path):
    """题面：「批选择与占比必须一起决定全局最低成本。」"""
    result, _, output = run_task(tmp_path, sample())
    document = read(output)
    fractions = [item["fraction"] for item in document["allocation"]]
    assert result.returncode == 0
    assert fractions == pytest.approx([0.2, 0.8, 0.0])
    assert document["total_cost"] == pytest.approx(180.0)


def test_bundle_mapping_is_recovered_independently_of_array_order(tmp_path):
    """题面：「bundle 与组分之间的对应关系故意没有给出，不能按数组顺序直接配对。」"""
    result, _, output = run_task(tmp_path, sample())
    document = read(output)
    assert result.returncode == 0
    assert document["bundle_map"] == [
        {"id": "SOL-A", "bundle_id": "BND-A"},
        {"id": "SOL-B", "bundle_id": "BND-B"},
        {"id": "SOL-C", "bundle_id": "BND-C"},
    ]


def test_singular_certification_matrix_is_bad_model(tmp_path):
    """题面：「三个性质向量必须由这些观测唯一恢复。」"""
    payload = sample()
    payload["certification_runs"][1]["fractions"] = payload["certification_runs"][0]["fractions"]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_MODEL\n" and not output.exists()


def test_ambiguous_lot_properties_are_bad_model(tmp_path):
    """题面：「恢复出的三元组必须分别落到唯一一个供货批。」"""
    payload = sample()
    primary = option(payload, 0)
    alternate = option(payload, 0, alternate=True)
    for key in ("cost_per_kg", "quality_index", "viscosity_cp"):
        alternate[key] = primary[key]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_MODEL\n" and not output.exists()


def test_each_lot_must_appear_in_exactly_one_bundle(tmp_path):
    # CASE BND-COVERAGE-01 BND-ID-01
    """题面：「每只供货批必须且只能出现在一个 bundle 中。」"""
    payload = sample()
    payload["lot_bundles"][0]["lots"][1] = payload["lot_bundles"][1]["lots"][0]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_certification_run_count_is_exact(tmp_path):
    """题面：「试配记录数与配方组分数相同。」"""
    payload = sample()
    payload["certification_runs"].pop()
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_fixed_activation_fee_changes_global_choice(tmp_path):
    """题面：「每只已开桶组分按所选批再加一笔 `activation_fee`。」"""
    result, _, output = run_task(tmp_path, fixed_fee_sample())
    document = read(output)
    fractions = [item["fraction"] for item in document["allocation"]]
    assert result.returncode == 0
    assert fractions == pytest.approx([0.0, 1.0, 0.0])
    assert document["activation_cost"] == pytest.approx(0.0)
    assert document["allocation"][1]["lot_id"] == "L-B-ALT"
    assert document["total_cost"] == pytest.approx(210.0)


def test_multiple_orders_share_activation_fee_once(tmp_path):
    """题面：「针对同一套配方单和供货批准备的若干次日生产任务，固定激活费按整份生产计划只收一次。」"""
    payload = sample()
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload["requirements"]["max_active"] = 2
    option(payload, 1)["activation_fee"] = 100.0
    option(payload, 1, alternate=True)["activation_fee"] = 100.0
    payload["requirements"]["additional_orders"] = [
        {
            "order_id": "SHIFT-B",
            "batch_kg": 100.0,
            "min_quality": 0.0,
            "max_viscosity": 20.0,
        },
        {
            "order_id": "SHIFT-C",
            "batch_kg": 100.0,
            "min_quality": 0.0,
            "max_viscosity": 20.0,
        },
    ]
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert document["activation_cost"] == pytest.approx(100.0)
    assert document["total_cost"] == pytest.approx(640.0)
    assert [item["order_id"] for item in document["additional_orders"]] == ["SHIFT-B", "SHIFT-C"]
    assert all(
        item["allocation"][1]["fraction"] == pytest.approx(0.8)
        for item in document["additional_orders"]
    )


def test_additional_order_record_has_exact_keys_and_id(tmp_path):
    """题面：「`additional_orders` 是 0 至 2 条追加生产任务。每条成员集合如下：」"""
    payload = sample()
    payload["requirements"]["additional_orders"] = [
        {
            "order_id": "PRIMARY",
            "batch_kg": 100.0,
            "min_quality": 0.0,
            "max_viscosity": 20.0,
        }
    ]
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_global_component_union_respects_active_limit(tmp_path):
    """题面：「整份计划的组分并集上限为 `max_active`。」"""
    payload = sample()
    payload["components"][0]["max_fraction"] = 1.0
    payload["requirements"]["min_quality"] = 15.0
    payload["requirements"]["max_viscosity"] = 1000.0
    payload["requirements"]["max_active"] = 2
    for index, quality, viscosity in ((0, 20.0, 1000.0), (1, 10.0, 10.0), (2, 5.0, 6.0)):
        option(payload, index)["quality_index"] = quality
        option(payload, index)["viscosity_cp"] = viscosity
        option(payload, index, alternate=True)["quality_index"] = quality
        option(payload, index, alternate=True)["viscosity_cp"] = viscosity
    payload["requirements"]["additional_orders"] = [
        {
            "order_id": "SHIFT-B",
            "batch_kg": 100.0,
            "min_quality": 0.0,
            "max_viscosity": 6.5,
        },
        {
            "order_id": "SHIFT-C",
            "batch_kg": 100.0,
            "min_quality": 8.0,
            "max_viscosity": 10.0,
        },
    ]
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: INFEASIBLE\n" and not output.exists()


def test_additional_order_output_shape_and_order(tmp_path):
    """题面：「`additional_orders` 按输入的追加任务顺序展开。」"""
    payload = sample()
    payload["requirements"]["additional_orders"] = [
        {"order_id": "SHIFT-B", "batch_kg": 50.0, "min_quality": 0.0, "max_viscosity": 20.0},
        {"order_id": "SHIFT-C", "batch_kg": 75.0, "min_quality": 0.0, "max_viscosity": 20.0},
    ]
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert [item["order_id"] for item in document["additional_orders"]] == ["SHIFT-B", "SHIFT-C"]
    assert all(
        set(item) == {"order_id", "allocation", "quality_index", "viscosity_cp"}
        for item in document["additional_orders"]
    )


def test_additional_order_output_keys_are_exact(tmp_path):
    """题面：「每项的成员集合如下：」"""
    payload = sample()
    payload["requirements"]["additional_orders"] = [
        {"order_id": "SHIFT-B", "batch_kg": 50.0, "min_quality": 5.0, "max_viscosity": 10.0}
    ]
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert set(document["additional_orders"][0]) == {
        "allocation", "order_id", "quality_index", "viscosity_cp"
    }


def test_shared_lot_inventory_is_global_across_orders(tmp_path):
    """题面：「各订单共享供货批库存。单只 lot 的累计耗用上限由其 `available_kg` 给出。」"""
    payload = sample()
    payload["requirements"]["min_quality"] = 5.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload["requirements"]["max_active"] = 2
    option(payload, 1)["available_kg"] = 100.0
    payload["requirements"]["additional_orders"] = [
        {"order_id": "SHIFT-B", "batch_kg": 100.0, "min_quality": 5.0, "max_viscosity": 20.0}
    ]
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    allocations = [document["allocation"], document["additional_orders"][0]["allocation"]]
    used = {
        lot_id: sum(
            item["mass_kg"]
            for allocation in allocations
            for item in allocation
            if item["lot_id"] == lot_id
        )
        for lot_id in ("L-B-PRM", "L-B-ALT")
    }
    assert max(used.values()) <= 100.0 + 1e-7


def test_global_fixed_fee_choice_differs_from_unit_price_order(tmp_path):
    """题面：「`bundle_map` 每项恰好含 `id` 和 `bundle_id`。」"""
    payload = fixed_fee_sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    fractions = [item["fraction"] for item in document["allocation"]]
    unit_cost_a = option(payload, 0)["cost_per_kg"]
    unit_cost_b = option(payload, 1)["cost_per_kg"]
    assert result.returncode == 0
    assert unit_cost_a < unit_cost_b and fractions[1] == pytest.approx(1.0)


def test_fixed_fee_optimum_cannot_come_from_continuous_lp_alone(tmp_path):
    """题面：「按单价排序或单独解连续线性规划都不足以得到它。」"""
    payload = fixed_fee_sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    lots = {item["lot_id"]: item for item in payload["supply_lots"]}
    variable_cost = sum(
        lots[item["lot_id"]]["cost_per_kg"] * item["fraction"]
        for item in document["allocation"]
        if item["lot_id"] is not None
    )
    continuous_lp_only = min(item["cost_per_kg"] for item in payload["supply_lots"])
    assert result.returncode == 0
    assert variable_cost > continuous_lp_only


def test_active_count_respects_max_active(tmp_path):
    """题面：「每一试配记录只含：」"""
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
    """题面：「不同组分不能共用同一只供货批。」"""
    payload = sample()
    payload["components"][0]["max_fraction"] = 1.0
    option(payload, 0)["cost_per_kg"] = 10.0
    option(payload, 0, alternate=True)["cost_per_kg"] = 0.5
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload["requirements"]["max_active"] = 2
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert document["allocation"][0]["lot_id"] == "L-A-ALT"
    assert document["activation_cost"] == pytest.approx(5.0)


def test_active_component_uses_one_lot_only(tmp_path):
    """题面：「每只已开桶组分按所选批再加一笔 `activation_fee`。」"""
    result, _, output = run_task(tmp_path, sample())
    document = read(output)
    active = [item for item in document["allocation"] if item["fraction"] > 0]
    assert result.returncode == 0
    assert active and all(isinstance(item["lot_id"], str) for item in active)


def test_alternate_lot_identifier_is_strict(tmp_path):
    """题面：「`lots` 是两个 `lot_id` 按字典序组成的二元素数组。」"""
    payload = sample()
    option(payload, 0, alternate=True)["lot_id"] = "lower-case"
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_alternate_lot_value_range_is_refused(tmp_path):
    """题面：「每条供货批只含：」"""
    payload = sample()
    option(payload, 0, alternate=True)["cost_per_kg"] = -0.1
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_supplier_active_limit_is_joint_constraint(tmp_path):
    """题面：「每个供应商最多启用 `max_active_per_supplier` 个组分。」"""
    payload = sample()
    for lot in payload["supply_lots"]:
        lot["supplier"] = "SHARED"
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload["requirements"]["max_active_per_supplier"] = 1
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert sum(item["fraction"] > 0 for item in document["allocation"]) == 1


def test_lot_choice_and_supplier_limit_are_joint(tmp_path):
    """题面：「每个已开桶组分从它恢复出的 bundle 的两只供货批中二选一。」"""
    payload = sample()
    for lot_id in ("L-A-PRM", "L-A-ALT", "L-B-PRM", "L-B-ALT"):
        next(item for item in payload["supply_lots"] if item["lot_id"] == lot_id)["supplier"] = "SHARED-A"
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload["requirements"]["max_active_per_supplier"] = 1
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert sum(item["fraction"] > 0 for item in document["allocation"]) == 1


def test_equal_cost_uses_lot_id_tiebreak(tmp_path):
    """题面：「占比仍相同时，按同一顺序比较所选 `lot_id`。」"""
    payload = sample()
    payload["components"][0]["max_fraction"] = 1.0
    option(payload, 0)["activation_fee"] = 0.0
    option(payload, 0, alternate=True)["cost_per_kg"] = 1.0
    option(payload, 0, alternate=True)["activation_fee"] = 0.0
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert document["allocation"][0]["lot_id"] == "L-A-ALT"


def test_incompatible_alternate_lots_are_not_combined(tmp_path):
    """题面：「每项为两个供货批编号按字典序组成的二元素数组。」"""
    payload = sample()
    payload["components"][0]["max_fraction"] = 1.0
    payload["components"][1]["max_fraction"] = 1.0
    option(payload, 0, alternate=True)["cost_per_kg"] = 0.5
    option(payload, 1, alternate=True)["cost_per_kg"] = 0.6
    payload["requirements"]["min_quality"] = 0.0
    payload["requirements"]["max_viscosity"] = 20.0
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    lots = {item["lot_id"] for item in document["allocation"]}
    assert result.returncode == 0
    assert not {"L-A-ALT", "L-B-ALT"}.issubset(lots)


def test_incompatible_pair_schema_is_strict(tmp_path):
    """题面：「二者不得相同，整组不得重复。」"""
    payload = sample()
    payload["incompatible_lot_pairs"] = [["L-B-ALT", "L-A-ALT"]]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_incompatible_pair_rejects_equal_lots(tmp_path):
    """题面：「`incompatible_lot_pairs` 是最多 48 项的数组。每项为两个供货批编号按字典序组成的二元素数组。二者不得相同，整组不得重复。」"""
    payload = sample()
    payload["incompatible_lot_pairs"] = [["L-A-ALT", "L-A-ALT"]]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_incompatible_pair_rejects_duplicate_entries(tmp_path):
    """题面：「整组不得重复。」"""
    payload = sample()
    pair = ["L-A-ALT", "L-B-ALT"]
    payload["incompatible_lot_pairs"] = [pair, pair.copy()]
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: BAD_INPUT\n" and not output.exists()


def test_activation_fee_is_added_to_total_cost(tmp_path):
    """题面：「质量指数按所选批加权求和，质量不得低于 `min_quality`。」"""
    payload = fixed_fee_sample()
    option(payload, 0)["activation_fee"] = 5.0
    option(payload, 1)["cost_per_kg"] = 2.1
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    lots = {item["lot_id"]: item for item in payload["supply_lots"]}
    variable_cost = sum(
        lots[item["lot_id"]]["cost_per_kg"] * item["fraction"]
        for item in document["allocation"]
        if item["lot_id"] is not None
    )
    expected = variable_cost * payload["requirements"]["batch_kg"] + document["activation_cost"]
    assert result.returncode == 0 and document["total_cost"] == pytest.approx(expected)


def test_too_many_mandatory_components_is_infeasible(tmp_path):
    """题面：「开桶上限是 `max_active`。」"""
    payload = sample()
    payload["requirements"]["max_active"] = 2
    for item in payload["components"]:
        item["min_fraction"] = 0.1
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: INFEASIBLE\n" and not output.exists()


def test_quality_and_viscosity_constraints_hold(tmp_path):
    """题面：「黏度按所选批加权求和。」"""
    payload = sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert document["quality_index"] >= payload["requirements"]["min_quality"]
    assert document["viscosity_cp"] <= payload["requirements"]["max_viscosity"]


def test_quality_index_is_weighted_sum(tmp_path):
    """题面：「质量指数按所选批加权求和，质量不得低于 `min_quality`。」"""
    payload = sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    lots = {item["lot_id"]: item for item in payload["supply_lots"]}
    expected = sum(
        lots[allocation["lot_id"]]["quality_index"] * allocation["fraction"]
        for allocation in document["allocation"]
        if allocation["lot_id"] is not None
    )
    assert result.returncode == 0 and document["quality_index"] == pytest.approx(expected)


def test_viscosity_is_weighted_sum(tmp_path):
    """题面：「黏度按所选批加权求和。」"""
    payload = sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    lots = {item["lot_id"]: item for item in payload["supply_lots"]}
    expected = sum(
        lots[allocation["lot_id"]]["viscosity_cp"] * allocation["fraction"]
        for allocation in document["allocation"]
        if allocation["lot_id"] is not None
    )
    assert result.returncode == 0 and document["viscosity_cp"] == pytest.approx(expected)


def test_mass_is_fraction_times_batch(tmp_path):
    """题面：「`bundle_map` 的排列次序跟随配方单中的组分次序。」"""
    result, _, output = run_task(tmp_path, sample())
    document = read(output)
    masses = [item["mass_kg"] for item in document["allocation"]]
    assert result.returncode == 0 and masses == pytest.approx([20.0, 80.0, 0.0])


def test_allocation_has_exact_keys_and_input_order(tmp_path):
    """题面：「下料明细沿用配方单的组分次序。」"""
    payload = sample()
    result, _, output = run_task(tmp_path, payload)
    document = read(output)
    assert result.returncode == 0
    assert [item["id"] for item in document["allocation"]] == [item["id"] for item in payload["components"]]
    assert all(set(item) == {"id", "lot_id", "fraction", "mass_kg"} for item in document["allocation"])


def test_inactive_component_has_null_lot_id(tmp_path):
    """题面：「每只 bundle 恰好被一个组分认领。」"""
    result, _, output = run_task(tmp_path, sample())
    document = read(output)
    assert result.returncode == 0
    assert document["allocation"][2]["lot_id"] is None


def test_equal_cost_tie_uses_lexicographic_fraction_vector(tmp_path):
    """题面：「并列时先按 `components` 顺序比较占比序列。」"""
    result, _, output = run_task(tmp_path, tie_sample())
    document = read(output)
    fractions = [item["fraction"] for item in document["allocation"]]
    assert result.returncode == 0 and fractions == pytest.approx([0.0, 0.6, 0.4])


def test_near_equal_cost_uses_same_lexicographic_tiebreak(tmp_path):
    """题面：「旧结果必须保持不变。」"""
    payload = tie_sample()
    option(payload, 1)["cost_per_kg"] += 1e-12
    payload = rebuild_certification(payload, ["L-A-PRM", "L-B-PRM", "L-C-PRM"])
    result, _, output = run_task(tmp_path, payload)
    fractions = [item["fraction"] for item in read(output)["allocation"]]
    assert result.returncode == 0 and fractions == pytest.approx([0.0, 0.6, 0.4])


def test_tiebreak_repeat_is_stable(tmp_path):
    """题面：「对同一输入重跑，凭证字节必须相同。」"""
    result, source, output = run_task(tmp_path, tie_sample())
    first = output.read_bytes()
    second = invoke_candidate(tmp_path, ENTRY, source, output)
    assert result.returncode == 0 and second.returncode == 0 and output.read_bytes() == first


def test_infeasible_constraints_are_refused(tmp_path):
    """题面：「若约束组合根本排不出配方，终端报 `运行拒收: INFEASIBLE` 并返回 1。」"""
    payload = sample()
    payload["requirements"]["min_quality"] = 100.0
    result, _, output = run_task(tmp_path, payload)
    assert result.returncode == 1 and result.stderr == "运行拒收: INFEASIBLE\n" and not output.exists()


def test_result_key_order_and_compact_bytes(tmp_path):
    """题面：「下料凭证只保留一张紧凑 JSON 文本，文件收尾用单个换行。」"""
    result, _, output = run_task(tmp_path, sample())
    raw = output.read_bytes()
    document = json.loads(raw)
    assert result.returncode == 0
    assert list(document) == ["batch_id", "bundle_map", "status", "activation_cost", "total_cost", "allocation", "quality_index", "viscosity_cp", "additional_orders"]
    assert b": " not in raw and raw.endswith(b"\n") and raw.count(b"\n") == 1


def test_absent_parent_is_io_failure(tmp_path):
    """题面：「落盘或换名失败时，终端报 `运行拒收: IO_FAILURE` 并返回 1。」"""
    source = write(tmp_path / "blend.json", sample())
    output = tmp_path / "absent-parent" / "result.json"
    result = invoke_candidate(tmp_path, ENTRY, source, output)
    assert result.returncode == 1 and result.stderr == "运行拒收: IO_FAILURE\n" and not output.exists()


def test_bad_input_preserves_existing_result(tmp_path):
    """题面：「发布结束不得留下草稿。」"""
    result, source, output = run_task(tmp_path, sample())
    before = output.read_bytes()
    payload = sample()
    payload["requirements"]["batch_kg"] = True
    write(source, payload)
    rejected = invoke_candidate(tmp_path, ENTRY, source, output)
    assert result.returncode == 0 and rejected.returncode == 1
    assert output.read_bytes() == before and "traceback" not in rejected.stderr.lower()


def test_success_leaves_no_temporary_files(tmp_path):
    """题面：「发布先写同目录草稿再原子替换。」"""
    result, _, output = run_task(tmp_path, sample())
    leftovers = [path.name for path in tmp_path.iterdir() if path.name.startswith(".blend-")]
    assert result.returncode == 0 and output.exists() and leftovers == []


def test_repeat_run_is_byte_identical(tmp_path):
    """题面：「对同一输入重跑，凭证字节必须相同。」"""
    result, source, output = run_task(tmp_path, sample())
    first = output.read_bytes()
    second = invoke_candidate(tmp_path, ENTRY, source, output)
    assert result.returncode == 0 and second.returncode == 0 and output.read_bytes() == first


def test_two_file_arguments_are_accepted(tmp_path):
    """题面：「入口只接收输入与结果两个路径：」"""
    result, _, output = run_task(tmp_path, sample())
    assert result.returncode == 0 and output.is_file()
