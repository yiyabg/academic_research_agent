# 正式评测数据准备指南

本指南对应 Phase 3、Phase 6 的三个外部输入：授权 venue 指标快照、人工 Gold Dataset、
CORD-19/TREC-COVID 外部基准。它们不能由待评测模型自行生成，也不能用虚构样例冒充正式数据。

## 1. 授权 venue 指标快照

### 1.1 数据来源与许可

- JIF：使用机构合法订阅导出的 JCR / Web of Science Journals API 数据。
- 中科院分区：使用机构合法持有的指定年度最终版，不从非官方“期刊查询”站点抓取。
- 会议等级：使用用户明确选择并有权使用的 CCF、CORE 或机构白名单。
- 保存合同号、订阅记录或内部许可单号；在上传界面填写 `license_reference` 和
  `authorized_scope`。原始快照进入私有对象存储，不进入公开发布包。

先复制 [templates/venue_metrics_snapshot_template.csv](templates/venue_metrics_snapshot_template.csv)，
一行表示一个 venue 的一个指标事实：

```csv
venue_name,venue_type,issn_l,metric_name,metric_value,metric_year
Journal of Example Systems,journal,1234-5678,jif,8.2,2025
Journal of Example Systems,journal,1234-5678,cas_partition,1,2025
Example Systems Conference,conference,,conference_rank,CCF_A,2025
```

示例行只用于说明格式，不得直接作为正式快照。字段约束如下：

- `venue_type` 使用 `journal` 或 `conference`；会议不得填 JIF/CAS。
- 期刊优先填规范化的 ISSN-L；系统同时使用 venue 名称回退匹配。
- `metric_name` 必须与协议字段后缀严格一致：`venue.metric.jif` 对应 `jif`，
  `venue.metric.cas_partition` 对应 `cas_partition`，
  `venue.metric.conference_rank` 对应 `conference_rank`。
- `metric_year` 必填，表示该事实所属年份；不能用上传年份代替指标年份。
- 数字比较保存纯数字；枚举等级采用协议约定的同一拼写，例如 `CCF_A`、`CORE_A_STAR`。
- 同一来源版本内不要放入 venue、指标名、年份完全相同但值冲突的重复行。

上传前运行本地检查：

```bash
sha256sum evaluation/templates/venue_metrics_snapshot_template.csv
```

正式 CSV 应另存到不进入 Git 的受控位置。先准备 3–5 行、覆盖实际协议的授权 smoke 快照，
再准备全量正式快照。以管理员登录后访问
`http://localhost:53000/admin/research-metrics` 上传，并填写：

- `source_version`：授权数据版本，例如 `JCR-2025`；
- `effective_from/effective_to`：该快照允许被哪些运行日期采用的有效窗口；
- `metric_year`：仍保留在每条事实内，系统只选择不晚于运行 `as_of_date` 的年度；
- 许可证明和使用范围，并勾选授权声明。

上传后核对页面中的 payload SHA-256、对象键、指标名和有效窗口。缺失、撤销、过期、未授权或
未来年份的指标均为 `UNKNOWN`，硬约束采用 fail-closed，不会进入严格结果集。最终发布包中的
`venue_metrics_snapshot.csv` 只导出本次运行实际引用的事实及来源证明，不导出整份授权数据库。

## 2. 人工 Gold Dataset

### 2.1 冻结评测对象

在开始标注前冻结：项目 ID、批准后的协议版本/哈希、`as_of_date`、查询计划、候选池和来源响应
SHA-256。Gold 必须与要评测的 run 属于同一项目和同一问题定义；标注者不能看到系统排序分数。

### 2.2 双人独立标注与裁决

1. 从冻结候选池导出 `case_id/title/doi`，随机打乱后分别交给 A、B 两名标注者。
2. 两人独立填写 `relevance_grade`：0 不相关、1 边缘相关、2 相关、3 核心相关；
   `relevant` 必须等于 `relevance_grade > 0`。
3. 元数据只按 DOI 注册记录、出版社页面或可信仓储复核；同一论文的预印本/正式版通过
   `observations.expected_cluster_id` 指向同一 case。
4. 分歧由第三人或预先声明的书面共识规则裁决，记录人员数、方法、完成时间和局限。
5. 证据仅保存允许引用片段的 SHA-256；数值答案保存可逐字复核的规范字符串（含必要单位/条件），
   不把受版权保护全文写进 Gold JSON。

使用 [gold_dataset_template.json](gold_dataset_template.json) 创建 `DRAFT`。数据集版本不可变；
裁决完成后复制为新版本（例如 `1.0.0-adjudicated`），加入如下 provenance，再将状态设为
`ADJUDICATED`：

