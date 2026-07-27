# ADR-0006：有效权限取用户、工作负载与委托权限的交集

- 状态：Accepted
- 日期：2026-07-27
- 决策者：CaseOps maintainers

## 背景

多 Agent 系统有三种容易被混为一谈的权限：用户原本拥有什么权限、执行该任务的工作负载被允许做什么、Supervisor 本次究竟委托了什么。只校验其中一层会产生权限放大：高权限用户可能让低信任工作负载读取过多数据，通用工作负载可能越过本次任务边界，子 Agent 也可能把父任务的权限继续扩大后转授。

Prompt、模型角色和 Agent Card 都只能表达意图或能力，不能成为授权证据。即使模型被间接提示注入控制，执行面仍须在模型之外做确定性判定。

## 决策

CaseOps 在 MCP 工具执行前设置统一的 `ToolGuard` 策略执行点，并采用以下有效权限公式：

```text
effective_scopes = user_scopes ∩ workload_scopes ∩ delegation_scopes
```

一次工具调用只有同时满足下列条件才会执行：

1. 工具存在版本化 Security Manifest，且 Manifest 摘要与运行时 Tool Definition 一致；
2. 工具已启用并位于运行时 allowlist；
3. Manifest 声明的 purpose、resource type、resource ID 与令牌和参数一致；
4. 工具要求的 scope 位于三层权限交集；
5. 工具风险、scope 和副作用声明没有发生漂移；
6. 当前切片只允许 `read_only + side_effect=none`。

短期任务令牌绑定 issuer、audience、tenant、subject、task、workload、purpose、resource 与三层 scope。签发器拒绝任何超出用户权限的委托；MCP 服务不接受模型传入 `tenant_id`。

每次允许或拒绝都写入最小化的 `security_decisions`：保存稳定原因码、策略与 Manifest 摘要、上下文摘要和参数哈希，不保存原始 Prompt、工具参数或业务载荷。

## 后果

### 正向

- 模型、Supervisor 或子 Agent 无法靠自然语言扩大权限；
- 工具描述、版本、风险或 scope 漂移会默认拒绝，而不是静默放行；
- 同一策略同时保护进程内测试路径和真实 MCP 路径；
- 决策记录足以支持回归、取证和策略版本对比，同时减少敏感数据留存。

### 代价

- 令牌和工具 Manifest 必须随任务、工具版本和业务用途同步维护；
- 默认拒绝会把配置漂移暴露为可用性故障，需要明确的发布检查；
- 当前本地 STS 使用共享密钥，仅用于可运行参考环境；生产环境仍需企业 IdP、非对称密钥、轮换、撤销和 OAuth 2.1 资源服务器元数据。

## 被拒绝的方案

- **让模型判断自己是否有权调用工具**：模型输出不是可信授权证据，且会受到提示注入影响。
- **只检查 API 用户权限**：忽略工作负载和本次委托边界，容易形成 confused deputy。
- **只在工具描述中声明“不要越权”**：描述有助于规划，但不能阻止执行。
- **记录完整 Prompt 和工具参数便于审计**：扩大敏感数据暴露面；当前审计只保留完成调查所需的最小字段和不可逆摘要。
