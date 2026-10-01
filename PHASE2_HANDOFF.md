# Zepic — Phase 2 session handoff

整理日期：2026-10-01（Australia/Perth）。Phase 2 MVP 已在本 session 完成。
当前事实和后续建议分开记录；MVP 技术选型已经由用户确认。

## 1. 工作目录与协作规则

- **实际仓库：`/Users/zhenyao/zepic-data-platform`**。
- 不要在旧目录 `/Users/zhenyao/Documents/ChatGPT/zepic` 继续修改；它是早期副本。
- GitHub：`https://github.com/Ynohaze/zepic-data-platform.git`，remote 为 `origin`，分支 `main`。
- 用户授权 Zepic 直接修改、commit、pull；**push 仍需明确授权**。该授权不适用于 4403。
- 每组实质修改后展示文件列表、git diff，并解释改变的业务行为与验证结果。
- 用户希望理解实现，新增业务规则先说明；避免悄悄改变已确定的 schema 或扩成 ERP。
- `.env` 保存本机连接信息，已被忽略；不要打印、提交密码。`.venv` 同样忽略。
- Phase 2 改动目前尚未提交。新 session 先检查 `git status -sb`。

主要提交：

| Commit | 内容 |
|---|---|
| `9b6f3cb` | Phase 1.3 初始数据，已 push |
| `53bb9a0` | Phase 1.4 每日订单，本地 |
| `0c00967` | Phase 1.5 生产和发货，本地 |
| `76862f8` | 09:00 规划、16:00 发货及 pending 校验修正，本地 |
| `8c8820b` | Phase 1.6 多日模拟，本地 |

## 2. 已完成的 Phase 1

完整成功路径已实现并验证：初始化 → 每日创建订单 → 次日确认 → 缺口规划
→ 生产入库 → 整单发货 → 多日连续执行及失败续跑。

- Docker Compose 目前只有 PostgreSQL 16；数据库 `zepic_operational`。
- Python 脚本运行在 Mac 的 `.venv`，通过 psycopg 连接 `localhost:5432`。
- 数据在 named volume `zepic_zepic_postgres_data` 中，不在 Git 中。
- 若以后把 API 容器化，同一 Compose 网络内数据库 hostname 为 `postgres`。
- 不要执行 `docker compose down -v`，不要重置/重新初始化现有业务库。
- `database/verify.sql` 会对整表执行测试语句，仅适用于独立空测试库，不能用于当前业务库。

核心文件：

| 文件 | 职责 |
|---|---|
| `database/schema.sql` | 9 张业务表、约束、索引、触发器；schema 基线 |
| `config/initial_data.json` | seed、初始时间、客户等级数量、产品目录、机器和库存参数 |
| `config/daily_operations.json` | 每日订单参数及首次模拟日期 |
| `config/production.json` | 生产 buffer、seed、运行时长 |
| `scripts/generate_initial_data.py` | 仅空库初始化；默认预览，`--apply` 写入 |
| `scripts/generate_daily_operations.py` | 单日订单生成与确认 |
| `scripts/run_production_planning.py` | 单日生产、入库与整单发货 |
| `scripts/run_simulation.py` | 多日编排，每天一个外层事务，支持 `--resume` |
| `src/source_api.py` | FastAPI 只读源 API；orders、order_items、inventory |
| `scripts/extract_to_raw.py` | HTTP 分页抽取；raw batch 与逐表 watermark 原子提交 |
| `database/raw_schema.sql` | `zepic_raw` raw 记录、批次与 watermark 表 |
| `tests/` | 单元测试与独立临时 PostgreSQL 库集成测试 |
| `PROJECT_HANDOFF.md` | 详细历史决策/验收记录；较早章节不是当前数据库状态 |

尚未实现 dbt 或 Airflow。运行依赖为 psycopg[binary]、python-dotenv、FastAPI
和 Uvicorn；本机验证环境为 Python 3.14.6。

## 3. 当前数据检查点

最后验证：2026-10-01。Phase 2 baseline 覆盖 2026-01-02 至 2026-01-31；随后
模拟并增量抽取了 2026-02-01。下一个模拟日是 **2026-02-02**。新 session
应先只读确认，不要重复推进 2 月 1 日。

| 表/指标 | 数量 |
|---|---:|
| customers | 100（standard 70 / silver 20 / gold 10） |
| products | 20 |
| machines | 5，全部 available |
| inventory | 20 行，库存总件数 812 |
| orders | 3,234 |
| order_items | 9,615 |
| production_orders | 570，全部 completed |
| production_runs | 570，全部 completed |
| maintenance_records | 0 |
| shipped / pending / confirmed 订单 | 3,137 / 97 / 0 |

97 张 pending 是 2 月 1 日的新订单，不是缺货积压。订单金额、逐产品库存
守恒、非负库存、机器运行不重叠和发货时间均已由模拟器及测试核验。Phase 2
全套验证为 **43 项通过**，数据库测试使用自动清理的独立临时库。

## 4. 不要改变的业务与时间语义

- 新订单当天 09:00–17:00 创建，全部 pending；次日 08:00 才确认。
- 当天 09:00 只汇总 confirmed 需求，生产从 09:01 开始，16:00 整单发货。
- 脚本会提前写入整天订单。当天晚些时候的 pending 及其明细不参与需求、
  发货或生产订单时间校验，不能用它们阻止 09:00 规划。
- 缺口 = confirmed 需求 − 当前库存；只为正缺口创建生产单。
  默认加 5% buffer，再向上取整到产品 batch_multiple。
