# 部署指南

## 快速部署

```bash
# 1. 应用数据库迁移
cd backend
uv run alembic upgrade head

# 2. 重启服务
sudo systemctl restart academic_research_agent

# 3. 同步填充新字段
uv run academic_research_agent cmd sync-local-library

# 4. 验证
python verify_refactoring.py
```

## 快速测试

```bash
# 测试查询解析
curl -X POST http://localhost:8000/api/research/local-library/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "2026年发表的 semantic communication", "limit": 10}'

# 期望结果：
# - 只返回2026年的论文
# - query_interpretation.effective_filters 包含 year_from=2026, year_to=2026
# - query_interpretation.semantic_query 是 "semantic communication"
```

## 回滚

```bash
uv run alembic downgrade -1
git revert <commit-hash>
sudo systemctl restart academic_research_agent
```

详细信息见 `REFACTORING_PROGRESS.md`
