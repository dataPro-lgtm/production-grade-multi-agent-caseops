# 第 9 章运行手册：AgentOps 事故诊断、成本归因与恢复

本手册验收 CaseOps Slice 8。目标不是制造一张“全绿”的监控大盘，而是回答三个生产问题：

1. A2A 依赖中断后，能否从持久化事实定位首个失败点；
2. 能否区分业务顶层症状、系统步骤与失败的委托任务；
3. 恢复依赖后，能否用一条新运行证明系统重新收敛。

## 1. 交付边界

| 交付物 | 路径 | 责任 |
|---|---|---|
| 运行评估契约 | `src/caseops/operations/contracts.py` | 固定影响、首故障、时间线、证据、成本和控制建议 |
| 诊断服务 | `src/caseops/operations/service.py` | 跨系统运行、步骤、委托任务、Context Graph 与安全审计重建因果链 |
| 数据迁移 | `migrations/versions/0007_add_operational_assessments.py` | 持久化评估快照与成本事件 |
| API | `POST /v1/system-runs/{id}/operational-assessments` | 显式、幂等、租户隔离地生成评估 |
| GameDay | `scripts/acceptance-chapter-09.sh` | 真实停止 A2A、恢复并复验 |
| 证据报告 | `artifacts/operations/chapter-09-gameday.json` | 汇总基线、事故与恢复三份评估 |

评估接口只接受 `completed`、`needs_human` 或 `failed` 运行。它不会读取或复制原始 Prompt、工具参数和业务载荷，只输出稳定引用、数量、摘要和版本。

## 2. 一键验收

```bash
docker compose up --build --wait --wait-timeout 120 -d
make acceptance-chapter-09
```

脚本会依次执行：

1. 验证数据库 revision 为 `0007`；
2. 完成一条健康系统运行，并生成基线评估；
3. 停止 `a2a` 容器；
4. 在同一生产路径发起新运行；
5. 恢复 A2A 并等待健康检查通过；
6. 为失败运行生成 Incident Bundle；
7. 再执行一条系统运行，确认恢复后重新收敛；
8. 验证三份评估、十二条成本事件和三条审计记录已经持久化。

脚本注册了退出恢复钩子；即使中途断言失败，也会尝试重新启动 A2A。

预期关键输出：

```text
operations schema accepted: revision 0007
healthy baseline accepted with durable evidence and honest unit attribution
incident bundle accepted: first failure=collaboration, safe side effects=0, wasted context unit=1
post-recovery run accepted: A2A readiness and goal convergence restored
durable operations evidence accepted: assessments=3 cost_events=12 audits=3
chapter 09 AgentOps GameDay acceptance passed
```

## 3. 如何阅读事故证据包

A2A 中断时，顶层 HTTP 请求仍然可能返回 `201`。这不是“系统健康”，而是协作层把依赖失败收敛成了结构化结果：三个委托任务失败，系统验收安全地产生 `SYSTEM_REJECTED`，外部副作用仍为零。

因此诊断顺序必须是：

```text
system run status
  → system steps
  → collaboration run
  → delegated tasks
  → first failed task
  → referenced evidence and timestamps
```

本次演练的稳定断言是：

- 运行状态：`failed`；
- 首故障层：`collaboration`；
- 首故障步骤：`specialist-collaboration`；
- 失败委托任务数：3；
- 外部副作用数：0；
- 已完成但未形成成功目标的 Context Run：1。

最后一项是“浪费工作”而不是货币成本。当前 conformance 模式没有计费模型调用，也没有供应商价格版本，因此报告中的货币字段必须为 `null`。

## 4. 手工生成运行评估

先从系统运行响应取得 `system_run_id`，再执行：

```bash
curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/system-runs/SYSTEM_RUN_ID/operational-assessments \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: manual-operations-assessment-0001'
```

相同租户使用相同幂等键重放时返回同一 `assessment_id` 和 `report_digest`；如果把这个键用于另一个运行，接口返回 `409 IDEMPOTENCY_KEY_REUSED`。另一租户访问同一运行时返回 `404`，不会泄露对象是否存在。

## 5. 验收证据查询

```bash
docker compose exec -T postgres psql -U caseops -d caseops -c \
  "select id, system_run_id, status, severity, report_digest, created_at
   from operational_assessments
   order by created_at desc
   limit 10;"
```

```bash
docker compose exec -T postgres psql -U caseops -d caseops -c \
  "select system_run_id, resource_type, quantity, unit, attribution,
          monetary_cost_microunits
   from operational_cost_events
   order by created_at desc
   limit 20;"
```

CI 会同时上传第 8 章评测报告和第 9 章 GameDay 报告。任何一次发布都必须同时回答“质量是否回归”和“发生依赖故障后是否还能安全诊断与恢复”。
