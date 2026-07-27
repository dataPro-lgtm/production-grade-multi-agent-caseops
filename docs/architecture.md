# CaseOps 架构说明

## 当前边界：Slice 4

CaseOps 仍以第 1 章的确定性业务内核为事实来源。Slice 1 建立受控 Agent 运行时和 MCP 工具服务；Slice 2 增加 Supervisor、三个专业 Agent、A2A 1.0、Evidence Join 与 CloudEvents Outbox；Slice 3 增加来源治理、混合检索、受限关系路径、Context Pack 与 Context Trace；Slice 4 把跨服务 deadline、健康语义、遥测和恢复验证收进同一套运行契约。任何 Agent 都没有租户身份、数据库凭据或最终处置权，任何检索候选也不会绕过上下文门禁直接进入模型。

```text
HTTP API
  │ API Key → Principal(tenant_id, actor_id, scopes)
  │ idempotency_key / request_id
  ▼
Agent Run Service
  │ durable run · checkpoint · audit · outbox
  ▼
Controlled Agent Runtime
  │ planner proposes one action
  ├─ schema validation
  ├─ tool allowlist + scope + resource boundary
  ├─ step / repeat / timeout / retry budget
  └─ tool ledger + terminal contract
          │ short-lived task token
          ▼
MCP Client ── Streamable HTTP ── MCP Tool Server
                                      │ verified tenant/task/scopes
                                      ▼
                                 PostgreSQL
```

Slice 2 的协作链路：

```text
HTTP API / trusted Principal
  │ idempotency key
  ▼
Supervisor Service ── collaboration_runs / delegated_tasks
  │
  ├─ Delegation Contract: goal · acceptance · evidence · scope · deadline
  ├─ least-privilege task token
  └─ parallel dispatch
          │
          ▼
     A2A 1.0 Agent Server ── durable A2A task store
          │ typed SpecialistResult artifacts
          ▼
 Coverage Agent ─┐
 Document Agent ─┼── MCP Streamable HTTP ── governed read tools ── PostgreSQL
 Risk Agent ─────┘
          │
          ▼
 Deterministic Evidence Join
  │ schema · task binding · evidence allowlist · quorum · conflict
  ▼
 CollaborationResult + Audit + CloudEvents Outbox
```

Slice 3 的上下文链路：

```text
Question + Principal + purpose + as_of
  ▼
Governed Query Planner
  ├─ structured lookup
  ├─ PostgreSQL full-text search
  └─ allowlisted graph path templates / max_hops=2
  ▼
Reciprocal Rank Fusion
  ▼
Context Builder
  │ scope · purpose · temporal · hash · trust · dedup · budget
  ▼
Evidence Sufficiency
  ▼
Context Pack ── Claim-Citation Binding ── Context Trace
  ▼
context_runs + Audit + CloudEvents Outbox
```

Slice 4 的运行保证：

```text
Client traceparent + timeout/deadline
  ▼
Runtime Envelope
  │ absolute deadline · trace id · remaining budget
  ▼
API ───────────────► A2A ───────────────► MCP
 │                     │                    │
 └──────── OpenTelemetry spans ─────────────┘
                       ▼
                OTel Collector ──► Tempo

Prometheus ◄── /metrics + low-cardinality SLI ── API
    │
    └── recording rules + error-budget alerts ──► Grafana

PostgreSQL ──► custom dump + manifest
    │
    └──► isolated restore ──► revision + signature + business invariant
```

## 执行与验证路径

| 路径 | 用途 | 是否调用外部模型 |
|---|---|---|
| `conformance + direct` | 单元与 API 集成测试，验证控制面 | 否 |
| `conformance + mcp` | Docker 默认验收，验证真实 MCP 协议链 | 否 |
| `openai + mcp` | 有 API Key 时验证真实模型规划 | 是 |
| `collaboration direct` | 单元与 API 集成测试，验证委托和 Join | 否 |
| `collaboration a2a + mcp` | Docker 默认协作验收，验证完整协议链 | 否 |
| `context structured + full_text + graph` | Docker 默认上下文验收，验证来源、时态、权限、证据与关系路径 | 否 |

