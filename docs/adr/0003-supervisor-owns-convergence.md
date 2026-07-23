# ADR-0003：由 Supervisor 持有收敛权，专业 Agent 只交付证据产物

- 状态：Accepted
- 日期：2026-07-23
- 对应里程碑：Chapter 3 / Slice 2

## 背景

C-102 的材料完整性、规则约束和风险信号来自不同事实域。继续扩充一个 Agent 的 Prompt，虽然可以生成看似完整的回答，却会把权限、证据、失败重试和最终责任混在同一个概率上下文里。反过来，仅仅拆成三个角色，也不会自然得到可靠协作：系统仍需回答谁创建任务、谁能改状态、何时算完成、冲突如何处理以及失败后交付什么。

## 决策

采用 Supervisor + 并行专业 Agent + 确定性 Evidence Join：

1. Supervisor 是业务运行、委托任务和最终结果的唯一状态所有者；
2. coverage、document、risk 三个专业 Agent 只接收不可变 Delegation Contract；
3. 委托显式携带目标、验收条件、证据边界、最小作用域和截止时间；
4. 跨 Agent 调用使用官方 A2A 1.0 HTTP+JSON 与 PostgreSQL TaskStore；
5. 专业 Agent 使用第二章的 MCP 工具面读取事实，不直接连接数据库；
6. 每个结果必须符合 `SpecialistResult`，Claim 必须携带 EvidenceRef；
7. Join 由确定性代码完成 Schema、任务绑定、证据范围、quorum 和冲突检查；
8. 风险结论最多触发人工复核建议，Slice 2 不开放写工具或自动处置。

运行与任务变化通过 CloudEvents 1.0 信封写入事务 Outbox。A2A Task、业务 DelegatedTask 和 CloudEvent 是三种不同对象：分别承担协议生命周期、业务责任和跨系统事实通知。

## 选择 A2A 的边界

A2A 用于独立 Agent 应用之间的发现、任务和 Artifact 交换。进程内紧耦合节点仍使用本地端口；外部能力访问仍使用 MCP；全局生命周期仍由 Supervisor 推进。引入 A2A 不意味着把所有函数拆成远程 Agent。

## 被否决的方案

### 所有 Agent 共享一个可变 State

并发写入会让责任、覆盖顺序和恢复点不可解释。共享事实应通过版本化 Artifact，写权限按状态类型归属单一 owner。

### 让 Supervisor 用自然语言总结所有结果

语言模型可能抹平缺失证据和相互冲突的 Claim。先执行确定性 Join，再允许模型润色面向人的摘要，二者不能倒置。

### 多数投票决定事实

多个 Agent 可能使用同一来源或同一模型，数量不等于证据独立性。CaseOps 按证据引用和规则验收，不按票数替代事实。

### A2A 代替工作流和授权

A2A 提供互操作协议，不定义企业业务的状态所有权、最小权限、幂等或收敛条件。这些责任留在 CaseOps 控制面。

## 结果

收益是每项结论都能回到任务、专业 Agent、Claim、EvidenceRef 和原始 MCP 读取；部分失败和冲突成为可测试终态。代价是新增 A2A 服务、持久化 Task、业务任务账本、任务令牌、Join 和事件记录。该复杂度仅在证据域、权限域和失败域确实分离时才值得承担。
