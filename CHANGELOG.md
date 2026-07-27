# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/)。章节标签是不可变的学习快照，正式版本记录可消费的工程里程碑。

## [1.0.0] - 2026-07-27

### Added

- 从 Slice 0 到 Slice 9 的完整 CaseOps 纵向切片；
- API、PostgreSQL、迁移、MCP、A2A、分层 Supervisor 与 Runtime Context Graph；
- 上下文治理、纵深安全、系统级 Golden Dataset、AgentOps 因果诊断与 GameDay；
- 带哈希的运行时和开发依赖锁文件；
- 干净源码归档、非 root 镜像、SPDX SBOM、发布证据清单与 SHA-256 校验；
- GitHub Release、GHCR 镜像、构建来源证明和 SBOM 证明自动化。

### Security

- 工具权限默认拒绝，授权取用户、工作负载与委托 scope 的交集；
- 发布镜像固定 Python 基础镜像 digest；
- 真实客户数据、生产凭据和模型供应商密钥均不属于仓库交付物。

### Known limits

- API Key 与 HS256 任务令牌仅供本地参考环境；
- 确定性 conformance 模式不生成真实模型 token 或货币成本；
- 来源证明与 SBOM 不替代部署级威胁评估、漏洞管理和合规审查。

[1.0.0]: https://github.com/dataPro-lgtm/production-grade-multi-agent-caseops/releases/tag/v1.0.0
