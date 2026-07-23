# 第 2 章运行与验收手册

## 1. 启动

```bash
docker compose up --build -d
docker compose ps
```

`postgres`、`mcp`、`api` 应为 `healthy`；`migrate` 正常退出码为 0。

## 2. 一键验收

```bash
make acceptance
```

脚本会执行一次新 Agent run，再使用相同幂等键重放。验收条件：

- 首次请求 `status=completed`；
- 工具步数为 4；
- 结果为 `DOCUMENTS_COMPLETE_AFTER_NORMALIZATION`；
- 归一出的代码为 `ACCIDENT_CERTIFICATE`；
- 第二次请求返回同一个 `run_id` 且 `replayed=true`。

## 3. 手工请求

```bash
curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/cases/C-102/agent-runs \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: book-ch02-manual-0001' \
  --data '{"goal":"判断案件材料是否满足其绑定规则，并给出可追溯结论。"}'
```

## 4. 查看工具账本

先从响应复制 `run_id`：

```bash
docker compose exec -T postgres \
  psql -U caseops -d caseops -P pager=off \
  -c "select tool_name,status,attempt_count from tool_executions where run_id='<RUN_ID>' order by started_at;"
```

预期依次出现四个 `succeeded`：

1. `caseops_get_case_snapshot`
2. `caseops_get_policy_requirements`
3. `caseops_list_unclassified_documents`
4. `caseops_resolve_document_alias`

## 5. 验证 MCP 未授权拒绝

```bash
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  --request POST \
  http://localhost:8081/mcp \
  --header 'Accept: application/json, text/event-stream' \
  --header 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"manual","version":"1.0"}}}'
```

预期 HTTP 状态为 `401`。任务令牌只由 API 内部签发，不通过命令行暴露。

## 6. 本地质量门禁

```bash
make verify
make security
```

若没有激活虚拟环境，可显式执行：

```bash
make PYTHON=.venv/bin/python verify
make PYTHON=.venv/bin/python security
```

## 7. 真实模型模式

默认 Docker 配置使用 `conformance` planner，因此验收不依赖公网。要启用 Responses API，需要为 API 服务设置：

```text
CASEOPS_AGENT_PLANNER=openai
CASEOPS_OPENAI_API_KEY=<secret>
CASEOPS_OPENAI_MODEL=gpt-5.6-terra
CASEOPS_OPENAI_REASONING_EFFORT=low
```

不要把 API Key 写入仓库或 Compose 文件。没有完成在线调用和评测时，只能声明模型适配器通过契约测试，不能声明真实模型链路已验收。

## 8. 停止

```bash
docker compose down
```

只有明确要删除本地数据库时，才执行 `docker compose down --volumes`。
