# 溶剂配料的最低成本核算

喷涂线明早开车。工艺组送来的配方单只有组分比例。供货批按来料验收批次混放在一起，标签没有写回组分。请修复 `/app/blend_simplex_optimize.py`，先从试配记录恢复“组分—供货批组”的对应关系，再给出可直接下料的配方。

## 配料单

配料单先按 UTF-8 字节流读取；若 JSON 顶层出现下列键名之外的内容，直接视为坏单：

```text
batch_id components supply_lots lot_bundles requirements certification_runs incompatible_lot_pairs
```

工单号写入 `batch_id`；可见字符数须为 1–64，两端不留空白，内部控制码不收。配方由 3–12 个组分组成，每条记录仅接受：

```text
id min_fraction max_fraction
```

`id` 匹配 `[A-Z][A-Z0-9_-]{1,15}` 且全批唯一。两个占比都是 0 到 1 的有限数，前项不大于后项。布尔值不算数字。

`supply_lots` 恰有组分数的两倍那么多条。每条供货批只含：

```text
lot_id supplier cost_per_kg quality_index viscosity_cp activation_fee available_kg
```

`lot_id`、`supplier` 同样匹配上述大写标识语法；`lot_id` 全批唯一。`cost_per_kg` 在 0 到 1000，`quality_index` 在 -100 到 100，`viscosity_cp` 在 0.1 到 1000000，`activation_fee` 在 0 到 100000。`available_kg` 是该批在整个计划期内可用的总质量，范围为 1 到 1000000000。

每只组分配一个 bundle，数量必须与配方组分数一致。bundle 记录只保留 `bundle_id` 与 `lots`，不接受补充说明。`bundle_id` 使用大写标识语法且唯一。`lots` 是两个 `lot_id` 按字典序组成的二元素数组。每只供货批必须且只能出现在一个 bundle 中。bundle 与组分之间的对应关系故意没有给出，不能按数组顺序直接配对。

试配记录数与配方组分数相同。每一试配记录只含：

```text
fractions cost_per_kg quality_index viscosity_cp
```

`fractions` 是与 `components` 同长的非负有限数数组，和为 1（绝对误差不超过 `1e-9`）。三个观测值分别是该次试配按所选供货批加权得到的每千克成本、质量指数和黏度。所有试配使用的历史供货批选择没有记录。试配系数矩阵必须满秩。三个性质向量必须由这些观测唯一恢复。恢复出的三元组必须分别落到唯一一个供货批。每只 bundle 恰好被一个组分认领。无法唯一认领时模型不成立。

`requirements` 的键集合恰为：

```text
min_quality max_viscosity batch_kg max_active max_active_per_supplier additional_orders
```

各项取值如下：

- `min_quality`：-100 至 100。
- `max_viscosity`：正有限数。
- `batch_kg`：1 至 1000000。
- `max_active`：整数 2 至 3。
- `max_active_per_supplier`：整数 1 至 4。

`additional_orders` 是 0 至 2 条追加生产任务。每条成员集合如下：

```text
order_id batch_kg min_quality max_viscosity
```

`order_id` 使用大写标识语法。`order_id` 不得等于 `PRIMARY`。其余字段沿用上面的值域。针对同一套配方单和供货批准备的若干次日生产任务，固定激活费按整份生产计划只收一次。整份计划的组分并集上限为 `max_active`。

各订单共享供货批库存。单只 lot 的累计耗用上限由其 `available_kg` 给出。

占比域还须满足：

```text
sum(min_fraction) <= 1
sum(max_fraction) >= 1
```

`incompatible_lot_pairs` 是最多 48 项的数组。每项为两个供货批编号按字典序组成的二元素数组。二者不得相同，整组不得重复。同一项中的两批不能同时出现在最终配方里。

入口只接收输入与结果两个路径：

```bash
python3 /app/blend_simplex_optimize.py <配料单> <核算结果>
```

从启动到凭证发布须在 20 秒内结束。

## 配方计算

设组分占比为 `x_i`，所有占比非负、满足各自上下限且总和为 1。占比大于零的组分视为已开桶。开桶上限是 `max_active`。每个已开桶组分从它恢复出的 bundle 的两只供货批中二选一。不同组分不能共用同一只供货批。每个供应商最多启用 `max_active_per_supplier` 个组分。每只已开桶组分按所选批再加一笔 `activation_fee`。

质量指数按所选批加权求和，质量不得低于 `min_quality`。黏度按所选批加权求和。黏度上限为 `max_viscosity`。总成本为：

```text
sum_over_orders(batch_kg * sum(cost_per_kg_i * x_i)) + activation_cost
```

`activation_cost` 汇总整张计划真正启用的不同 `lot_id` 所对应的 `activation_fee`。批选择与占比必须一起决定全局最低成本。按单价排序或单独解连续线性规划都不足以得到它。

若若干顶点的成本相差不超过 `1e-10`，它们视为同一最低成本。并列时先按 `components` 顺序比较占比序列。占比仍相同时，按同一顺序比较所选 `lot_id`。未启用组分的该槽为空字符串。选批的槽为真实 `lot_id`。比较结果为唯一规范解。

## 下料凭证

下料凭证只保留一张紧凑 JSON 文本，文件收尾用单个换行。顶层键依次为：

```text
batch_id bundle_map status activation_cost total_cost allocation quality_index viscosity_cp additional_orders
```

`bundle_map` 的排列次序跟随配方单中的组分次序。`bundle_map` 每项恰好含 `id` 和 `bundle_id`。`status` 写 `optimal`。

下料明细沿用配方单的组分次序。`allocation` 的每项成员集合如下：

```text
id lot_id fraction mass_kg
```

未启用组分的 `lot_id` 为 `null`。启用组分写最终选择的真实 `lot_id`。`mass_kg` 等于占比乘 `batch_kg`。

`additional_orders` 按输入的追加任务顺序展开。每项的成员集合如下：

```text
order_id allocation quality_index viscosity_cp
```

`allocation` 的结构与首单相同，`mass_kg` 使用该任务自己的 `batch_kg`。

计算成功时标准输出和标准错误都为空。对同一输入重跑，凭证字节必须相同。发布先写同目录草稿再原子替换。发布失败时旧凭证和目录中已有内容不变。发布结束不得留下草稿。

## 退回

读入值不合约定时，终端只回 `运行拒收: BAD_INPUT`（退出码 1）。

试配矩阵奇异、性质三元组无法唯一匹配供货批，或 bundle 认领不唯一时，标准错误只写 `运行拒收: BAD_MODEL`，退出码为 1。

若约束组合根本排不出配方，终端报 `运行拒收: INFEASIBLE` 并返回 1。

落盘或换名失败时，终端报 `运行拒收: IO_FAILURE` 并返回 1。旧结果必须保持不变。拒绝请求时不要显示 traceback 文本。