- 一张生产单一个 run；默认实际产量等于计划产量。每次 run 10 个模拟分钟，
  不建模真实产能。机器可先后执行多个 run，但同机不重叠，均在 16:00 前结束。
- 库存随产出增加、整单发货减少；没有 reservation、allocation 或 fulfilled_quantity。
- 不支持取消订单；尚未生成随机失败、损耗和维护事件。这些扩展不阻塞 Phase 2。
- 1 月 3 日保留早期已验证的 18:00 规划/18:32 发货记录；1 月 4 日起采用新时间。
  这是已知历史差异，不要为了统一时间而重写旧数据。

## 5. 对增量抽取尤其重要

1. 全部 9 张表有 `created_at`、`updated_at`（TIMESTAMPTZ）及 updated_at 索引。
   8 张表为 BIGINT identity 主键；inventory 的 product_id 是 PK/FK。
   **ID 不保证从 1 开始或连续**，测试/失败事务会消耗序号。
2. `set_updated_at()`：新旧 updated_at 相同时改为事务当前时间；显式指定不同
   模拟时间则保留。模拟脚本必须继续显式写业务时间，避免混入现实日期。
3. orders 状态更新不会自动修改 order_items.updated_at；库存是更新的当前状态，
   不是每日追加快照。每张表应独立考虑增量进度。
4. 同一批次大量记录共享 updated_at。只用时间作为分页游标可能漏掉同时间的行；
   建议按 `(updated_at, 主键)` 稳定排序和分页，并测试跨页相同时间边界。
5. 本项目业务时间是 **2026 年 1 月**，现实运行时间是 **2026 年 10 月**。
   **不要把现实 now() 直接写成模拟数据的成功 watermark**，否则后续模拟的
   2 月记录会被永久跳过。空批次也不能随意推进到现实时间。
6. 完整一天在一个事务内提交，外部只能看见提交后的行状态。计划/运行中间状态、
   同日 confirmed → shipped、库存先增后减不会全部保留为历史事件。
   这是 current-state 增量抽取，不是 CDC/event log；不要宣称能还原所有中间状态。
7. 已先对现有 30 天做 baseline，再推进 2 月 1 日并增量抽取；验证捕获了旧
   pending 订单和库存更新，以及新订单/明细插入。未来改变 baseline 次序时，
   仍不要在 baseline 前一次性推进 365 天。
8. 跨 HTTP 页的一致性需要明确策略：复合游标并不能单独解决并发修改。
   MVP 可先约定抽取期间暂停模拟写入，固定批次上界；若支持并发，另行设计
   快照/重叠窗口及幂等策略，不能仅以稳定排序声称无漏数。

## 6. Phase 2 已实现范围

用户确认了 FastAPI + 同库 PostgreSQL `zepic_raw` + 复合 watermark 方案。

- API 白名单只允许 `orders`、`order_items`、`inventory`。用 `(updated_at,
  主键)` 升序 keyset 分页，单页最大 1,000 行；批次先固定最大复合键，再按
  `<= upper` 取数。Decimal 输出十进制字符串，TIMESTAMPTZ 输出带 `Z` 的 UTC。
- 客户端以来源表为单位独立抽取。raw payload 保留完整行；批次 metadata、
  raw 行和 watermark 在同一事务中提交。失败整批回滚；重试使用有界 HTTP
  退避。空批次写入 0 行批次记录，watermark 不移动。空表标为 initialized，
  不用运行时钟虚构来源 watermark。
- MVP 要求抽取期间暂停模拟写入。复合游标和固定上界解决稳定排序/分页，
  但并发源写仍不在 MVP 内；不是 CDC，也不能重建事务内部的短暂状态。
- 首次 baseline 从 2026 年 1 月数据抽取；之后模拟 2 月 1 日并再次抽取。
  扩展其他 6 张表、并发快照与 Phase 3 数据质量属于后续工作。

已应用 `database/raw_schema.sql` 到 `zepic_operational`。Raw batch 1–3 为
baseline：orders 3,137、order_items 9,306、inventory 20 行。Batch 4–6 为
2 月 1 日增量：orders 216（97 INSERT、119 UPDATE）、order_items 309 INSERT、
inventory 20 UPDATE。Batch 7–9 是立即重跑所得空批次，均 0 行且 watermark
未变。逐表 watermarks 仍是源记录时间，不是现实抽取时间。

后续路线：Phase 3 数据质量/隔离/幂等并扩展全表；Phase 4 warehouse + dbt；Phase 5 Airflow
编排；Phase 6 portfolio 整理。不要现在加入 Kafka、云部署、Spark 或额外 ERP 表。

## 7. 新 session 可用命令

```sh
cd /Users/zhenyao/zepic-data-platform
git status -sb
git log -5 --oneline
docker compose ps
.venv/bin/python -m unittest discover -s tests -v
# 需要 Docker/PostgreSQL 启动；会创建并自动删除独立测试库：
ZEPIC_DB_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Docker 未运行时先启动 Docker Desktop，再 `docker compose up -d --wait`，不要删 volume。
库连接信息从 `.env` 读取；不要把实际密码写入新配置或 handoff。

运行本地 API 和增量抽取：

```sh
.venv/bin/uvicorn src.source_api:app --host 127.0.0.1 --port 8000
.venv/bin/python scripts/extract_to_raw.py --table all
```

抽取前暂停模拟器。不要重复应用 2026-02-01；下一模拟日为 2026-02-02。
