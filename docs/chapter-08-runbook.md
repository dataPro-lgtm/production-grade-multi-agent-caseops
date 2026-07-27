# 第 8 章运行手册：Golden Dataset、分层评测与持续回归

本手册验收 CaseOps Slice 7。目标不是得到一个漂亮的总分，而是回答一个可追责的问题：

> 相对于 v0.7.0 基线，v0.8.0 是否仍能沿正确路径、使用充分证据、守住租户与副作用边界，并稳定收敛？

## 1. 交付物

| 交付物 | 位置 | 责任 |
|---|---|---|
| Golden Dataset | `src/caseops/evaluation/golden_cases.json` | 场景、风险、N-run 与预期 |
| Quality Contract | `src/caseops/evaluation/quality_contract.json` | 硬门禁和资源预算 |
| 冻结基线 | `src/caseops/evaluation/baseline_v0.7.0.json` | 成对非回归参照 |
| 分层 grader | `src/caseops/evaluation/evaluators.py` | 契约、结果、路径、证据、安全、效率 |
| Live runner | `src/caseops/evaluation/runner.py` | 真实 HTTP 执行、汇总与退出码 |
| 发布验收 | `scripts/acceptance-chapter-08.sh` | 生成报告并执行最终断言 |

## 2. 为什么是 5 个场景、13 次运行

| Case | N | 关键风险 |
|---|---:|---|
| GD-001 | 3 | 标准高风险案件能否完整收敛 |
| GD-002 | 3 | 最小证据预算下是否守住结果与证据合同 |
| GD-003 | 3 | 恶意目标能否扩大权限或产生副作用 |
| GD-004 | 2 | 完全相同请求能否安全重放 |
| GD-005 | 2 | 变更后的请求能否复用既有幂等键 |

13 次不是统计学上的通用样本量。它是当前确定性 conformance 模式的发布合同：用于发现状态、排序、幂等和控制面漂移。接入随机模型后，应按目标差异、方差与错误成本重新做样本量设计。

## 3. 一键验收

```bash
docker compose up --build -d
make acceptance-chapter-08
```

成功输出：

```text
PASS: 5/5 cases, 13 trials, semantic drift cases=0
PASS GD-001: N=3 consistency=1.000
PASS GD-002: N=3 consistency=1.000
PASS GD-003: N=3 consistency=1.000
PASS GD-004: N=2 consistency=1.000
PASS GD-005: N=2 consistency=1.000
```

报告位于：

```text
artifacts/evaluation/chapter-08-report.json
```

这是运行产物，不提交到 Git。CI 会以 Workflow Artifact 形式保留 14 天。

## 4. 如何读报告

先看 `summary.release_decision`，再按以下顺序下钻：

1. `cases[].passed`：哪个 Golden case 阻断发布；
2. `cases[].regressions`：相对 v0.7.0 哪一层下降；
3. `trials[].layers[].findings`：哪个不变量被破坏；
4. `trials[].diagnostics`：步数、Claim、Evidence、图节点与边的实测值；
5. `semantic_fingerprint`：同一 case 的语义形状是否漂移。

wall-clock 耗时只做诊断。共享 CI 的机器噪声不会阻断发布；目标环境的延迟 SLO 由 Prometheus 和第 9 章 AgentOps 负责。

## 5. 故障注入

最直接的 evaluator 自检是删除一条 Claim 的 `SUPPORTED_BY` 边：

```bash
.venv/bin/python -m pytest \
  tests/test_evaluation_pipeline.py::EvaluationEngineTest::test_missing_supported_by_edge_blocks_evidence_gate \
  -q
```

这个测试必须证明 `evidence` 层失败。若修改 evaluator 后它仍然通过，说明 scorer 已经失去区分力，不能合并。

## 6. 发布判定

当前全部六层都是硬门禁：

- `contract`：HTTP 与响应结构；
- `outcome`：系统状态、结论、下一动作与副作用；
- `path`：三步 DAG、依赖、尝试次数与七项系统验收；
- `evidence`：Claim、Evidence 和 Runtime Context Graph 支撑关系；
- `security`：零副作用与跨租户图访问 404；
- `efficiency`：步骤数和尝试次数不超过声明预算。

模型 Judge 不是硬门禁。开放式解释质量需要模型评分时，必须另行记录 judge model、rubric、顺序随机化、重复次数、人工校准集与弃权策略。
