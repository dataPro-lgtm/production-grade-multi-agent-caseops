# 第 7 章分层合龙与 Context Graph 验收手册

## 1. 本章验证什么

Slice 6 将前六章的上下文、协作、协议、运行保证和安全控制合龙为一个系统级运行。验收重点不是 Agent 数量，而是四件事：

1. 中央层提交的是可验证、无环、带依赖和验收条件的 DAG；
2. 团队只写自己拥有的状态，通过 Artifact 向上交付；
3. 跨团队冲突由确定性规则暴露，不由模型润色；
4. 最终 Claim 可沿 Runtime Context Graph 反查团队、任务和证据。

## 2. 启动

```bash
docker compose up --build -d
docker compose ps
```

`postgres`、`mcp`、`a2a` 和 `api` 应为 `healthy`，`migrate` 应以退出码 0 结束。

## 3. 一键验收

```bash
make acceptance-chapter-07
```

脚本会真实执行：

1. 验证 Alembic revision 为 `0006`；
2. 读取真实 Agent Card，确认 A2A HTTP+JSON 1.0 和三个有界 Skill；
3. 发起一个系统运行；
4. Context Team 生成 Context Pack；
5. Collaboration Team 通过 A2A → MCP → PostgreSQL 执行三个专业委托；
6. Central Supervisor 执行 7 项确定性验收；
7. 查询 Runtime Context Graph，证明每条 Claim 都有 `SUPPORTED_BY` 边；
8. 使用相同幂等键重放，确认不重复创建运行；
9. 查询 PostgreSQL，确认 3 个系统步骤、来源图和至少 6 次 ToolGuard 判定已持久化。

预期输出：

```text
hierarchical orchestration schema accepted: revision 0006
A2A capability snapshot accepted: HTTP+JSON 1.0 and three bounded skills
system convergence accepted: <system-run-id>
runtime Context Graph accepted: nodes=<n> edges=<n>
system-level idempotent replay accepted
durable hierarchy accepted: steps=3 security_decisions=6
chapter 07 hierarchical system acceptance passed
```

## 4. 理解结果

C-102 的正常终态是：

```json
{
  "status": "needs_human",
  "result": {
    "outcome": "SYSTEM_ACCEPTED_WITH_HUMAN_REVIEW",
    "side_effect": "none"
  }
}
```

`needs_human` 不是系统失败。它表示上下文与三个专业节点一致确认：事故证明已满足 `2026.1` 规则，但高金额和短保单期限触发人工风险复核。系统只交付证据和建议，没有拒赔、冻结或发送通知。

## 5. 查看任务 DAG

```bash
docker compose exec -T postgres \
  psql -U caseops -d caseops -c "
    select step_key, owner, depends_on, status, attempt_count, result_ref
    from system_steps
    order by started_at;
  "
```

`context-evidence` 与 `specialist-collaboration` 没有前置依赖；`system-acceptance` 必须等待两者成功。当前本地执行器按稳定顺序运行 ready set，数据库合同不假装已经交付跨进程并行调度。

## 6. 查看 Runtime Context Graph

创建系统运行后，响应会返回：

```json
{
  "context_graph_uri": "/v1/system-runs/<id>/context-graph"
}
```

使用创建运行的同一租户凭据读取：

```bash
curl --fail \
  "http://localhost:8080/v1/system-runs/<id>/context-graph" \
  --header 'X-API-Key: caseops-local-dev-key' |
  python3 -m json.tool
```

图中不返回 Context Pack 正文、Prompt、工具参数或工具结果，只返回稳定引用、责任主体、状态、分类和摘要。业务内容仍由 Context Run、Collaboration Run 和源系统管理。

## 7. 三种图不要混用

| 结构 | 回答的问题 | 当前实现 |
|---|---|---|
| Knowledge Graph | 业务实体之间长期是什么关系 | `knowledge_entities` / `knowledge_relations` |
| Distributed Trace | 这次请求按什么时间顺序调用了哪些服务 | OpenTelemetry / Tempo |
| Runtime Context Graph | 这次结论由哪个计划、团队、证据和门禁形成 | `runtime_context_nodes` / `runtime_context_edges` |

Trace ID 可以作为来源图的扩展引用，但不能替代 Claim 到 Evidence 的支持关系。

## 8. 为什么没有启用 MCP Tasks

MCP 2025-11-25 引入的 Tasks 仍是实验能力，适用于昂贵计算、批处理和其他需要轮询或延迟取回结果的工作。CaseOps 当前五个工具是有界、同步、只读数据库查询；强行包装为 MCP Task 只会增加第二套生命周期。

本章把耐久任务放在业务层的 `system_runs`、`system_steps`、`collaboration_runs` 和 `delegated_tasks`。未来出现长时工具时，必须先通过 MCP capability negotiation，并明确 MCP Task 与业务任务账本的映射。

## 9. 当前边界

- DAG 已持久化并验证无环，但当前 API 进程按稳定顺序执行 ready set；
- 失败后使用原幂等键可复用已完成子运行，但尚无跨进程租约、心跳和工作窃取；
- 能力快照记录当前已验证的 A2A/MCP 边界；生产系统还需要快照过期、重新发现和协议兼容策略；
- Runtime Context Graph 是来源索引，不是通用图数据库；
- 系统验收规则只覆盖 C-102 当前主张。新增领域主张必须新增显式映射、失败语义和 Golden Dataset。

## 10. 停止

```bash
docker compose down
```

不要添加 `--volumes`，除非明确需要删除本地 PostgreSQL 数据。