```json
{
  "status": "ADJUDICATED",
  "provenance": {
    "source_name": "Project-specific independent human annotation",
    "source_url": "https://your-internal-record.example/annotation-batch-id",
    "license": "internal-evaluation-only",
    "annotator_count": 2,
    "judgment_method": "Two blinded independent labels; disagreements adjudicated by a third reviewer.",
    "completed_at": "2026-08-22T12:00:00+08:00",
    "domain_coverage": ["replace-with-domain"],
    "language_coverage": ["zh", "en"],
    "limitations": ["Replace with actual sampling and coverage limitations."]
  }
}
```

不要把上面的占位内容作为正式 provenance。当前最低样本门槛为：检索、约束、去重、元数据、
证据精度指标各 20；claim-evidence coverage、数值准确率和重试恢复各 10。某一维度没有足够的
真实标注时会返回 `NOT_EVALUATED`，而不是自动通过。完整定义见
`backend/app/services/literature_research/evaluation.py`。

在项目 run 页面展开“创建版本化金标准数据集”，粘贴 JSON 创建新版本；只有
`ADJUDICATED` 或 `EXTERNAL_BENCHMARK` 会进入可评测列表。更详细规则见
[ANNOTATION_GUIDE.md](ANNOTATION_GUIDE.md)。

## 3. CORD-19 / TREC-COVID 基准

这里应使用 **TREC-COVID Complete Round 5** 的最终累计 qrels，并与 **2020-07-16** 的
CORD-19 metadata 版本严格配对。不要把 2022 最终 CORD-19 metadata 与 Round 5 qrels 混用，
也不要把 chronological qrels 与 Complete 集合混用。

### 3.1 下载和核验

1. 从 NIST TREC-COVID Complete 页面下载最终 qrels：
   `qrels-covid_d5_j0.5-5.txt`。
2. 从 AllenAI CORD-19 官方版本表取得 2020-07-16 发布包；官方表列出的 SHA-1 为
   `7adcf31a`。下载体积较大，应由数据管理员在接受数据使用条款后手工完成。
3. 解压并保留 `metadata.csv`，同时保存下载 URL、时间、许可文本版本和文件哈希。原始全文不放入
   本仓库。
4. 先验证 qrels 中一个 topic：

```bash
cd backend
DEBUG=false .venv/bin/python scripts/import_trec_covid_gold.py \
  --qrels /controlled/path/qrels-covid_d5_j0.5-5.txt \
  --topic-id 1 \
  --inspect-only
```

5. 生成 API-ready JSON（替换真实项目 UUID 和受控路径）：

```bash
cd backend
DEBUG=false .venv/bin/python scripts/import_trec_covid_gold.py \
  --qrels /controlled/path/qrels-covid_d5_j0.5-5.txt \
  --metadata-csv /controlled/path/2020-07-16/metadata.csv \
  --topic-id 1 \
  --project-id 00000000-0000-0000-0000-000000000000 \
  --license-reference "NIST qrels terms + CORD-19 data-use record ID" \
  --completed-at 2020-07-16T00:00:00+00:00 \
  --minimum-mapped-cases 100 \
  --output /controlled/path/trec-covid-topic-1.json
```

脚本会流式读取约 257 MB 的历史 metadata，只保留该 topic 的已评审文档，排除 grade `-1`，
并报告 qrel 数、映射数、缺失 ID 和各等级数量。只有映射数达到门槛才会生成
`EXTERNAL_BENCHMARK` JSON。将结果粘贴到同一 Gold Dataset 页面创建不可变版本，然后针对 run
执行评测。

TREC-COVID 是医学英文检索基准，采用 pooled judgments，通常不是每个样本双人独立标注；它只能
证明特定外部基准表现，不能替代本项目的跨领域/中文人工 Gold 验收。CORD-19 的版权和再分发限制
适用于其中各篇文章，处理前必须阅读官方许可文件。

官方资料：

- NIST TREC-COVID Complete: <https://ir.nist.gov/covidSubmit/data.html>
- AllenAI CORD-19 releases: <https://github.com/allenai/cord19>
- CORD-19 license: <https://github.com/allenai/cord19/blob/master/LICENSE>

## 4. 推荐准备顺序

1. 先轮换已暴露的第三方 GPT Key，并保持只在本项目 `backend/.env` 中配置。
2. 制作 3–5 行授权指标 smoke 快照，验证协议匹配、年份和 fail-closed 行为。
3. 冻结一个小型候选池，建立不少于 20 条的双人 Gold v1，并完成裁决。
4. 用 3 篇论文执行一次低额度真实 run，确认发布包含 8 个必需产物和完整来源账本。
5. 数据管理员下载并核验匹配版本的 CORD-19/TREC-COVID，再导入单 topic 外部基准。
6. 扩大正式指标快照和领域 Gold，保留每次版本、哈希、许可与评测报告。
