# 第 3 章运行与验收手册

## 1. 启动完整链路

```bash
docker compose up --build -d
docker compose ps
```

`postgres`、`mcp`、`a2a`、`api` 应为 `healthy`；`migrate` 应以退出码 0 正常结束。

服务地址：

| 地址 | 用途 |
|---|---|
| `http://localhost:8080/docs` | CaseOps OpenAPI |
| `http://localhost:8081/mcp` | 受任务令牌保护的 MCP 工具面 |
| `http://localhost:8082/.well-known/agent-card.json` | A2A Agent Card |
| `http://localhost:8082/a2a/rest` | A2A 1.0 HTTP+JSON 接口 |

## 2. 一键验收

```bash
make acceptance-chapter-03
```

脚本验证：

- Agent Card 声明三个专业技能和 A2A 1.0；
- 未携带任务令牌的 A2A 操作返回 401；
- API → A2A → MCP → PostgreSQL 全链路成功；
- coverage、document、risk 三个任务均持久化并成功；
- Join 返回 `COMPLETE_WITH_REVIEW_REQUIRED`；
- `side_effect=none`；
- 相同幂等键不会重新派发专业 Agent；
- 运行产生 3 个业务任务和 5 个 CloudEvents Outbox 事件。

## 3. 手工发起协作运行

```bash
curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/cases/C-102/collaboration-runs \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: book-ch03-manual-0001' \
  --data '{
    "goal": "并行核对案件规则、材料完整性与风险信号，通过证据合同形成可追溯的协作结论。"
  }'
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

## 4. 检查业务任务、A2A Task 与事件

从响应复制 `run_id`：

```bash
docker compose exec -T postgres \
  psql -U caseops -d caseops -P pager=off \
  -c "select specialist_id,status,attempt_count from delegated_tasks where run_id='<RUN_ID>' order by specialist_id;" \
  -c "select count(*) as a2a_tasks from a2a_tasks;" \
  -c "select topic,count(*) from outbox_events where aggregate_id='<RUN_ID>' group by topic order by topic;"
```

业务任务和 A2A Task 不能混为一张表：前者记录 CaseOps 的责任与验收，后者记录协议生命周期。

## 5. 验证部分失败与冲突

确定性 Join 的回归测试：

```bash
.venv/bin/pytest tests/test_collaboration.py -q
```

覆盖三类场景：

- 全部成功且风险门禁要求人工复核；
- 可选风险节点失败，结果显式降级为 `PARTIAL_EVIDENCE`；
- 两个 Agent 对同一 Claim 给出不同值，结果为 `CONFLICT_REQUIRES_HUMAN`。

## 6. 本地质量与安全门禁

```bash
make PYTHON=.venv/bin/python verify
make PYTHON=.venv/bin/python security
```

## 7. 停止

```bash
docker compose down
```

不要在普通停止时添加 `--volumes`，否则会删除本地 PostgreSQL 数据。
