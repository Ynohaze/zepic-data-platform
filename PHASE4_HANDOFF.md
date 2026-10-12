# Zepic — Phase 4 warehouse + dbt handoff

整理日期：2026-10-12（Australia/Perth）。Phase 4 MVP 已实现并在本机
PostgreSQL 16 的 Phase 3 数据检查点上完成构建与测试。

## 已实现范围

- `warehouse/` 是独立 dbt 项目，使用 dbt Core 1.12 与 PostgreSQL adapter 1.11。
- 唯一 source 是 `zepic_raw.validated_records`；模型不会直接读取 operational 表，
  也不会修改 raw rows、quality findings 或 extraction watermarks。
- `zepic_staging` 有九个 view，对应九张源表。每个源主键按
  `(source_updated_at DESC, batch_id DESC)` 选择最新的已验证版本，并把 JSONB
  payload 显式转换为业务类型。
- `zepic_analytics` 有八张 table：三个当前维表、order-line / production-run /
  maintenance 三个事实表、current inventory 和 daily order aggregate。
- `scripts/run_warehouse.py` 从被忽略的根目录 `.env` 加载数据库连接，并将其余
  参数传给 dbt；仓库中的 `profiles.yml` 不保存密码。

## 关键语义

- 这是 latest-observed-current-state warehouse，不是 CDC warehouse。
  未被抽取的事务内状态变化不能重建，也没有 SCD2 历史。
- staging 选择“最新的有效版本”。如果某实体的最新 raw 版本被隔离，旧的有效
  版本仍可能成为 warehouse 当前版本；这是本期明确的 availability 取舍。
- `fct_order_lines` 粒度是一行一个 order item，金额使用订单创建时快照的
  `unit_price`，不是产品当前目录价。
- `current_inventory` 是当前余额及按当前目录价计算的零售估值，不是周期快照。
- `agg_daily_orders` 按下单日期和订单当前状态聚合。订单后续 shipped 时，历史
  下单日期对应的状态分组会改变；不要将其解释为按发货日的收入事件。
- source 使用模拟器的 UTC 时间；模型中的 `order_date` 当前按 PostgreSQL session
  时区从 timestamptz 转 date。本地连接默认 UTC，与现有模拟语义一致。

## 验收结果

2026-10-12 执行：

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/run_warehouse.py parse
.venv/bin/python scripts/run_warehouse.py build
```

- 原有 Python suite：53 tests 通过，其中 25 个 PostgreSQL integration tests 因
  未设置 `ZEPIC_DB_TESTS=1` 而按设计跳过。
- dbt build：17 models（9 views + 8 tables）、72 data tests，合计 89/89 PASS，
  0 warning、0 error。
- analytics 抽查：100 customers、20 products、5 machines、3,342 orders、
  9,950 order lines、590 production runs、20 inventory rows / 700 units、
  0 maintenance rows、32 daily date/status groups。
- order-line 金额总计 299,590.20；dbt 的逐订单 header-vs-lines 精确金额断言通过。

## 运行方式与下一步

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/validate_raw.py
.venv/bin/python scripts/reconcile_order_totals.py
.venv/bin/python scripts/run_warehouse.py build
```

生成目录 `warehouse/target`、`warehouse/logs` 和 `warehouse/dbt_packages` 已忽略。
Docker PostgreSQL volume 保留，不要执行 `docker compose down -v`。

下一阶段是 Phase 5 Airflow 编排。建议 DAG 顺序为 extraction → raw validation →
order reconciliation → dbt build，并继续遵守“抽取期间暂停 simulator 写入”的
Phase 2 一致性边界。不要在 Phase 5 顺手引入 Kafka、Spark、云部署或额外 ERP 表。