`ConformancePlanner` 是确定性的协议一致性驱动器，不伪装成 Agent。真实模型适配器使用 Responses API，但无论 planner 来自哪里，都必须经过同一套契约、授权、预算、MCP、账本和检查点。

## 工具面

当前有五个只读、幂等、封闭世界工具：

| 工具 | 作用域 | 业务责任 |
|---|---|---|
| `caseops_get_case_snapshot` | `case:read` | 读取不可变案件快照 |
| `caseops_get_policy_requirements` | `policy:read` | 读取案件绑定的精确规则版本 |
| `caseops_list_unclassified_documents` | `document:read` | 发现尚未结构化的来源材料 |
| `caseops_resolve_document_alias` | `document:resolve` | 用版本化别名规则做只读归一 |
| `caseops_list_risk_signals` | `risk:read` | 读取结构化风险信号，不授权处置 |

工具参数中没有 `tenant_id`。MCP 服务从已验证的短期任务令牌提取租户、调用主体、任务 ID 和作用域，避免模型通过参数越权。

## 状态与恢复

显式状态机只允许以下主路径：

```text
created → planning → tool_proposed → tool_authorized
        → tool_running → observing → planning
        → completed | needs_human | stopped | failed
```

每次状态迁移都写入 `agent_checkpoints`；每个调用以 `run_id + tool_call_id` 写入 `tool_executions`。动作指纹不包含 `tool_call_id`，因此即使模型不断生成新调用 ID，重复的“工具 + 参数”仍会被识别并熔断。

进程在只读工具的 `tool_running` 窗口崩溃时，运行时可从上一个检查点恢复并重新读取。Slice 1 不宣称写操作恰好一次：未来写工具必须先提供业务幂等键、效果账本和人工审批语义，否则恢复会停在 `needs_human`。

## 多 Agent 的状态所有权

Supervisor 是 `collaboration_runs`、`delegated_tasks` 与最终 Join 的唯一写入者。专业 Agent 只能返回不可变 `SpecialistResult` Artifact，不能直接改全局状态。A2A 自身的 `Task` 由官方 SDK 的 PostgreSQL TaskStore 持久化；它是协议生命周期，不替代业务任务账本。

每个 Delegation Contract 必须包含：

- 子目标与验收条件；
- 允许的证据类型；
- 最小作用域；
- 截止时间；
- 父运行与任务标识。

短期任务令牌同时绑定 `tenant_id`、`task_id`、`audience` 和 scope。A2A Agent 接收任务后，再为 MCP 调用签发范围不扩大的任务令牌。

## Join 不做语言平均

`EvidenceJoin` 先做结构化验收，再形成总结：

1. 校验结果 Schema、任务 ID 与专业 Agent 身份；
2. 校验证据 URI 是否属于该专业 Agent 的允许范围；
3. 判断必要节点和最低成功数；
4. 按 Claim key 检测冲突；
5. 将失败、部分成功、证据不足和冲突保留为不同终态。

风险 Agent 对 C-102 只给出 `manual_review_required`，不会执行拒赔、冻结或通知。最终结果为 `COMPLETE_WITH_REVIEW_REQUIRED`，`side_effect=none`。

## 事件语义

运行开始、三个委托完成和 Join 完成均写入与业务状态同一 PostgreSQL 事务中的 Outbox。Payload 使用 CloudEvents 1.0 信封，包含 `id`、`source`、`type`、`subject`、`time`、`dataschema`、`correlationid` 和 `tenantid`。这里实现的是可靠事件产生端；跨 Broker 投递、消费者幂等与重放策略将在基础设施章节继续扩展。

## C-102 为什么需要这一层

确定性基线只看到两个结构化代码，因此结论是缺少 `ACCIDENT_CERTIFICATE`。来源材料区实际存在“道路交通事故认定书”，但尚未映射为规范代码。受控 Agent 通过四步工具链找到材料，并使用 `2026.1` 版别名规则完成归一，最终得到 `DOCUMENTS_COMPLETE_AFTER_NORMALIZATION`。

