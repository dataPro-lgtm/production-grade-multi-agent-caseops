# CaseOps

CaseOps 是《生产级多智能体系统：从架构判断到工程落地》的独立代码项目。它围绕保险理赔协作案例 C-102 逐章演进：先建立确定性业务与安全基线，再逐步加入工具状态机、MCP、多 Agent 协作、企业知识系统、可观测性、安全、评测和 AgentOps。

这不是“几段 Prompt + 一个聊天页面”的演示项目。仓库从 Slice 0 开始就按照可部署服务建设：API、PostgreSQL、迁移、租户边界、幂等、审计、Outbox、指标、健康检查、容器与 CI 都属于代码交付的一部分。

## 当前里程碑：Slice 0

Slice 0 运行 C-102 的确定性调查：

1. 从鉴权结果获得可信租户身份；
2. 在租户边界内读取案件；
3. 锁定案件提交时适用的规则版本；
4. 判断缺失材料；
5. 生成“仅草稿”的补件通知；
6. 在同一事务中保存结果、审计事件与 Outbox 事件。

这一阶段故意不调用大模型。它为后续 Agent 方案提供正确性、延迟、成本和安全基线。只有评测证明动态决策确有收益，系统才提高自治等级。

## 一键运行

环境要求：

- Docker 27+；
- Docker Compose v2。

启动 API 与 PostgreSQL：

```bash
docker compose up --build -d
docker compose ps
```

运行案件调查：

```bash
curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/cases/C-102/investigations \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: book-ch01-c102-0001' \
  --data '{"notification_action":"draft"}'
```

关键结果：

```json
{
  "replayed": false,
  "result": {
    "decision": {
      "code": "MISSING_REQUIRED_DOCUMENTS"
    },
    "missing_documents": [
      {
        "code": "ACCIDENT_CERTIFICATE",
        "name": "事故证明"
      }
    ],
    "recommended_action": {
      "type": "draft_notification",
      "execution_policy": "human_approval_required",
      "side_effect": "none"
    }
  }
}
```

使用相同幂等键再次请求会返回同一 `investigation_id`，且 `replayed` 为 `true`，数据库不会新增调查、审计或 Outbox 记录。

运行接口：

| 地址 | 用途 |
|---|---|
| `http://localhost:8080/docs` | OpenAPI 交互文档 |
| `http://localhost:8080/health/live` | 进程存活检查 |
| `http://localhost:8080/health/ready` | 数据库就绪检查 |
| `http://localhost:8080/metrics` | Prometheus 指标 |

停止服务：

```bash
docker compose down
```

只有明确需要删除本地数据库数据时，才执行 `docker compose down --volumes`。

## 本地开发

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

alembic upgrade head
caseops seed
uvicorn caseops.api.app:app --reload --port 8080
```

默认本地配置使用 SQLite，便于快速开发；Docker Compose 与 CI 的迁移验证使用 PostgreSQL。`production` 环境配置会拒绝 SQLite 和内置开发 API Key。

## 质量验证

```bash
make verify
make security
```

质量门禁包括：

- Ruff 代码规范与格式；
- Mypy strict 类型检查；
- pytest 单元、API 集成和 OpenAPI 契约测试；
- 分支覆盖率门槛；
- Bandit 静态安全扫描；
- PostgreSQL 迁移与种子数据验证；
- 运行镜像构建。

## 架构

```text
HTTP API
  │  authentication · request_id · idempotency
  ▼
Application Service
  │  transaction · audit · outbox
  ▼
Deterministic Domain Kernel
  │  ports
  ▼
SQLAlchemy Adapters ── PostgreSQL
```

领域层不依赖 FastAPI、SQLAlchemy 或任何模型 SDK。后续 Agent、MCP 和多 Agent 编排会通过应用能力与适配器加入，不会替换案件、规则、权限和事务这些确定性边界。

详细说明见 [架构文档](docs/architecture.md) 和 [ADR-0001](docs/adr/0001-start-with-a-deterministic-modular-monolith.md)。

## “生产级”的准确含义

本仓库交付的是可运行、可测试、可部署、可观测、可安全演进的生产导向参考实现。它不会声称替任何企业完成以下工作：

- 真实身份提供方与组织权限模型接入；
- 指定地区和行业的合规认证；
- 经过实测的容量规划、SLO 与灾备指标；
- 针对真实数据和工具的安全评审。

这些能力会在后续章节通过代码和运行证据继续加入。在证据出现之前，README 会把它们写成限制，不写成能力。

## 十章演进路线

| 章节 | 工程增量 | 验收证据 |
|---|---|---|
| 第 1 章 | 确定性内核、API、事务、审计、Outbox | 基线测试与端到端请求 |
| 第 2 章 | 工具状态机、MCP、超时与恢复 | 故障注入与恢复测试 |
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
