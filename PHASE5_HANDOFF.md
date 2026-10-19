# Zepic — Phase 5 Airflow orchestration handoff

整理日期：2026-10-17（Australia/Perth）。Phase 5 已在现有 PostgreSQL 数据检查点
上完成本地 Airflow 编排、UI 验收和故障恢复演练。最终成功的验收 Run 为
`manual__2026-10-17T10:36:01.774340+00:00`，DAG 已在验收后重新暂停。

## 最终架构

Phase 5 在既有命令行入口上增加一层薄编排，不复制 extraction、quality 或 dbt
业务逻辑。一个 Compose project 包含以下应用容器，并通过显式
`zepic-network` bridge network 互通：

| Service | 职责 | 本机入口 |
| --- | --- | --- |
| `postgres` | operational、Raw、Silver/Gold 与 Airflow metadata | `127.0.0.1:5432` |
| `source-api` | 向 extraction 暴露九张 operational 表 | `127.0.0.1:8000` |
| `airflow-apiserver` | Airflow UI 与 task execution API | `127.0.0.1:8080` |
| `airflow-scheduler` | LocalExecutor 调度与最多四个并行 task instances | Compose network only |
| `airflow-dag-processor` | DAG 解析与序列化 | Compose network only |
| `airflow-init` / `airflow-db-init` | 一次性 metadata database 初始化与迁移 | 无长期进程 |

PostgreSQL 继续复用同一个 container 和持久化 volume，但业务数据使用
`zepic_operational` database，Airflow metadata 使用独立的 `airflow` database。
项目 checkout 在 Airflow container 内只读；dbt 日志和编译产物写入
`/tmp/zepic-dbt`，最终模型仍持久化到 PostgreSQL。

## DAG contract

唯一 DAG 为 `zepic_raw_to_gold`，共 14 个 tasks：

```text
start
  |
  +-- extract.customers -----------+
  +-- extract.products ------------+
  +-- extract.machines ------------+
  +-- extract.inventory -----------+
  +-- extract.orders --------------+
  +-- extract.order_items ---------+--> validate_raw
  +-- extract.production_orders ---+          |
  +-- extract.production_runs -----+          v
  +-- extract.maintenance_records -+  reconcile_order_totals
                                               |
                                               v
                                        build_warehouse
                                               |
                                               v
                                            complete
```

- `schedule=None`：只允许人工触发；没有 catchup。
- `max_active_runs=1`：避免两个完整 pipeline 同时推进相同 watermarks。
- extraction 每张表独立执行，失败后等待 30 秒，最多重试两次。
- 全局 parallelism 为 4，九张表保持并行分支但不会一次启动九个 worker。
- validation、reconciliation 与 dbt build 默认不自动重试，使数据问题在 UI
  中保持可见并由操作者决定恢复范围。

## 质量门禁与幂等边界

1. `validate_raw.py --fail-on-findings` 检查每个 source key 的最新已质检版本。
   旧 quarantine 不会因为一次空 extraction 而被绕过；更新后的有效版本可以解除
   当前 gate。
2. `reconcile_order_totals.py --fail-on-findings` 在 order header 与当前有效
   order items 金额不一致、缺少明细或因 quarantine 被跳过时退出非零。
3. 只有两个 gate 都通过后才执行 dbt Silver/Gold build。
4. extraction 将 raw rows 与 watermark 放在同一 transaction；重复拉取不会重复
   推进数据。quality run、reconciliation snapshot 与 dbt table rebuild 也都可安全重跑。

## 故障与恢复验收

### Source API 故障

验收时停止 `source-api` 后触发 DAG，九个 extraction tasks 先显示
`up_for_retry`，三次尝试耗尽后变为 failed，所有下游保持阻断。重新启动 API 并
清除该 Run 后，extraction、两个 quality gates 和 warehouse build 均成功。

