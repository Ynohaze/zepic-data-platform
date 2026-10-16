# Zepic — Phase 4 warehouse + dbt handoff

整理日期：2026-10-16（Australia/Perth）。Phase 4 已按 side-chat 中确认的新设计
重做，并在本机 PostgreSQL 16 的现有 Phase 3 数据检查点上完成构建与测试。

## 最终架构

- `warehouse/` 是独立 dbt 项目，使用 dbt Core 1.12 与 PostgreSQL adapter 1.11。
- `zepic_raw.records` 和 Phase 3 quality results 是 Bronze/Raw 输入；dbt 不读取
  operational 表，也不修改 raw rows、quality findings 或 extraction watermarks。
- `zepic_staging` 是实际落地的 Silver，共 10 张 table：九张 typed current-state
  表，加一张保留所有已观测有效订单版本的 `stg_order_versions`。
- `zepic_analytics` 是 Gold，共 8 张 table：三个当前维表，`fct_orders`、
  `fct_order_lines`、`fct_production_runs`、`fct_maintenance` 四个事实表，以及
  `current_inventory`。
- 具体边界、grain 和逻辑位置记录在 [warehouse/DESIGN.md](warehouse/DESIGN.md)。

## 已确认的设计决策

1. Silver 物化为 table，不再是 view。当前规模每次由 dbt 原子重建，不引入
   incremental merge 状态；raw 仍是可重放的历史真相。
2. current-state 先在所有“已完成质检”的 raw versions 中选最新版本，再判断该
   精确版本是否有效。最新版本被隔离时，该 key 不进入 Silver；绝不静默回退到
   旧的 valid 值。关系测试会把由此产生的孤儿数据显式报错。
3. 新增订单头粒度 `fct_orders`。订单金额、行数、件数只在订单粒度表达；
   `fct_order_lines` 继续保留成交价快照和 line amount。
4. `fct_orders` 使用宽表字段表达生命周期。`pending_at` 是确定的订单创建时间；
   `confirmed_at`/`shipped_at` 只来自实际抽取到的对应版本，并配有 observed flags。
5. 删除 `agg_daily_orders`。按下单日与“当前状态”聚合会重写历史分组，既不是
   事件事实也不是稳定快照，应该由消费者基于 `fct_orders` 明确选择日期语义。

## 生命周期数据限制

这仍然不是 CDC。发生并结束于两次 extraction 之间的状态无法重建；空的
`confirmed_at` 代表“没有观测到”，不是“业务上没有确认”。当前 raw 数据中：

- current pending orders：108，全部观测到 pending；
- current shipped orders：3,234，其中 216 曾被抽取为 pending；
- captured confirmed versions：0；
- current shipped orders 的 shipped version：3,234 全部已捕获。

因此 shipment lead time 可计算，但不能把当前数据用于可靠的
pending → confirmed → shipped 全漏斗分析。

## 验收结果

2026-10-16 执行：

```sh
.venv/bin/python scripts/run_warehouse.py parse
.venv/bin/python scripts/run_warehouse.py build
```

- dbt build：18 table models（10 Silver + 8 Gold）、93 data tests；
  `111/111 PASS`，0 warning、0 error。
- Silver 抽查：100 customers、20 products、5 machines、20 inventory rows、
  3,342 current orders、3,558 validated order versions、9,950 order lines、
  590 production orders、590 production runs、0 maintenance records。
- Gold 抽查：3,342 orders、9,950 order lines、590 production runs、20 inventory
  rows；逐订单 header-vs-lines 金额和生命周期时间顺序测试通过。

## Code 与可视化入口

- dbt 项目配置：`warehouse/dbt_project.yml`
- Silver SQL/YAML：`warehouse/models/staging/`
- Gold SQL/YAML：`warehouse/models/marts/`
- 最新有效性选择：`warehouse/macros/latest_validated_records.sql`
- 跨模型断言：`warehouse/tests/`
- 运行入口：`scripts/run_warehouse.py`

Phase 4 是 code-first，但 dbt 自带可交互 lineage/catalog 页面：

```sh
.venv/bin/python scripts/run_warehouse.py docs generate
.venv/bin/python scripts/run_warehouse.py docs serve
```

静态入口是 `warehouse/target/index.html`。Phase 5 再增加 Airflow DAG 与运行监控
面板，顺序保持 extraction → raw validation → order reconciliation → dbt build。
不要在 Phase 5 顺手引入 Kafka、Spark、云部署或额外 ERP 表。
