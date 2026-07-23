# CaseOps 架构说明

## 当前边界：Slice 1

CaseOps 仍以第 1 章的确定性业务内核为事实来源。Slice 1 新增受控 Agent 运行时和独立 MCP 工具服务，但模型没有获得租户身份、权限或数据库连接，也不能自行执行工具。

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

## 两条执行路径

| 路径 | 用途 | 是否调用外部模型 |
|---|---|---|
| `conformance + direct` | 单元与 API 集成测试，验证控制面 | 否 |
| `conformance + mcp` | Docker 默认验收，验证真实 MCP 协议链 | 否 |
| `openai + mcp` | 有 API Key 时验证真实模型规划 | 是 |

`ConformancePlanner` 是确定性的协议一致性驱动器，不伪装成 Agent。真实模型适配器使用 Responses API，但无论 planner 来自哪里，都必须经过同一套契约、授权、预算、MCP、账本和检查点。

## 工具面

Slice 1 只有四个只读、幂等、封闭世界工具：

| 工具 | 作用域 | 业务责任 |
|---|---|---|
| `caseops_get_case_snapshot` | `case:read` | 读取不可变案件快照 |
| `caseops_get_policy_requirements` | `policy:read` | 读取案件绑定的精确规则版本 |
| `caseops_list_unclassified_documents` | `document:read` | 发现尚未结构化的来源材料 |
| `caseops_resolve_document_alias` | `document:resolve` | 用版本化别名规则做只读归一 |

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

## C-102 为什么需要这一层

确定性基线只看到两个结构化代码，因此结论是缺少 `ACCIDENT_CERTIFICATE`。来源材料区实际存在“道路交通事故认定书”，但尚未映射为规范代码。受控 Agent 通过四步工具链找到材料，并使用 `2026.1` 版别名规则完成归一，最终得到 `DOCUMENTS_COMPLETE_AFTER_NORMALIZATION`。

这不是用模型替换规则。模型负责决定下一步查什么；案件、规则、别名、权限、状态迁移和最终结构化输出仍由确定性系统约束。

## 已实现的生产属性

| 属性 | 当前实现 |
|---|---|
| 租户边界 | API Principal 与 MCP 任务令牌双重绑定；工具参数不接收租户 |
| 工具授权 | allowlist、scope、风险等级、案件资源边界四重检查 |
| 有限执行 | step、重复指纹、单工具超时与有限重试 |
| 可恢复性 | durable checkpoint；只读崩溃窗口安全重放 |
| 可审计性 | Agent run、每次工具执行、审计事件与 Outbox 均持久化 |
| MCP | 官方 Python SDK、Streamable HTTP、结构化输出、Bearer 校验 |
| 模型隔离 | Responses API 仅返回提议或终态；不持有数据库凭据 |
| 质量门禁 | Ruff、Mypy strict、pytest、分支覆盖率、Bandit、pip-audit、容器验收 |

## 尚未声称完成

- 本地任务令牌使用共享密钥签名，是开发环境的 STS 替身；企业 OIDC、密钥轮换与完整 OAuth 2.1 授权服务器在安全章节接入；
- 当前工具全部只读，外部副作用、审批与 Effect Ledger 在后续切片实现；
- OpenAI 适配器有契约测试，但没有 API Key 时不声称完成真实模型在线验收；
- 当前没有容量报告、多副本会话池、备份恢复证明或生产 SLO。

这些限制是显式演进项，不用 Prompt 或文档承诺替代代码证据。
