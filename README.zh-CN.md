# Zepic 数据平台

[English](README.md) | [简体中文](README.zh-CN.md)

Zepic 是一个可在笔记本电脑上运行的端到端数据与 AI 工程作品集项目，模拟制造与
订单履约业务。它将 PostgreSQL 业务数据经过可恢复的增量抽取、版本化质量门禁、
dbt Silver/Gold 建模和 Airflow 编排，最终提供受控的 LLM 分析能力。

项目重点不是展示分布式规模，而是展示可信的数据流、清晰的事务边界、可恢复性、
明确的数据语义以及 AI 使用数据时所需的权限和上下文约束。PostgreSQL 16、Python、
FastAPI、dbt Core 和 Airflow 3 均运行在同一个本地 Docker Compose 项目中。

## 架构

```text
业务模拟器
    |
    v
PostgreSQL operational 表（9 张）
    |
    | 白名单 keyset API：(updated_at, primary key)
    v
FastAPI source service
    |
    | 独立增量抽取 + 事务性 watermark
    v
只追加的 Raw 版本、批次与质量结果
    |
    | 行级校验 + 订单头/明细金额对账
    v
dbt Silver（10 张表） -> dbt Gold（8 张表）
    |                         |
    |                         +-> 固定 SQL Dashboard
    |                         +-> Level 1 单次问答
    |                         +-> 有界分析 Agent beta
    v
Airflow：9 路并行抽取 -> 质量门禁 -> warehouse build
```

LLM 不会获得数据库凭据，也不能直接调用数据库。模型提出的每一条 SQL 都必须经过
解析和白名单检查，只能访问 Gold；随后由只读 PostgreSQL 角色在超时和行数限制下
执行，并把 SQL、证据与真实运行事件一并展示。

## 项目展示的能力

- 确定性且按日原子提交的订单、生产、库存与履约模拟。
- 九张源表的复合游标增量抽取、固定批次上界和独立 watermark。
- Raw 行、批次元数据与 watermark 在同一事务中提交，可安全重试。
- 版本化隔离规则与精确的订单头/明细金额对账门禁。
- dbt 构建并测试的 10 张实体 Silver 表和 8 张分析 Gold 表。
- Airflow 并行抽取、有限并发、下游阻断可视化与 task 级恢复。
- 轻量 Dashboard、Level 1 单查询问答，以及最多执行三条受控查询并显式展示事件
  轨迹的 bounded analytical agent。

## 已验收检查点

| 证据 | 验收结果 |
| --- | ---: |
| Operational 订单 / 明细 | 3,468 / 10,334 |
| 完成的生产单 / 生产运行 | 610 / 610 |
| 已发货 / 待处理订单 | 3,342 / 126 |
| Raw 行 / 抽取批次 | 15,551 / 72 |
| 最新订单对账 | 3,468 匹配，0 异常 |
| Silver / Gold 表 | 10 / 8 |
| 含 PostgreSQL 的完整 Python 测试 | 110/110 通过 |
| dbt 模型与数据测试 | 18 个模型、93 项测试，111/111 通过 |
| Airflow 收尾运行 | 1 个 DAG、14 个 task，14/14 成功 |

本地保留的数据库是作品集验收检查点，不是代码运行所依赖的 fixture。全新环境可以
通过已提交配置生成自己的确定性数据。

## 快速启动

需要 Docker Compose 和 Python 3.10 或更高版本。

```sh
cp .env.example .env
# 配置本地数据库凭据；如需 AI 功能，再配置 GROQ_API_KEY。
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

docker compose build source-api airflow-scheduler
docker compose up --no-build --abort-on-container-exit \
  --exit-code-from airflow-init airflow-init
docker compose up -d --no-build \
  source-api airflow-apiserver airflow-scheduler airflow-dag-processor
```

Airflow 地址为 <http://127.0.0.1:8080>。启用并手动触发 `zepic_raw_to_gold`；
该 DAG 没有定时计划或 catchup，并且只允许一个 active run。本地默认账号密码为
`airflow` / `airflow`，共享环境中应在 `.env` 中修改。

不通过 Airflow 直接运行数据链路：

```sh
docker compose up -d --wait postgres source-api
.venv/bin/python scripts/etl/extract_to_raw.py --table all
.venv/bin/python scripts/etl/validate_raw.py --fail-on-findings
.venv/bin/python scripts/etl/reconcile_order_totals.py --fail-on-findings
.venv/bin/python scripts/etl/run_warehouse.py build
```

Gold 构建完成后启动受控分析应用：

```sh
.venv/bin/python scripts/ai/setup_analytics_role.py
.venv/bin/uvicorn src.analytics_api:app --host 127.0.0.1 --port 8001
```

打开 <http://127.0.0.1:8001/dashboard/>。固定 Dashboard 指标不依赖 LLM；
`Ask data` 是 Level 1 单查询流程，`Analysis task` 是有界 Agent beta。

## 必须理解的数据语义

- 增量抽取捕获的是 current-state 变化，并非 CDC。两次抽取之间完整发生并结束的状态
  变化无法重建。
- 每个抽取批次会先固定上界游标，但 MVP 不支持模拟器与抽取并发写入；抽取时应暂停
  模拟写入。
- Silver 先选择最新的已质检 Raw 版本，再判断该精确版本是否有效；绝不会静默回退到
  较旧的有效版本。
- `confirmed_at` 与 `shipped_at` 只表示实际观测到的版本。空值表示“未观测到”，
  不是“从未发生”。
- `current_inventory` 是当前余额，不是库存历史快照。
- Agent 只提供分析协助，不能写数据、触发 Airflow、保留会话记忆或在后台运行。

## 验证

```sh
# 快速测试
.venv/bin/python -m unittest discover -s tests -v

# 包含临时隔离 PostgreSQL 数据库的完整测试
ZEPIC_DB_TESTS=1 .venv/bin/python -m unittest discover -s tests -v

# Warehouse contract
.venv/bin/python scripts/etl/run_warehouse.py parse
.venv/bin/python scripts/etl/run_warehouse.py build

# 可选：真实 Agent 验收，会消耗 provider quota
.venv/bin/python scripts/ai/run_agent_acceptance.py
```

数据库测试会创建唯一命名的临时数据库并在结束后删除。`database/verify.sql` 是面向
破坏性边界的 smoke test，只能对可丢弃 schema 使用。

## 仓库结构

| 路径 | 职责 |
| --- | --- |
| `config/` | 确定性业务模拟配置 |
| `database/` | Operational、Raw 和质量层 PostgreSQL DDL |
| `src/source_api.py` | 白名单 operational source API |
| `src/analytics_api.py` | Dashboard 与受控分析 API |
| `scripts/etl/` | 模拟、抽取、质量和 warehouse CLI |
| `scripts/ai/` | 只读分析角色配置与真实 Agent 验收 |
| `warehouse/` | dbt Silver/Gold 模型、macro 与测试 |
| `orchestration/` | Airflow DAG、task contract 与镜像 |
| `analytics/` | 语义契约、模型 provider 边界与 SQL guard |
| `dashboard/` | 轻量分析 UI |
| `tests/` | 单元测试与隔离 PostgreSQL 集成测试 |

详细阶段文档：

- [ETL 阶段：设计、运行与验收证据](ETL_README.md)
- [AI 阶段：边界、配置与评估](AI_README.md)

## 范围边界

这是本地作品集 runtime，不是生产部署。项目有意不包含 TLS、身份认证、secret
manager、scheduler 高可用、远程日志、告警、CDC、分布式计算或自主后台 Agent。
这些都是明确边界，不应被描述为已经实现的能力。
