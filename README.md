# CaseOps

CaseOps 是《生产级多智能体系统：从架构判断到工程落地》的独立代码项目。它围绕保险理赔协作案例 C-102 逐章演进：先建立确定性业务与安全基线，再逐步加入工具状态机、MCP、多 Agent 协作、企业知识系统、可观测性、安全、评测和 AgentOps。

这不是“几段 Prompt + 一个聊天页面”的示例。仓库交付 API、PostgreSQL、迁移、租户边界、幂等、审计、Outbox、MCP、状态机、检查点、工具账本、指标、容器和 CI；每项能力都要有可运行证据。

## 当前里程碑：Slice 2

第 1 章的 Slice 0 保留为确定性基线：它只看到 C-102 已结构化的两个材料代码，因此判断缺少事故证明。

第 2 章的 Slice 1 加入受控调查 Agent，通过状态机和 MCP 找到未结构化的事故证明，并得到 `DOCUMENTS_COMPLETE_AFTER_NORMALIZATION`。

第 3 章的 Slice 2 在这条控制面上增加真实多 Agent 协作：

1. Supervisor 为 coverage、document、risk 三个专业 Agent 创建不可变委托合同；
2. 合同显式约束目标、验收条件、证据类型、最小 scope 和 deadline；
3. API 使用官方 A2A Python SDK，通过 A2A 1.0 HTTP+JSON 并行派发；
4. A2A Task 与 CaseOps 业务任务分别持久化，不混淆协议状态和业务责任；
5. 专业 Agent 使用短期任务令牌调用 MCP，只读取自己获准的事实；
6. `EvidenceJoin` 确定性校验 Schema、任务绑定、证据范围、quorum 和冲突；
7. 运行状态与 CloudEvents 1.0 信封写入事务 Outbox；
8. C-102 的最终结果为 `COMPLETE_WITH_REVIEW_REQUIRED`，但 `side_effect=none`。

模型不拥有执行权，专业 Agent 也不拥有全局收敛权。当前默认验收完全确定性，不用模型随机性伪装控制面正确性。

## 一键运行

环境要求：

- Docker 27+；
- Docker Compose v2。

```bash
docker compose up --build -d
docker compose ps
make acceptance-chapter-03
```

运行接口：

| 地址 | 用途 |
|---|---|
| `http://localhost:8080/docs` | OpenAPI 交互文档 |
| `http://localhost:8080/health/live` | API 存活检查 |
| `http://localhost:8080/health/ready` | API 与数据库就绪检查 |
| `http://localhost:8080/metrics` | Prometheus 指标 |
| `http://localhost:8081/health/live` | MCP 服务存活检查 |
| `http://localhost:8081/mcp` | 受 Bearer 保护的 MCP endpoint |
| `http://localhost:8082/health/live` | A2A 服务存活检查 |
| `http://localhost:8082/.well-known/agent-card.json` | A2A Agent Card |
| `http://localhost:8082/a2a/rest` | A2A 1.0 HTTP+JSON endpoint |

手工发起多 Agent 协作运行：

```bash
curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/cases/C-102/collaboration-runs \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: book-ch03-c102-0001' \
  --data '{"goal":"并行核对案件规则、材料完整性与风险信号，通过证据合同形成可追溯的协作结论。"}'
```

关键结果：

```json
{
  "status": "completed",
  "result": {
    "outcome": "COMPLETE_WITH_REVIEW_REQUIRED",
    "join": {
      "accepted_specialists": ["coverage", "document", "risk"],
      "conflicts": [],
      "quorum_met": true
    },
    "recommended_action": "route_to_human_reviewer",
    "side_effect": "none"
  }
}
```

使用相同幂等键再次请求会返回相同 `run_id` 和三个 `task_id`，且 `replayed=true`，不会重新派发专业 Agent。

完整命令、A2A Task、业务任务、事件查询和故障语义见 [第 3 章运行手册](docs/chapter-03-runbook.md)。

## 控制面

```text
API Principal
  │ tenant · actor · scopes
  ▼
Supervisor
  │ typed delegation contracts + idempotency
  │ parallel dispatch + durable task ledger
  ▼
A2A 1.0 Server
  │ coverage · document · risk artifacts
  ▼
MCP Tool Server ── five governed read tools ── PostgreSQL
  │
  ▼
Evidence Join ── quorum · evidence · conflicts ── Audit + CloudEvents Outbox
```

五个 MCP 工具全部只读：

