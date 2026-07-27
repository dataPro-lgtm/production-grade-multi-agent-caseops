# 第 5 章运行与恢复验收手册

## 1. 启动应用与可观测性栈

```bash
docker compose \
  -f compose.yaml \
  -f deploy/compose.observability.yaml \
  up --build -d

docker compose \
  -f compose.yaml \
  -f deploy/compose.observability.yaml \
  ps
```

`postgres`、`mcp`、`a2a`、`api`、`otel-collector`、`tempo`、`prometheus` 和 `grafana` 应为 `healthy`；`migrate` 应以退出码 0 结束。

本地入口：

| 地址 | 运行证据 |
|---|---|
| `http://localhost:8080/health/live` | API 进程存活 |
| `http://localhost:8080/health/startup` | 数据库迁移版本可证明 |
| `http://localhost:8080/health/ready` | 关键与可选依赖状态 |
| `http://localhost:8080/metrics` | 低基数 Prometheus 指标 |
| `http://localhost:3200/ready` | Tempo 就绪 |
| `http://localhost:9090/rules` | SLO 记录规则与告警 |
| `http://localhost:3300` | Grafana 仪表盘，账号 `admin/caseops-local` |

Grafana 密码仅供本地演练。生产环境必须从秘密管理系统注入。

## 2. 一键执行第 5 章验收

```bash
make acceptance-chapter-05
```

脚本不是静态配置检查，而会执行：

1. 验证 liveness、startup 和依赖感知 readiness；
2. 用过期 deadline 请求确认入口返回 `408`；
3. 发起 C-102 三专业 Agent 协作，并固定一个 W3C `traceparent`；
4. 等待 Tempo 出现 API、A2A、MCP 三个服务的完整 Trace；
5. 从 Prometheus API 验证错误率记录规则、快速燃烧告警和关键依赖告警；
6. 停止 A2A 服务，确认 API 保持存活且 readiness 变为 `degraded`；
7. 重新启动 A2A；
8. 创建 PostgreSQL custom-format 备份，在隔离数据库恢复；
9. 比较源库与恢复库内容签名、Alembic revision 和 `tenant-demo/C-102` 业务不变量。

一次实测结果：

```text
cross-service W3C trace accepted: caseops-a2a, caseops-api, caseops-mcp
SLO recording and actionable alert rules accepted
optional dependency degradation accepted without failing liveness
isolated PostgreSQL restore accepted: rto=6s
```

`6s` 是开发机上本次小数据集的实测恢复时间，不是生产 RTO 承诺。

## 3. 手工验证 deadline 与 Trace

```bash
TRACE_ID=11111111111111111111111111111115

curl --fail-with-body \
  --request POST \
  http://localhost:8080/v1/cases/C-102/collaboration-runs \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: caseops-local-dev-key' \
  --header 'Idempotency-Key: book-ch05-manual-0001' \
  --header "traceparent: 00-${TRACE_ID}-2222222222222215-01" \
  --header 'X-Request-Timeout-Ms: 20000' \
  --data '{"goal":"在统一截止时间和 Trace 上核对规则、材料与风险证据。"}' \
  --dump-header -

curl --fail --silent \
  "http://localhost:3200/api/traces/${TRACE_ID}"
```

响应应包含 `X-Trace-ID`、`traceparent` 与绝对时间格式的 `X-Request-Deadline`。Tempo Trace 中应同时出现 `caseops-api`、`caseops-a2a` 和 `caseops-mcp`。

## 4. 手工执行备份与隔离恢复

```bash
mkdir -p artifacts/recovery
scripts/backup-postgres.sh artifacts/recovery

scripts/restore-drill-chapter-05.sh \
  artifacts/recovery/caseops-<UTC_TIMESTAMP>.dump
```

备份目录会得到 `.dump` 与 `.manifest.json`。恢复脚本使用固定且受控的隔离库 `caseops_restore_drill_ch05`，结束后自动删除，不覆盖源数据库。

报告中的 `content_signature` 证明选定核心表的有序内容一致；它不替代业务级抽样、附件对象存储校验和跨区域恢复演练。

## 5. 验证平台配置

```bash
docker compose \
  -f compose.yaml \
  -f deploy/compose.observability.yaml \
  config --quiet

docker run --rm \
  --entrypoint /bin/promtool \
  -v "$PWD/deploy:/etc/caseops:ro" \
  prom/prometheus:v3.12.0 \
  check rules /etc/caseops/caseops.rules.yml
```

## 6. 本地质量与安全门禁

```bash
make PYTHON=.venv/bin/python verify
make PYTHON=.venv/bin/python security
```

## 7. 停止

```bash
docker compose \
  -f compose.yaml \
  -f deploy/compose.observability.yaml \
  down
```

普通停止不添加 `--volumes`，避免删除 PostgreSQL、Tempo、Prometheus 和 Grafana 的本地持久卷。

