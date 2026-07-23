# ADR-0004：Context Pack 是受治理的证据产品，不是检索结果拼接

- 状态：Accepted
- 日期：2026-07-23
- 对应里程碑：Chapter 4 / Slice 3

## 背景

C-102 的案件快照、规则版本、来源材料、别名规则和风险信号分别位于不同事实域。仅执行 Top-k 文本检索会产生三个问题：相似内容不一定在目标时间有效，召回结果不一定对当前主体和用途授权，关系型问题也不能仅靠段落相似度可靠回答。

上下文窗口增大不会消除这些问题。进入模型的内容仍需回答来源、版本、时间、权限、用途、完整性、预算以及支持哪条主张。

## 决策

将 Context Pack 作为版本化、可审计的证据产品：

1. 每个来源先登记 owner、分类、刷新 SLA、解析器版本和启停状态；
2. 每个知识对象携带 source version、locator、content hash、valid time、observed time、scope、purpose 和 claim coverage；
3. Query Planner 只能选择 allowlisted channel 和 graph path template，不能生成任意 SQL 或 Cypher；
4. Slice 3 使用 PostgreSQL 全文检索、结构化过滤和最长两跳的关系扩展；
5. 多通道结果使用 Reciprocal Rank Fusion 合并，不直接比较异构原始分数；
6. Context Builder 按 scope、purpose、as-of、hash、信任等级、去重和 token budget 顺序执行门禁；
7. 检索到的非可信指令被视为数据并拒绝进入 Pack；
8. Evidence Sufficiency 按必要 claim type 判断，达到充分度或命中停止条件后终止；
9. 最终回答中的每条 Claim 必须引用 Pack 内 Evidence ID；
10. 候选的进入与拒绝原因写入 Context Trace，并与结果、审计和 Outbox 同事务持久化。

## 为什么本切片不启用向量通道

仓库尚未交付经过领域数据校准的 embedding provider、向量索引版本、召回基线和离线评测集。为少量种子文本生成伪向量只能证明 API 会运行，不能证明语义检索有效。

因此合同保留 `vector` channel，但 Slice 3 的 C-102 查询只启用 `structured + full_text + graph`。向量通道必须在具备真实语料、固定 embedding 版本、ACL 过滤策略和可复现检索评测后才能进入默认计划。

## 被否决的方案

### 把所有内容放入长上下文

会同时放大过期信息、越权信息、提示注入和注意力稀释，且无法解释每条内容为何进入。

### 让模型自由生成 SQL 或 Cypher

灵活性高，但查询成本、标签范围、遍历深度和数据暴露不可预测。当前只开放显式路径模板和有界关系扩展。

### 用相似度阈值决定证据充分

相似度衡量检索接近程度，不等于目标主张已经被覆盖。CaseOps 分开记录 ranking score 与 claim coverage。

### 只在最终答案末尾附来源链接

无法判断哪个来源支持哪条主张，也不能发现引用漂移。CaseOps 在 `AnswerClaim.evidence_ids` 中逐条绑定。

## 结果

收益是每次模型调用前都能重放“候选从哪里来、为何有权看、在何时有效、为何被选中或拒绝、支持哪条主张”。代价是新增来源与知识对象登记、关系表、门禁、Trace 和评测责任。该复杂度属于生产上下文系统的控制面，而不是 Prompt 技巧。
