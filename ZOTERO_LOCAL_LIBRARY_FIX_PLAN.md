# 本地Zotero文献库功能修复计划

> 生成日期：2026-08-23  
> 项目路径：`academic_research_agent`  
> 目标：修复"已索引0篇 / 检索命中0篇"的根本问题，让检索功能真正落地

---

## 一、问题诊断总结

### 1. 核心崩溃 Bug：PDF提取的null字节导致同步任务整体失败

**错误现象：**
```
asyncpg.exceptions.CharacterNotInRepertoireError:
  invalid byte sequence for encoding "UTF8": 0x00
sqlalchemy.exc.PendingRollbackError: This Session's transaction
  has been rolled back due to a previous exception during flush.
```

**根本原因：**  
PyMuPDF (`fitz`) 从某些IEEE/学术PDF中提取文本时，会产生含 `\x00`（null byte）的字符串。PostgreSQL的`VARCHAR`列拒绝null字节，导致整批`INSERT INTO local_paper_chunks`失败，进而整个事务回滚——**一个坏PDF让整次同步彻底中止，其他正常PDF也无法入库**。

`_chunk()` 函数和 `extract_source()` 都没有对提取结果做null字节清洗。

**影响范围：** 同步任务 `FAILED`，DB中 `indexed_papers = 0`，前端显示「已索引0篇」，所有检索返回空结果。

---

### 2. 同步架构问题：一个异常导致整批失败，无容错

当前 `run_sync()` 在一次大事务中处理全部BibTeX条目。任何单个PDF的 flush 失败都会污染整个 SQLAlchemy Session（`PendingRollbackError`），导致后续所有条目也无法写入。

---

### 3. 权限设计问题：搜索/Ask API仅限 `is_app_admin`

```python
# local_library.py 全部端点
async def search_local_library(body, db, current_admin: CurrentAppAdmin)
async def ask_local_library(body, db, current_admin: CurrentAppAdmin)
```

前端页面供**普通登录用户**访问，但后端要求 `is_app_admin` 标志，普通用户调用会收到 `403`，前端静默展示空结果。

---

### 4. Bib文件名不匹配（潜在问题）

`_require_source()` 用 `root.glob("*.bib")` 搜索，取 **按文件名排序的第一个** `.bib` 文件。Zotero导出的默认文件名是中文（`我的文库.bib`），glob本身没问题，但排序结果取第一个不够健壮，应明确使用唯一的 `.bib` 文件或记录日志说明使用了哪个文件。

---

### 5. Celery worker 的 `research-worker-cpu` 未挂载本地文库

`docker-compose.local-library.yml` 中为 `research-worker-cpu` 挂载了卷：
```yaml
research-worker-cpu:
  volumes:
    - models_cache:/app/models_cache
    - ${LOCAL_PAPER_LIBRARY_HOST_PATH}:/local-paper-library:ro
```
但当前 `docker-compose.yml` 中对应的 `celery_worker` 没有同步这一挂载，若通过主 compose 文件启动则 worker 容器内看不到 `/local-paper-library`，同步任务在worker侧会报 `NOT_INITIALIZED`。

---

## 二、修复方案（按优先级）

### P0：修复 null 字节导致的同步崩溃

**文件：** `backend/app/services/literature_research/local_paper_library.py`

在 `extract_source()` 的返回处理和 `_chunk()` 的输入处，清洗null字节：

```python
def _strip_null(text: str) -> str:
    """PostgreSQL VARCHAR 不接受 null byte（\x00），PDF提取时可能产生。"""
    return text.replace("\x00", "")

def extract_source(path: Path) -> list[tuple[int, str]]:
    kind = SUPPORTED_SUFFIXES[path.suffix.lower()]
    if kind == "html":
        parser = _SafeHTMLText()
        parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
        parser.close()
        return [(1, _strip_null(parser.text()))]
    document = fitz.open(path)
    try:
        return [
            (page.number + 1, _strip_null(page.get_text("text")))
            for page in document
        ]
    finally:
        document.close()
```

**改动量：** ~5行，风险极低，立竿见影。

---

### P0：修复 run_sync 的事务隔离，改为逐条目独立异常处理

**文件：** `backend/app/services/literature_research/local_paper_library.py`

当前 `run_sync()` 在一个事务里处理所有条目。应将每个 BibTeX 条目的 DB flush 包裹在独立的 savepoint 中，单条失败时回滚到 savepoint 并进入 quarantine，不影响其他条目：

```python
# 在 for entry in entries: 循环内，将 flush 调用改为：
try:
    await self.db.flush()  # 或批量 add_all 后 flush
except Exception as exc:
    await self.db.rollback()  # 回滚到可用状态
    await self._quarantine(run, library, "PARSE_OR_INDEX_ERROR",
                           relative, entry.citekey,
                           f"{type(exc).__name__}: {exc}")
    summary["errors"] += 1
    continue
```

