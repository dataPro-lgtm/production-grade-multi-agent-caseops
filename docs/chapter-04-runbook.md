# 第 4 章运行与验收手册

## 1. 启动完整链路

```bash
docker compose up --build -d
docker compose ps
```

`postgres`、`mcp`、`a2a`、`api` 应为 `healthy`；`migrate` 应以退出码 0 正常结束。

## 2. 一键验收

```bash
make acceptance-chapter-04
```

脚本验证：

- API 构建结构化、全文和关系检索计划；
- PostgreSQL 中存在全文 GIN 索引和八条受约束关系；
- Context Pack 只包含通过 scope、purpose、as-of、hash 和预算门禁的证据；
- 过期的 `2025.4` 规则被标记为 `rejected_temporal`；
- 带有越权指令的外部邮件被标记为 `rejected_untrusted_instruction`；
- 三条最终 Claim 均绑定 Pack 内 Evidence ID；
- 最终结果为 `complete`，`side_effect=none`；
- 相同幂等键返回同一个 `pack_id`，不会重新构建；
- Context Run、审计和 CloudEvents Outbox 同事务持久化。

## 3. 手工发起 Context Investigation

```bash
curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/cases/C-102/context-investigations \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: book-ch04-manual-0001' \
  --data '{
    "question": "C-102 的事故证明是否满足规则要求，适用哪个规则版本，为什么需要人工复核？",
    "purpose": "claim_investigation",
    "as_of": "2026-07-23T12:00:00+08:00",
    "evidence_token_budget": 1800,
    "max_rounds": 2
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

## 4. 检查来源、图关系与运行证据

从响应复制 `run_id`：

```bash
docker compose exec -T postgres \
  psql -U caseops -d caseops -P pager=off \
  -c "select source_id,owner_team,classification,active from knowledge_sources order by source_id;" \
  -c "select relation_id,relation_type,path_template from knowledge_relations order by relation_id;" \
  -c "select id,status,jsonb_array_length(trace::jsonb) as trace_events from context_runs where id='<RUN_ID>';" \
  -c "select topic,payload->>'specversion' as specversion from outbox_events where aggregate_id='<RUN_ID>';"
```

## 5. 验证门禁、充分度与幂等

```bash
.venv/bin/pytest tests/test_context_pipeline.py -q
```

覆盖：

- 过期、越权、非可信指令、hash 失配和预算门禁；
- RRF 多通道融合；
- 低权限主体只能得到 `insufficient_evidence`；
- Claim-Citation Binding；
- 幂等重放与幂等键冲突。

## 6. 本地质量与安全门禁

```bash
make PYTHON=.venv/bin/python verify
make PYTHON=.venv/bin/python security
```

## 7. 停止

```bash
docker compose down
```

普通停止不添加 `--volumes`，避免删除本地 PostgreSQL 数据。
