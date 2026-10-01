# Zepic — Phase 2 session handoff

整理日期：2026-10-01（Australia/Perth）。这是新 session 的首读文件。
当前目标是从已完成的业务模拟进入「源数据 API + watermark 增量抽取」。
本文件区分已实现事实与下一阶段建议；不要把建议当成已批准的技术选型。

## 1. 工作目录与协作规则

- **实际仓库：`/Users/zhenyao/zepic-data-platform`**。
- 不要在旧目录 `/Users/zhenyao/Documents/ChatGPT/zepic` 继续修改；它是早期副本。
- GitHub：`https://github.com/Ynohaze/zepic-data-platform.git`，remote 为 `origin`，分支 `main`。
- 用户授权 Zepic 直接修改、commit、pull；**push 仍需明确授权**。该授权不适用于 4403。
- 每组实质修改后展示文件列表、git diff，并解释改变的业务行为与验证结果。
- 用户希望理解实现，新增业务规则先说明；避免悄悄改变已确定的 schema 或扩成 ERP。
- `.env` 保存本机连接信息，已被忽略；不要打印、提交密码。`.venv` 同样忽略。
- 本文件创建前工作区干净，本地比已知 `origin/main` 领先 4 个 commit。
  本 handoff 提交后还会新增一个文档 commit；新 session 先检查 `git status -sb`。

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
| `tests/` | 单元测试与独立临时 PostgreSQL 库集成测试 |
| `PROJECT_HANDOFF.md` | 详细历史决策/验收记录；较早章节不是当前数据库状态 |

当前没有 API 服务、抽取客户端、raw 层、watermark 存储、dbt 或 Airflow。
运行依赖为 psycopg[binary]、python-dotenv；本机验证环境为 Python 3.14.6。

## 3. 当前数据检查点

最后验证：2026-10-01，模拟已完成 **2026-01-02 至 2026-01-31，共 30 天**。
下一个模拟日是 **2026-02-01**。以下是最近核验值，新 session 应先只读确认。

| 表/指标 | 数量 |
|---|---:|
| customers | 100（standard 70 / silver 20 / gold 10） |
| products | 20 |
| machines | 5，全部 available |
| inventory | 20 行，库存总件数 659 |
| orders | 3,137 |
| order_items | 9,306 |
| production_orders | 550，全部 completed |
| production_runs | 550，全部 completed |
| maintenance_records | 0 |
| shipped / pending / confirmed 订单 | 3,018 / 119 / 0 |

119 张 pending 是 1 月 31 日的新订单，不是缺货积压。
订单金额、逐产品库存守恒、非负库存、机器运行不重叠和发货时间已核验。
最新完整测试共 **32 项通过**，数据库测试使用自动清理的独立临时库。

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
7. 首先对现有 30 天做 baseline 抽取并验证，再推进 2 月 1 日、再次抽取，才能
   展示旧 pending 订单更新、旧库存更新、新订单/明细/生产记录插入。
   **不要在建立 baseline 之前再一次性推进 365 天。**
8. 跨 HTTP 页的一致性需要明确策略：复合游标并不能单独解决并发修改。
   MVP 可先约定抽取期间暂停模拟写入，固定批次上界；若支持并发，另行设计
   快照/重叠窗口及幂等策略，不能仅以稳定排序声称无漏数。

## 6. Phase 2 建议起步范围（尚待讨论/确认）

已确定方向是「通过源 API 抽取到 raw，并维护可恢复的增量进度」。具体 API
框架、接口、分页契约、raw 目的地和 watermark 存储尚未实现/锁定。

建议先设计一个小闭环，再扩展全部表：

1. 讨论 API/抽取契约：表白名单、时间过滤、稳定分页、批次上界、金额序列化。
   金额保持 Decimal 精度；时间统一输出带时区的 UTC 字符串。
2. 实现只读源 API；建议先覆盖 orders、order_items、inventory，分别验证
   「新增+更新」「以新增为主」「持续更新」三种访问模式。FastAPI 只是候选选型。
3. 客户端通过 HTTP 分页读取并落 raw；保留来源表、原始主键和抽取批次信息。
   原始业务数据与抽取元数据不要混成同一业务字段。
4. 全批次成功持久化后再提交 watermark；失败、重试、同时间分页、空批次都应测试。
   最低限度的安全重试不可省略；完整清洗/隔离/幂等层主要属于后续 Phase 3。
5. baseline → 单日模拟 → 第二次增量 → 对账，证明同时捕获 INSERT 和 UPDATE。
   再扩展到剩余业务表；maintenance 当前为空，要验证空表行为。

后续路线：Phase 3 数据质量/隔离/幂等；Phase 4 warehouse + dbt；Phase 5 Airflow
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

baseline 完成后，推进一天的命令（现在不要为了准备 handoff 就执行）：

```sh
.venv/bin/python scripts/run_simulation.py --start-date 2026-02-01 --days 1
.venv/bin/python scripts/run_simulation.py --start-date 2026-02-01 --days 1 --apply
```

新 session 的第一步：阅读本文件和 schema，核对工作目录/Git/数据库检查点，
然后先与用户确认 Phase 2 API 与 watermark 的最小设计，再实现。