这次演练同时发现 Airflow UID 无权在只读 checkout 中创建 `warehouse/logs/dbt.log`。
修复后，container 内 dbt runtime artifacts 改写到 `/tmp/zepic-dbt`；只清除
`build_warehouse` 与 `complete` 即完成同一个 Run 的恢复。

### Reconciliation gate 故障

为了不污染 operational、Raw 或 Gold 数据，DAG 支持 scheduler container 内的一次性
sentinel：`/tmp/zepic-simulate-reconciliation-failure`。真实 reconciliation 先执行，
sentinel 随后让 task 输出明确的 simulated order-total mismatch 并退出 1。

最终验收 Run 中：

- extraction 与 `validate_raw` 成功；
- 真实 reconciliation 结果为 3,342 input、3,342 matched、0 mismatched、
  0 missing-item、0 skipped；
- `reconcile_order_totals` 按预期变红；
- `build_warehouse` 与 `complete` 变为 `upstream_failed`；
- 删除 sentinel 并清除 reconciliation 后，UI 只选择这三个 task instances；
- 同一个 Run 最终 14/14 tasks success，随后重新暂停 DAG。

重复该演练的具体命令见 [orchestration/README.md](orchestration/README.md)。

## 验收结果

- Airflow 3.3.2 成功解析一个 DAG、14 个 tasks、0 import errors。
- `airflow-apiserver`、scheduler、DAG processor、source API 与 PostgreSQL health
  checks 均通过。
- UI 已验证 queued、running、up-for-retry、failed、upstream-failed 与 success
  状态，以及 task logs 和定向 clear/re-run。
- 恢复后数据检查点未重复：3,342 operational orders、14,873 raw records、
  3,342 Gold `fct_orders`。
- 使用 `ZEPIC_DB_TESTS=1` 的完整 suite 共 59 tests，全部通过；其中 26 个
  database tests 使用临时隔离 PostgreSQL databases，不修改业务检查点。
- Phase 5 收尾时工作树干净，故障 sentinel 已删除，DAG 已暂停。

## 启动与停止

构建 image、初始化 Airflow metadata database，再启动长期服务：

```sh
docker compose build source-api airflow-scheduler
docker compose up --no-build --abort-on-container-exit \
  --exit-code-from airflow-init airflow-init
docker compose up -d --no-build \
  source-api airflow-apiserver airflow-scheduler airflow-dag-processor
```

打开 <http://127.0.0.1:8080>。本地默认账号是 `airflow` / `airflow`，共享环境应在
`.env` 中覆盖。停止服务但保留全部数据库数据：

```sh
docker compose stop \
  source-api airflow-apiserver airflow-scheduler airflow-dag-processor
```

不要运行 `docker compose down --volumes`；`zepic_postgres_data` 保存整个项目的
operational 与 warehouse 检查点。

## Code 入口

- Compose runtime：`docker-compose.yml`
- DAG：`orchestration/dags/zepic_pipeline.py`
- 稳定 task commands：`orchestration/pipeline_contract.py`
- 架构与验收设计：`orchestration/DESIGN.md`
- 本地运行手册：`orchestration/README.md`
- Airflow / source API images：`orchestration/Dockerfile.airflow`、
  `orchestration/Dockerfile.source-api`
- 严格 quality CLI：`scripts/etl/validate_raw.py`、
  `scripts/etl/reconcile_order_totals.py`
- orchestration contract tests：`tests/test_orchestration.py`

## 明确边界与下一步

这是单机 portfolio runtime，不是 production deployment：没有 TLS、secret manager、
remote log storage、HA scheduler、Celery/Kubernetes executor、alerting integration 或
定时生产 schedule。source API 只绑定 localhost；container 间流量走 Compose network。

下一步是项目总收尾：以 Phase 1–5 handoff 和各层 DESIGN/README 为事实来源，统一根
README 的项目故事、架构图、运行路径、数据质量证据和 portfolio 展示顺序。不要在总
文档阶段继续扩展 Kafka、Spark、云部署或新的业务表，也不要推进现有 simulation
checkpoint；下一个可模拟日期仍是 2026-02-03。