单 Agent 可以判断材料是否齐备，却不应该同时拥有规则解释、来源材料归一、风险判断和最终处置责任。Slice 2 将这三类证据边界拆开并行调查，再由 Supervisor 按显式 Join Contract 收敛。拆分的理由是权限、证据与失败域不同，而不是角色名称不同。

Slice 3 进一步解决“Agent 究竟看到了什么”。案件快照、当前规则、历史规则、来源材料、别名规则、风险信号、风险规则和外部邮件都可能被检索到，但只有满足当前主体 scope、调查用途、`as_of`、完整性和信任边界的对象才能进入 Context Pack。`2025.4` 历史规则和带越权指令的外部邮件被明确拒绝并保留 Trace，最终三条 Claim 分别绑定规则、材料和风险证据。

## 已实现的生产属性

| 属性 | 当前实现 |
|---|---|
| 租户边界 | API Principal 与 MCP 任务令牌双重绑定；工具参数不接收租户 |
| 工具授权 | allowlist、scope、风险等级、案件资源边界四重检查 |
| 有限执行 | step、重复指纹、单工具超时与有限重试 |
| 可恢复性 | durable checkpoint；只读崩溃窗口安全重放 |
| 可审计性 | Agent run、每次工具执行、审计事件与 Outbox 均持久化 |
| MCP | 官方 Python SDK、Streamable HTTP、结构化输出、Bearer 校验 |
| A2A | 官方 Python SDK 1.x、Agent Card、HTTP+JSON 1.0、持久化 Task |
| 委托 | 目标、验收、证据、scope、deadline 与父子任务绑定 |
| Join | 必要节点、quorum、部分失败与冲突升级 |
| 事件 | CloudEvents 1.0 信封与事务 Outbox |
| 来源治理 | owner、分类、刷新 SLA、parser version 与启停状态 |
| 检索 | PostgreSQL 全文、结构化查询、最长两跳关系路径与 RRF |
| 上下文门禁 | scope、purpose、as-of、hash、信任、去重与 token budget |
| 证据 | Context Pack、Claim-Citation Binding 与逐候选 Context Trace |
| 模型隔离 | Responses API 仅返回提议或终态；不持有数据库凭据 |
| 时间预算 | 相对 timeout 转换为绝对 deadline；跨 API/A2A/MCP 单调递减 |
| 健康语义 | liveness、startup、readiness 分离；关键依赖失败与可选依赖降级分开表达 |
| 分布式追踪 | W3C Trace Context、OpenTelemetry SDK、Collector 与 Tempo |
| SLI/SLO | 低基数请求指标、记录规则、错误预算快速燃烧与依赖告警 |
| 数据恢复 | 带哈希清单的 custom dump、隔离恢复、内容签名和业务不变量 |
| 质量门禁 | Ruff、Mypy strict、pytest、分支覆盖率、Bandit、pip-audit、容器验收 |

## 尚未声称完成

- 本地任务令牌使用共享密钥签名，是开发环境的 STS 替身；企业 OIDC、密钥轮换与完整 OAuth 2.1 授权服务器在安全章节接入；
- 当前工具全部只读，外部副作用、审批与 Effect Ledger 在后续切片实现；
- 目前由 API 请求同步等待三个专业 Agent；Slice 4 让同步等待受绝对 deadline 约束，但面向长任务的异步提交、回调与跨进程 Supervisor 恢复仍未实现；
- OpenAI 适配器有契约测试，但没有 API Key 时不声称完成真实模型在线验收；
- Slice 4 已提供本地 SLI/SLO 规则和隔离恢复证据，但没有真实生产流量容量报告、多副本会话池、PITR 或跨区域灾备指标；
- 当前未启用 vector channel；需先交付 embedding 版本、真实领域语料、ACL 过滤策略和可复现检索评测。

这些限制是显式演进项，不用 Prompt 或文档承诺替代代码证据。