更稳健的方案是使用 SQLAlchemy 的嵌套事务（savepoint）：
```python
async with self.db.begin_nested():
    # 处理单条entry的DB写入
    ...
```

---

### P1：修复权限——将搜索/Ask/导出端点改为普通登录用户可访问

**文件：** `backend/app/api/routes/v1/literature_research/local_library.py`

```python
# 修改前：所有端点使用 current_admin: CurrentAppAdmin
# 修改后：search / ask / export 改为 CurrentUser
from app.api.deps import CurrentUser, CurrentAppAdmin, DBSession

@router.get("/status")
async def local_library_status(db: DBSession, current_admin: CurrentAppAdmin):
    # 保持 admin-only（系统信息）

@router.post("/sync")  
async def sync_local_library(db: DBSession, current_admin: CurrentAppAdmin):
    # 保持 admin-only（写操作）

@router.post("/search")
async def search_local_library(body, db: DBSession, current_user: CurrentUser):
    # 改为普通用户可访问
    return await LocalPaperLibraryService(db).search(
        owner_id=current_user.id, request=body
    )

@router.post("/ask")
async def ask_local_library(body, db: DBSession, current_user: CurrentUser):
    return await LocalPaperLibraryService(db).ask(
        owner_id=current_user.id, question=body.question, limit=body.limit
    )

@router.post("/export")
async def export_local_library(body, db: DBSession, current_user: CurrentUser):
    ...
```

同时修改 `LocalPaperLibraryService._assert_owner()` 的逻辑，允许搜索只读访问任何已有的 library（当前单 library 设计下，`owner_id` 检查会导致非创建者用户 403）：

```python
# 可选：搜索时去掉 owner 校验，或允许 admin 的 library 被普通用户只读查询
```

---

### P2：增强 run_sync 日志和前端状态轮询

当前同步状态只有 `QUEUED / RUNNING / READY / FAILED`，前端显示 `QUEUED · 尚无结果` 后不会自动刷新。

- 后端：同步完成后在 `library.last_sync_summary_json` 中写入 `indexed / errors / quarantined` 统计
- 前端：`/status` 接口已有，前端应在 `QUEUED/RUNNING` 状态时每3s轮询一次，完成后显示统计

---

### P3：Bib文件名健壮性

```python
bib_files = sorted(root.glob("*.bib"))
# 改为：
bib_files = list(root.glob("*.bib"))
if len(bib_files) > 1:
    logger.warning("发现多个.bib文件，将使用：%s", bib_files[0])
entries = parse_bibtex(bib_files[0].read_text(encoding="utf-8", errors="ignore"))
```

---

## 三、验证步骤

1. 修复 `extract_source()` 加入 `_strip_null()` 后，重新触发同步（前端「手动同步/增量重建」按钮）
2. 查看 Celery worker 日志：`docker logs 719bac54e327 -f`
3. 同步完成后，前端状态应显示 `已索引 N 篇`（N > 0）
4. 输入 `semantic communication` 检索，应出现命中结果
5. 测试「有证据问答」——应能返回基于页码证据的回答，而非「没有找到可引用的页码证据」

---

## 四、问题清单（优先级排序）

| # | 问题 | 影响 | 优先级 | 修改文件 |
|---|------|------|--------|----------|
| 1 | PDF提取null字节导致整个同步任务crash | 核心功能完全不可用 | **P0** | `local_paper_library.py` |
| 2 | 单条目失败污染整个事务，无容错 | 核心功能完全不可用 | **P0** | `local_paper_library.py` |
| 3 | search/ask/export端点要求app_admin | 普通用户无法使用 | **P1** | `local_library.py` (routes) |
| 4 | 前端不轮询同步状态，QUEUED后无反馈 | UX问题 | **P2** | 前端组件 |
| 5 | Bib文件多个时取第一个无警告 | 低概率数据错误 | **P3** | `local_paper_library.py` |

---

## 五、你的怀疑是否成立？

**部分成立，但问题更具体：**

这不是"套壳未部署"——基础设施（PostgreSQL、Qdrant、Celery、MinIO、GROBID）全部在运行，后端逻辑（BibTeX解析、PDF分块、向量索引）代码实现也是真实的。

**真正的问题是：**  
一个真实的代码Bug（PDF中的null字节 `\x00` 未清洗）导致同步任务在数据写入阶段crash，整批文献无法入库，后续所有检索自然命中0篇。这是一个典型的"代码写了但没跑通"的问题，而非"界面套壳"。

修复 `extract_source()` 中的 `_strip_null()` 即可让整个链路打通。