| 工具 | 作用 |
|---|---|
| `caseops_get_case_snapshot` | 读取案件快照 |
| `caseops_get_policy_requirements` | 读取案件绑定的规则版本 |
| `caseops_list_unclassified_documents` | 列出未归一的来源材料 |
| `caseops_resolve_document_alias` | 使用版本化别名规则做只读归一 |
| `caseops_list_risk_signals` | 读取受治理的结构化风险信号 |

动作指纹以“工具名 + 规范化参数”计算，不依赖 `tool_call_id`；模型即使更换调用 ID，也不能绕过重复调用熔断。

## 本地开发

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

alembic upgrade head
caseops seed
uvicorn caseops.api.app:app --reload --port 8080
```

默认本地配置使用 SQLite 和进程内工具适配器，便于快速开发；Docker Compose 使用 PostgreSQL 和真实 MCP Streamable HTTP。`production` 配置会拒绝 SQLite、内置 API Key 和本地任务令牌密钥。

## 真实模型模式

无 API Key 时，仓库只声明控制面和 Responses API 适配器契约已验证，不声明真实模型在线链路已完成。

启用真实 planner 所需环境变量：

```text
CASEOPS_AGENT_PLANNER=openai
CASEOPS_OPENAI_API_KEY=<secret>
CASEOPS_OPENAI_MODEL=gpt-5.6-terra
CASEOPS_OPENAI_REASONING_EFFORT=low
```

API Key 必须通过秘密管理系统注入，不能写入仓库。

## 质量验证

```bash
make verify
make security
```

质量门禁包括：

- Ruff 代码规范与格式；
- Mypy strict 类型检查；
- pytest 单元、API、MCP 协议、OpenAI 适配器和恢复测试；
- 分支覆盖率门槛；
- Bandit 静态安全扫描；
- pip-audit 依赖漏洞扫描；
- PostgreSQL 迁移与种子数据验证；
- 非 root、只读文件系统容器构建；
- API → MCP → PostgreSQL 端到端验收。

## 设计文档

- [架构说明](docs/architecture.md)
- [ADR-0001：先建立确定性模块化单体](docs/adr/0001-start-with-a-deterministic-modular-monolith.md)
- [ADR-0002：先建设 Agent 控制面，再提高模型自治](docs/adr/0002-control-plane-before-model-autonomy.md)
- [ADR-0003：由 Supervisor 持有收敛权](docs/adr/0003-supervisor-owns-convergence.md)
- [第 2 章运行与验收手册](docs/chapter-02-runbook.md)
- [第 3 章运行与验收手册](docs/chapter-03-runbook.md)

## “生产级”的准确含义

本仓库交付的是可运行、可测试、可部署、可观测、可安全演进的生产导向参考实现。它不会声称替任何企业完成以下工作：

- 真实身份提供方、组织权限和完整 OAuth 2.1 授权服务器接入；
- 指定地区和行业的合规认证；
- 经过实测的容量规划、SLO、多副本拓扑与灾备指标；
- 写工具的业务幂等、效果账本和审批闭环；
- 长任务异步 API、跨进程 Supervisor 恢复与 Broker 消费端；
- 未实际执行的真实模型在线评测。

这些能力会在后续章节通过代码和运行证据继续加入。在证据出现之前，README 会把它们写成限制，不写成能力。

## 十章演进路线

| 章节 | 工程增量 | 验收证据 |
|---|---|---|
| 第 1 章 | 确定性内核、API、事务、审计、Outbox | 基线测试与端到端请求 |
| 第 2 章 | 工具状态机、MCP、超时、熔断与恢复 | 协议、授权、故障与恢复测试 |
| 第 3 章 | Supervisor、A2A、委托、Join 与 CloudEvents | 全链路、部分成功与冲突测试 |
| 第 4 章 | Context Pipeline、RAG 2.0、GraphRAG | 来源、新鲜度与检索评测 |
| 第 5 章 | 部署、遥测、恢复与平台化 | SLO、故障演练和恢复报告 |
| 第 6 章 | ToolGuard、OIDC/ABAC、隐私与审计 | 红队与安全回归 |
| 第 7 章 | 分层多 Agent 系统合龙 | C-102 全链路追踪 |
| 第 8 章 | Golden Dataset 与持续回归 | 分层评测报告 |
| 第 9 章 | AgentOps、事件诊断与成本治理 | 运营看板与 Runbook |
| 第 10 章 | 系统验收与开源发布 | 发布包、SBOM 与验收报告 |

每章会发布独立 tag；书稿引用 tag 和 commit，而不是复制源码。

## 许可与安全

代码采用 [MIT License](LICENSE)。安全问题请按 [Security Policy](SECURITY.md) 私下报告；请勿在公开 Issue 中披露漏洞细节。
