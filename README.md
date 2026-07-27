# CaseOps

CaseOps 是《生产级多智能体系统：从架构判断到工程落地》的独立代码项目。它围绕保险理赔协作案例 C-102 逐章演进：先建立确定性业务与安全基线，再逐步加入工具状态机、MCP、多 Agent 协作、企业知识系统、可观测性、安全、评测和 AgentOps。

这不是“几段 Prompt + 一个聊天页面”的示例。仓库交付 API、PostgreSQL、迁移、租户边界、幂等、审计、Outbox、MCP、状态机、检查点、工具账本、指标、容器和 CI；每项能力都要有可运行证据。

## 当前里程碑：Slice 8

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

第 4 章的 Slice 3 把“Agent 能取数”升级为“每次推理只获得受治理的证据产品”：

1. Source Registry 登记 owner、分类、刷新 SLA 与 parser version；
2. 知识对象携带 source version、valid time、observed time、ACL scope、purpose 与 content hash；
3. Query Planner 只允许结构化、PostgreSQL 全文检索和显式 Graph Path Template；
4. 多通道候选用 RRF 融合，不混用不可比较的原始分数；
5. Context Builder 依次执行 scope、purpose、as-of、完整性、非可信指令、去重和 token budget 门禁；
6. Evidence Sufficiency 按必要主张类型判断，最多执行两轮；
7. 最终三条 Claim 均绑定 Context Pack 内的 Evidence ID；
8. 过期规则和带提示注入的外部邮件会进入 Context Trace，但不会进入 Context Pack。

第 5 章的 Slice 4 把“服务能启动”升级为“运行保证可执行、可观测、可恢复”：

1. API、A2A、MCP 统一接收并传播 W3C Trace Context；
2. 相对 timeout 在入口转换为绝对 deadline，下游只能消耗剩余预算；
3. liveness、startup、readiness 分离，数据库是关键依赖，A2A/MCP 是可降级依赖；
4. OpenTelemetry Collector 汇聚跨服务 Trace，Tempo 保存因果链；
5. Prometheus 使用低基数 SLI、错误率记录规则和面向用户症状的告警；
6. Grafana 预置运行看板，可从请求指标跳转到 Trace；
7. PostgreSQL 备份包含版本与哈希清单，恢复在隔离数据库验证内容签名和业务不变量；
8. 一键验收会真实停止 A2A、观察降级、重启服务并完成恢复演练。

第 6 章的 Slice 5 把“声明最小权限”升级为“权限与数据释放都经过可执行判定”：

1. 每个工具都有版本化 Security Manifest，并用 SHA-256 绑定真实 Tool Definition；
2. MCP 执行前统一经过默认拒绝的 ToolGuard；
3. 有效 scope 取用户、工作负载和本次委托三者交集；
4. 短期令牌绑定 tenant、task、audience、purpose、resource 和 workload；
5. 工具版本、scope、风险或定义漂移会拒绝执行；
6. 每次允许与拒绝只记录策略、Manifest、上下文和参数摘要，不落原始 Prompt 与业务载荷；
7. OutputGuard 会最小化邮箱与手机号，并阻断合成 canary secret 和未获准的受限数据；
8. 11 个红队样例与一条真实恶意协作目标共同验证零越权副作用、零 canary 泄漏。

第 7 章的 Slice 6 把前六章能力合龙为一个可验收的分层系统：

1. Central Supervisor 先提交经过环检测的类型化 DAG，不靠自由文本记录依赖；
2. Context Team 产出受治理的 Context Pack，Collaboration Team 通过真实 A2A → MCP 链调度三个专业节点；
3. Team Supervisor 持有团队内收敛权，Central Supervisor 只消费已验收的团队 Artifact；
4. 系统级 Reducer 确定性核对规则版本、材料状态、风险门禁、证据绑定和只读边界；
5. `system_runs` 与 `system_steps` 持久化父子运行、依赖、尝试次数、结果引用和摘要；
6. Runtime Context Graph 把 Goal、Plan、Step、Task、Context Pack、Claim、Evidence、Acceptance 与 Result 连成可查询的来源图；
7. 图中只保存引用、分类和摘要，不复制原始 Prompt 或业务载荷；
8. 同一幂等键可安全重放，失败运行可复用已完成的子运行继续收敛；
9. C-102 通过全部 7 项系统检查，因风险规则进入人工复核，且 `side_effect=none`。

