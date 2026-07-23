# CaseOps

CaseOps 是《生产级多智能体系统：从架构判断到工程落地》的独立代码项目。它围绕保险理赔协作案例 C-102 逐章演进：先建立确定性业务与安全基线，再逐步加入工具状态机、MCP、多 Agent 协作、企业知识系统、可观测性、安全、评测和 AgentOps。

这不是“几段 Prompt + 一个聊天页面”的示例。仓库交付 API、PostgreSQL、迁移、租户边界、幂等、审计、Outbox、MCP、状态机、检查点、工具账本、指标、容器和 CI；每项能力都要有可运行证据。

## 当前里程碑：Slice 1

第 1 章的 Slice 0 保留为确定性基线：它只看到 C-102 已结构化的两个材料代码，因此判断缺少事故证明。

第 2 章的 Slice 1 加入受控调查 Agent：

1. planner 提议读取案件、规则或来源材料；
2. 运行时校验参数、allowlist、scope、风险和案件边界；
3. API 签发绑定租户、任务和短期时效的任务令牌；
4. MCP Client 通过 Streamable HTTP 调用独立工具服务；
5. 工具服务从令牌取得租户，不接受模型传入的 `tenant_id`；
6. 每次状态迁移和工具调用写入检查点与执行账本；
7. 未结构化的“道路交通事故认定书”通过 `2026.1` 版别名规则归一为 `ACCIDENT_CERTIFICATE`；
8. 最终结论为 `DOCUMENTS_COMPLETE_AFTER_NORMALIZATION`。

模型只提出下一步，不拥有执行权。默认验收使用确定性的 `ConformancePlanner` 检查控制面；它不是模型，也不会被包装成“Agent 效果”。真实模型适配器使用 OpenAI Responses API，并经过同一套控制和 MCP 链路。

## 一键运行

环境要求：

- Docker 27+；
- Docker Compose v2。

```bash
docker compose up --build -d
docker compose ps
make acceptance
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

手工发起 Agent run：

```bash
curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/cases/C-102/agent-runs \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: book-ch02-c102-0001' \
  --data '{"goal":"判断案件材料是否满足其绑定规则，并给出可追溯结论。"}'
```

关键结果：

```json
{
  "status": "completed",
  "step_count": 4,
  "result": {
    "outcome": "DOCUMENTS_COMPLETE_AFTER_NORMALIZATION",
    "resolved_document_codes": ["ACCIDENT_CERTIFICATE"],
    "missing_document_codes": []
  }
}
```

使用相同幂等键再次请求会返回相同 `run_id`，且 `replayed=true`，不会重新执行工具。

完整命令、工具账本查询、MCP 未授权验证和真实模型配置见 [第 2 章运行手册](docs/chapter-02-runbook.md)。

## 控制面

```text
API Principal
  │ tenant · actor · scopes
  ▼
Agent Runtime
  │ proposal → validate → authorize → execute → observe
  │ step/repeat/timeout/retry budgets
  │ checkpoints + tool ledger
  ▼
MCP Client ── short-lived task token ── MCP Tool Server
                                              │
                                              ▼
                                          PostgreSQL
```

四个 MCP 工具全部只读：

| 工具 | 作用 |
|---|---|
| `caseops_get_case_snapshot` | 读取案件快照 |
| `caseops_get_policy_requirements` | 读取案件绑定的规则版本 |
| `caseops_list_unclassified_documents` | 列出未归一的来源材料 |
| `caseops_resolve_document_alias` | 使用版本化别名规则做只读归一 |

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
- [第 2 章运行与验收手册](docs/chapter-02-runbook.md)

## “生产级”的准确含义

本仓库交付的是可运行、可测试、可部署、可观测、可安全演进的生产导向参考实现。它不会声称替任何企业完成以下工作：

- 真实身份提供方、组织权限和完整 OAuth 2.1 授权服务器接入；
- 指定地区和行业的合规认证；
- 经过实测的容量规划、SLO、多副本拓扑与灾备指标；
- 写工具的业务幂等、效果账本和审批闭环；
- 未实际执行的真实模型在线评测。

这些能力会在后续章节通过代码和运行证据继续加入。在证据出现之前，README 会把它们写成限制，不写成能力。

## 十章演进路线

| 章节 | 工程增量 | 验收证据 |
|---|---|---|
| 第 1 章 | 确定性内核、API、事务、审计、Outbox | 基线测试与端到端请求 |
| 第 2 章 | 工具状态机、MCP、超时、熔断与恢复 | 协议、授权、故障与恢复测试 |
| 第 3 章 | Supervisor、委托与 Join Contract | 部分成功与冲突测试 |
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
