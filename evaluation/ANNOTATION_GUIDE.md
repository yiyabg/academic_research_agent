# 人工 Gold 数据标注与验收规范

本目录用于构建可复核的人工金标，而不是放置由系统自评生成的“伪金标”。只有状态为
`ADJUDICATED` 或 `EXTERNAL_BENCHMARK` 且包含完整 provenance 的数据集才能提交运行评测。
`ADJUDICATED` 必须至少两名独立标注者参与；`EXTERNAL_BENCHMARK` 必须如实记录公开基准的
评审方法和局限，不能等同于本项目已完成双人标注。

## 标注流程

1. 冻结研究协议、查询主题、候选池快照和原始来源哈希。
2. 两名或以上标注者独立判断论文是否与协议相关，不得查看系统排序分数。
3. 对分歧样本由第三人或书面共识流程裁决，并记录裁决时间和方法。
4. 元数据按 DOI 注册记录、出版社页面或仓储记录核验；预印本和正式版分别记录 source observation，
   但使用同一 expected cluster 标识。
5. 证据金标保存允许引用片段的 SHA-256，不在仓库再分发受版权保护的全文。
6. 数值金标只录入可从正文、表格单元格或有校准误差的图中复核的字符串，同时保留单位和条件。
7. 数据集先以 `DRAFT` 导入；数据集是不可变版本，完成双人标注和裁决后，必须以新版本号和
   provenance 创建 `ADJUDICATED` 版本，不能原地修改 DRAFT。
8. 外部人工基准使用 `EXTERNAL_BENCHMARK`，保留原始 0–3 分级标签；若原基准不是双人逐样本
   独立标注，必须写入 `limitations`。

## 最低样本量

评测服务对 Recall@Pool、Precision@20、nDCG@20、约束合规、去重、元数据和证据指标设置了
最低样本量。样本不足返回 `NOT_EVALUATED`，不会因为少量样本全对就显示 PASS。具体门槛以
`backend/app/services/literature_research/evaluation.py` 中的 `MIN_SAMPLES` 为准。

## 禁止事项

- 不得让待评测模型生成自身金标。
- 不得把单人快速浏览标成 `ADJUDICATED`。
- 不得把缺失维度按满分处理。
- 不得在未核验许可时把第三方全文或高清图表提交到公共仓库。