第 8 章的 Slice 7 把“测试通过”升级为“候选发布有系统级质量证据”：

1. Golden Dataset、Quality Contract 与 v0.7.0 基线全部版本化；
2. 5 类场景执行 13 次真实 HTTP 运行，覆盖标准合龙、最小预算、提示注入、幂等重放与冲突；
3. grader 分层检查契约、结果、路径、证据、安全和效率预算，不用一个平均分掩盖高风险失败；
4. 每个成功运行都检查 3 步 DAG、7 项系统验收、6 条 Claim、7 个 Evidence 引用；
5. Runtime Context Graph 至少包含 20 个节点和 40 条边，且所有 Claim 必须存在 `SUPPORTED_BY`；
6. 每次成功运行都用另一租户反向访问 Context Graph，必须返回 404；
7. N-run 使用排除运行 ID 与时间戳的语义指纹检查漂移；
8. 候选与冻结基线按 case、按 layer 成对比较，任一硬层下降即阻断发布；
9. CI 留存完整 JSON 报告，wall-clock 仅诊断，目标环境 SLO 由生产遥测判定；
10. 模型 Judge 保持辅助地位，未经人工校准不能覆盖确定性安全门禁。

第 9 章的 Slice 8 把“有监控”升级为“事故可以因果重建、成本可以诚实归因、恢复可以实际验证”：

1. 运行评估是显式、幂等、租户隔离的写操作，只接受已经进入终态的系统运行；
2. `operational_assessments` 保存影响、时间线、首个失败点、证据清单、成本归因、处置建议与版本快照；
3. 诊断会穿透顶层系统步骤，继续检查委托任务，避免把 `SYSTEM_REJECTED` 当作根因；
4. Incident Bundle 只保存运行、步骤、子任务和 Context Graph 的引用与摘要，不复制 Prompt、工具参数或业务载荷；
5. `operational_cost_events` 按成功目标归因步骤尝试、上下文运行、委托尝试和安全决策；
6. 确定性模式没有模型调用和供应商价目表，因此货币成本明确为 `null`，不伪造精确金额；
7. GameDay 真实停止 A2A，验证三个委托任务受控失败、外部副作用为零、首故障位于协作层；
8. A2A 恢复后重新执行系统运行，必须再次形成健康评估；
9. 基线、事故与恢复三份评估合并为版本化 JSON 证据，并由 CI 留存。

模型不拥有执行权，专业 Agent 不拥有全局收敛权，检索器也不能决定什么可以进入模型。当前默认验收完全确定性，不用模型随机性伪装控制面、运行保证或安全策略的正确性。

## 一键运行

环境要求：

- Docker 27+；
- Docker Compose v2。

```bash
docker compose up --build -d
docker compose ps
make acceptance-chapter-09
```

如需同时启动 Collector、Tempo、Prometheus 与 Grafana：

```bash
make observability-up
make acceptance-chapter-05
```

运行接口：

| 地址 | 用途 |
|---|---|
| `http://localhost:8080/docs` | OpenAPI 交互文档 |
| `http://localhost:8080/health/live` | API 存活检查 |
| `http://localhost:8080/health/startup` | 数据库迁移启动检查 |
| `http://localhost:8080/health/ready` | 关键与可选依赖就绪检查 |
| `http://localhost:8080/metrics` | Prometheus 指标 |
| `http://localhost:8081/health/live` | MCP 服务存活检查 |
| `http://localhost:8081/mcp` | 受 Bearer 保护的 MCP endpoint |
| `http://localhost:8082/health/live` | A2A 服务存活检查 |
| `http://localhost:8082/.well-known/agent-card.json` | A2A Agent Card |
| `http://localhost:8082/a2a/rest` | A2A 1.0 HTTP+JSON endpoint |
| `http://localhost:3200/ready` | Tempo 就绪检查 |
| `http://localhost:9090/rules` | Prometheus 记录与告警规则 |
| `http://localhost:3300` | Grafana 本地仪表盘 |

