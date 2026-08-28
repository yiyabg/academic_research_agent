# 部署与验收

本项目以 Docker Compose 为完整运行拓扑：API、迁移、PostgreSQL、Redis、Qdrant、对象存储，以及 `research-worker-cpu` 和 `research-worker-llm` 都必须运行。不要用单独的 systemd/uvicorn 进程替代该拓扑。

## 部署顺序

1. 备份 PostgreSQL。同步会清空已废弃的摘要/引言/结论兼容字段；不要把 Alembic downgrade 当作无条件安全回滚。
2. 从 `backend/.env.example` 创建并填写 `backend/.env`，尤其是密钥、对象存储和 `LOCAL_PAPER_LIBRARY_HOST_PATH`。
3. 构建并运行迁移，然后启动完整拓扑：

```bash
docker compose --env-file backend/.env -f docker-compose.yml -f docker-compose.research.yml build
docker compose --env-file backend/.env -f docker-compose.yml -f docker-compose.research.yml run --rm migrate
docker compose --env-file backend/.env -f docker-compose.yml -f docker-compose.research.yml up -d --wait
docker compose --env-file backend/.env -f docker-compose.yml -f docker-compose.research.yml ps
```

4. 确认 `research-worker-cpu` 正常运行后，通过 API 或 CLI 入队同步任务：

```bash
docker compose --env-file backend/.env -f docker-compose.yml -f docker-compose.research.yml exec app \
  uv run academic_research_agent cmd rag-source-sync
```

该 CLI 只负责入队，实际解析、切分、嵌入由 `research-worker-cpu` 完成。同步完成后再进行搜索和分析验收。

## 端口与执行模式

容器内部始终使用 `POSTGRES_HOST=db` 与 `POSTGRES_PORT=5432`。`POSTGRES_EXPOSE_PORT` 只控制宿主机映射（例如 `55432:5432`），绝不能填入容器的 `POSTGRES_PORT`。

生产默认 `LOCAL_PAPER_ANALYSIS_EXECUTION_MODE=staged`。background 是实验性显式选项，且必须额外启用 `LOCAL_PAPER_ANALYSIS_ALLOW_EPHEMERAL_PROVIDER_STORAGE=true`；它会让模型服务临时保存可轮询的响应。

`research-worker-llm` 默认并发为 1，以避免一个 worker 同时启动多个 analysis job 而超过上游模型并发预算。

## 验收

```bash
curl -X POST http://localhost:8000/api/v1/research/local-library/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"2026年发表的 semantic communication","limit":10}'

docker compose --env-file backend/.env -f docker-compose.yml -f docker-compose.research.yml logs --tail=200 research-worker-cpu
```

响应中的 `query_interpretation` 应显示 `semantic_query` 为 `semantic communication`，并显示 `year_from` 与 `year_to` 均为 2026。验收还应确认 `research-worker-llm` 正常消费 staged analysis queue，并在需要时下载 analysis artifact。

本指南不承诺未实际测量的时延或吞吐量。

## 迁移回归测试安全边界

`backend/tests/test_migrations.py` 会执行 `downgrade base`，因此默认跳过，绝不能对正在运行的项目数据库直接启用。仅可在一次性、无数据卷的 PostgreSQL 实例上明确授权运行：

```bash
cd backend
MIGRATION_TEST_ISOLATED_DATABASE=1 \
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55433 \
POSTGRES_USER=refactor_test POSTGRES_PASSWORD=... POSTGRES_DB=refactor_migration_test \
uv run pytest -q tests/test_migrations.py
```

这不是生产回滚流程；生产数据库的历史数据可能已不满足旧 schema 的唯一约束，必须先备份并人工评估，不能为通过测试删除数据。
