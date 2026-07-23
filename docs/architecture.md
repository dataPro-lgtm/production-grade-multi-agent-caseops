# CaseOps 架构说明

## 当前边界

Slice 0 是一个可部署的模块化单体。它包含真实 API、事务数据层与运维接口，但调查决策保持确定性；这个选择为后续 Agent 化建立可比较的正确性基线。

```text
HTTP API
  ├─ API Key → Principal → tenant_id
  ├─ request_id / idempotency_key
  └─ typed request and problem details
          │
          ▼
Investigation Service
  ├─ idempotency replay/conflict
  ├─ deterministic domain use case
  └─ one database transaction
          │
          ├─ investigation result
          ├─ audit event
          └─ outbox event
          │
          ▼
PostgreSQL
```

## 依赖方向

- `domain.py` 不依赖 FastAPI、SQLAlchemy 或模型 SDK；
- `application.py` 只依赖领域对象和仓储端口；
- `infrastructure/` 实现数据库适配；
- `service.py` 管理事务语义和幂等；
- `api/` 负责协议、鉴权、错误映射和遥测。

这条依赖方向允许后续把模型、MCP 和多 Agent 编排作为新适配器或应用能力加入，而不改写案件和规则的业务真相。

## 已实现的生产属性

| 属性 | 当前实现 |
|---|---|
| 租户边界 | API Key 映射可信 Principal；查询条件强制包含 tenant_id |
| 幂等 | tenant_id + idempotency_key 唯一约束；请求哈希检测冲突 |
| 事务 | 结果、审计、Outbox 同事务提交 |
| 副作用 | Slice 0 仅生成草稿；没有发送端口 |
| 错误契约 | `application/problem+json` 风格的稳定错误结构 |
| 数据演进 | Alembic 版本化迁移 |
| 可观测性 | JSON 日志、request_id、Prometheus 指标、健康检查 |
| 运行隔离 | 非 root、只读文件系统、capabilities 全移除 |
| 质量门禁 | Ruff、Mypy、pytest、覆盖率、Bandit、CI |

## 尚未声称完成

- API Key 是本地与服务间参考鉴权，企业 OIDC/JWT 与细粒度 ABAC 在安全章节接入；
- Outbox 已原子落库，可靠发布 Worker 在工具与基础设施章节实现；
- 当前未提供高可用拓扑、备份恢复证明和容量报告；
- 当前没有模型参与调查，不存在“Agent 已上线”的表述。

这些限制是显式演进项，不以 Prompt 或文档承诺代替代码。