手工发起受治理的 Context Investigation：

```bash
curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/cases/C-102/context-investigations \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: book-ch04-c102-0001' \
  --data '{
    "question":"C-102 的事故证明是否满足规则要求，适用哪个规则版本，为什么需要人工复核？",
    "purpose":"claim_investigation",
    "as_of":"2026-07-23T12:00:00+08:00",
    "evidence_token_budget":1800,
    "max_rounds":2
  }'
```

关键结果：

```json
{
  "status": "complete",
  "result": {
    "context_pack": {
      "stop_reason": "evidence_sufficient",
      "retrieval_rounds": 1
    },
    "answer": {
      "verdict": "complete",
      "recommended_action": "route_to_human_reviewer",
      "side_effect": "none"
    }
  }
}
```

使用相同幂等键再次请求会返回相同 `run_id` 和 `pack_id`，且 `replayed=true`，不会重新检索和构建 Context Pack。

完整的 Trace、SLO、故障降级与恢复验证见 [第 5 章运行手册](docs/chapter-05-runbook.md)。
安全控制、红队样例与审计查询见 [第 6 章运行手册](docs/chapter-06-runbook.md)。
分层合龙、系统验收与 Runtime Context Graph 见 [第 7 章运行手册](docs/chapter-07-runbook.md)。
Golden Dataset、分层评测与持续回归见 [第 8 章运行手册](docs/chapter-08-runbook.md)。
事故证据包、成本归因与 A2A 恢复演练见 [第 9 章运行手册](docs/chapter-09-runbook.md)。

手工发起系统级合龙运行：

```bash
curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/cases/C-102/system-runs \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: book-ch07-c102-0001' \
  --data '{
    "goal":"合并上下文调查与多专业协作结论，对 C-102 执行系统级一致性验收。",
    "question":"本案适用什么规则，材料是否满足要求，是否触发人工风险复核？",
    "as_of":"2026-07-23T12:00:00+08:00",
    "evidence_token_budget":1800,
    "max_rounds":2
  }'
```

成功响应会返回 `context_graph_uri`。使用同一 API Key 读取该 URI，可以检查每条最终 Claim 的 `SUPPORTED_BY` 证据边。

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

工具安全控制面：

```text
Model / Agent tool intent
  ▼
ToolGuard
  ├─ Tool Manifest + definition digest
  ├─ user ∩ workload ∩ delegation scopes
  ├─ purpose + tenant + resource + audience
  └─ risk + side effect + kill switch
  ├─ deny ──► no execution + minimal decision audit
  └─ allow ──► MCP read-only tool ──► PostgreSQL

Structured result ──► OutputGuard ──► release | redact | block
```

上下文控制面：

