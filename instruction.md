# 溶剂配料的最低成本核算

喷涂线明早开车。工艺组送来配料单，请把 `/app/blend_simplex_optimize.py` 修成能直接下料的最低成本配方核算程序。

## 配料单

配料单源文件按 UTF-8 解码，读入一个 JSON 对象。

顶层只有 `batch_id`、`components`、`requirements`、`incompatible_lot_pairs` 四项。

`batch_id` 保存批次标签；字符数在 1 到 64 之间，不能有首尾空白或控制字符。组分表由 3 到 16 条记录组成。每条记录只放下面九个键：

```text
id
min_fraction
max_fraction
supplier
cost_per_kg
quality_index
viscosity_cp
activation_fee
alternate
```

`id` 与 `supplier` 都匹配 `[A-Z][A-Z0-9_-]{1,15}`；`id` 全批唯一。两个占比都是 0 到 1 的有限数，且 `min_fraction` 不大于 `max_fraction`；`cost_per_kg` 位于 0 到 1000，`quality_index` 位于 -100 到 100，`viscosity_cp` 位于 0.1 到 1000000，`activation_fee` 位于 0 到 100000。布尔值不算数字。

`alternate` 是该组分的备用供货批，键集合恰为：

```text
lot_id
supplier
cost_per_kg
quality_index
viscosity_cp
activation_fee
```

`lot_id` 和 `supplier` 使用同一标识语法。`lot_id` 全批唯一，且不得为 `primary`。备用批沿用同名字段的数值口径。已启用组分必须从主供货批和备用批中二选一，不能两批混合。

`requirements` 的键集合恰为 `min_quality`、`max_viscosity`、`batch_kg`、`max_active`、`max_active_per_supplier`。

`incompatible_lot_pairs` 是最多 48 项的数组。每项是两个备用批 `lot_id` 组成的二元素数组，两个编号必须按字典序升序、指向不同备用批，且整组不得重复；同一条中的两批不得同时出现在最终配方里。

`min_quality` 是 -100 到 100 的有限数。`max_viscosity` 是大于 0 的有限数。`batch_kg` 是 1 到 1000000 的有限数。`max_active` 是 2 到 4 的整数。`max_active_per_supplier` 是 1 到 4 的整数。所有组分的占比之和必须覆盖 1：最小占比之和不超过 1，最大占比之和不小于 1。

程序调用时只接收输入和结果两个路径：

```bash
python3 /app/blend_simplex_optimize.py <配料单> <核算结果>
```

从启动到凭证发布须在 20 秒内结束。

## 配方要求

设组分占比为 `x_i`，要同时满足下限、上限及占比总和为 1。占比大于零的组分视为已开桶，数量不得超过 `max_active`。每个已开桶组分选择主批或备用批之一；所选批的 `supplier` 计数不得超过 `max_active_per_supplier`。每只已开桶组分按所选批再加一笔 `activation_fee`。成品质量指数和黏度都按所选批的同名系数加权求和，质量不得低于 `min_quality`，黏度不得超过 `max_viscosity`。

`activation_cost` 是所有已开桶组分所选批的 `activation_fee` 之和。总成本为
`batch_kg * Σ 所选批 cost_per_kg_i * x_i + activation_cost`。固定费用必须计入全局成本。供货批选择和供应商启用上限也必须与开桶集合共同决定最低成本。单靠每千克单价排序，或单靠一个连续线性规划，都不足以覆盖这些选择。

只存在一个配比能让总成本最低时直接输出它。

若若干顶点的成本相差不超过 1e-10，把它们视为同一个最低成本。

处理并列解时，再按 `components` 输入顺序比较占比序列。

并列解按 `components` 顺序做字典序比较。不可行时不能编造配方。

若占比向量仍相同，再按同一组分顺序比较 `lot_id`，主批字符串 `primary` 也参加比较。

## 下料凭证

配料结果压成紧凑的一行 JSON，末尾保留一个换行。

```text
batch_id
status
activation_cost
total_cost
allocation
quality_index
viscosity_cp
```

`status` 写 `optimal`。`allocation` 按输入组分顺序排列，每项恰好有 `id`、`lot_id`、`fraction`、`mass_kg` 四个键；未启用组分的 `lot_id` 写 `null`，启用组分写所选备用批的实际 `lot_id`，选主批时写 `primary`。`fraction` 是本次占比，`mass_kg` 等于占比乘 `batch_kg`。数值均按双精度写入有限 JSON 数字。计算成功时，终端不接收任何标准输出或错误字节。

用原配料单重跑时，凭证应保持原有字节。

发布时先写同目录临时文件，再用替换写出目标。

任何写入或替换失败都要保留旧结果。

发布结束不能留下临时文件。

## 退回情形

退回原因是输入结构、键、类型、范围或总占比不合规；此时进程返回 1，标准错误内容为 `运行拒收: BAD_INPUT`。

若质量、黏度与占比条件互相冲突，本次不生成凭证。命令退出码为 1。标准错误仅写 `运行拒收: INFEASIBLE`。

替换凭证时若磁盘操作失败，先前的下料单仍须原样可用。

这种磁盘故障以退出码 1 结束，标准错误只报告 `运行拒收: IO_FAILURE`。

输入被退回时终端不显示 `traceback`，先前导出的配料凭证也一个字节都不改。