```text
Question + Principal + as_of + purpose
  ▼
Governed Query Planner
  ├─ structured
  ├─ PostgreSQL full-text
  └─ allowlisted graph paths
  ▼
RRF → Context Gates → Evidence Sufficiency
  ▼
Versioned Context Pack + Claim-Citation Binding + Context Trace
  ▼
Audit + CloudEvents Outbox
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
- API → A2A → MCP → PostgreSQL 端到端验收；
- Context Pipeline → PostgreSQL FTS / Graph → Context Pack 端到端验收；
- API → A2A → MCP 跨服务 W3C Trace 与 deadline 验收；
- Prometheus SLO 规则、依赖降级和 PostgreSQL 隔离恢复演练。
- ToolGuard 权限交集、Manifest 漂移、跨资源、purpose、kill switch 与未知工具回归；
- PII 最小化、canary 外泄阻断和真实间接提示注入隔离验收。
- 系统 DAG、父子运行、跨团队一致性验收、来源图完整性和系统级幂等重放。
- Golden Dataset、N-run、六层成对比较与发布 Artifact。
- A2A 故障注入、跨层首故障定位、资源归因和恢复后 Goal 复验。

## 设计文档

- [架构说明](docs/architecture.md)
- [ADR-0001：先建立确定性模块化单体](docs/adr/0001-start-with-a-deterministic-modular-monolith.md)
- [ADR-0002：先建设 Agent 控制面，再提高模型自治](docs/adr/0002-control-plane-before-model-autonomy.md)
- [ADR-0003：由 Supervisor 持有收敛权](docs/adr/0003-supervisor-owns-convergence.md)
- [ADR-0004：Context Pack 是受治理的证据产品](docs/adr/0004-context-pack-is-a-governed-evidence-product.md)
- [ADR-0005：把生产级定义为可执行的运行保证](docs/adr/0005-production-is-an-executable-runtime-guarantee.md)
- [ADR-0006：有效权限取用户、工作负载与委托权限的交集](docs/adr/0006-authority-is-an-intersection-not-a-model-judgment.md)
- [ADR-0007：系统合龙使用确定性验收与运行时来源图](docs/adr/0007-system-convergence-needs-deterministic-acceptance.md)
- [ADR-0008：发布需要系统级评测门禁](docs/adr/0008-release-needs-system-level-evaluation-gates.md)
- [ADR-0009：运行结论必须来自跨层因果证据与诚实成本归因](docs/adr/0009-operations-needs-causal-evidence-and-honest-cost-attribution.md)
- [第 2 章运行与验收手册](docs/chapter-02-runbook.md)
- [第 3 章运行与验收手册](docs/chapter-03-runbook.md)
- [第 4 章运行与验收手册](docs/chapter-04-runbook.md)
- [第 5 章运行与恢复验收手册](docs/chapter-05-runbook.md)
- [第 6 章安全与红队验收手册](docs/chapter-06-runbook.md)
- [第 7 章分层合龙与 Context Graph 验收手册](docs/chapter-07-runbook.md)
- [第 8 章系统级评测与发布门禁手册](docs/chapter-08-runbook.md)
- [第 9 章 AgentOps 事故诊断与恢复手册](docs/chapter-09-runbook.md)

## “生产级”的准确含义

本仓库交付的是可运行、可测试、可部署、可观测、可安全演进的生产导向参考实现。它不会声称替任何企业完成以下工作：

- 真实身份提供方、组织权限、非对称签名、轮换撤销和完整 OAuth 2.1 授权服务器接入；
- 指定地区和行业的合规认证；
- 经过真实业务流量校准的容量规划、多副本拓扑和跨区域灾备指标；
- 写工具的业务幂等、效果账本和审批闭环；
- 长任务异步 API、跨进程 Supervisor 恢复与 Broker 消费端；
- 经过领域语料评测的 embedding provider、向量索引和默认 vector channel；
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
| 第 6 章 | ToolGuard、权限交集、隐私与安全审计 | 11 项红队、注入隔离与零越权副作用 |
| 第 7 章 | 分层 DAG、团队合龙、系统验收与 Runtime Context Graph | 7 项一致性检查、来源图与幂等重放 |
| 第 8 章 | Golden Dataset 与持续回归 | 分层评测报告 |
| 第 9 章 | AgentOps、事件诊断与成本治理 | 运营看板与 Runbook |
| 第 10 章 | 系统验收与开源发布 | 发布包、SBOM 与验收报告 |

每章会发布独立 tag；书稿引用 tag 和 commit，而不是复制源码。

## 许可与安全

代码采用 [MIT License](LICENSE)。安全问题请按 [Security Policy](SECURITY.md) 私下报告；请勿在公开 Issue 中披露漏洞细节。
