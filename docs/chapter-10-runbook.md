# 第 10 章运行手册：系统终审与可验证开源交付

本章的 Slice 9 不增加新的业务智能，而是关闭交付证据链。验收对象是一个精确 Git 提交，而不是维护者当前机器上的工作目录。

## 1. 本地前置条件

- Docker 27+ 与 Docker Compose v2；
- Python 3.12；
- 已完成第 8、9 章的系统评测和 GameDay，证据文件位于 `artifacts/`；
- 工作区所有源代码已提交。

```bash
git status --short
docker compose up --build --wait
make acceptance-chapter-08
make acceptance-chapter-09
make acceptance-chapter-10
```

最后一个命令会执行：

```text
Git commit
  -> git archive 干净源码
  -> 锁定依赖与固定基础镜像构建
  -> 非 root 运行时与 OCI 身份检查
  -> Syft 生成 SPDX 2.3 JSON SBOM
  -> 发布证据清单
  -> SHA-256 完整性复核
```

成功终态应包含：

```text
chapter 10 accepted: clean archive -> pinned image -> non-root runtime
-> SPDX SBOM -> checksummed release evidence
```

## 2. 检查发布证据

```bash
ls -1 artifacts/release
python -m json.tool artifacts/release/release-evidence.json
python -m json.tool artifacts/release/caseops-v1.0.0.spdx.json >/dev/null
```

`release-evidence.json` 必须至少绑定：

- `source.commit`：40 位 Git SHA；
- `runtime.digest`：本次构建镜像的 SHA-256 身份；
- `runtime.database_revision`：`0007`；
- OpenAPI、发布契约、系统评测、GameDay 与 SBOM 的文件摘要；
- 消费者可直接执行的校验命令；
- 来源证明不能替代安全评估的边界声明。

## 3. GitHub v1.0 发布链

正式发布只由精确标签触发：

```bash
git tag -a v1.0.0 -m "CaseOps v1.0.0"
git push origin v1.0.0
```

`.github/workflows/release.yml` 会重新运行代码、安全、系统评测、GameDay 与干净环境门禁；全部通过后才会：

1. 推送 `ghcr.io/datapro-lgtm/production-grade-multi-agent-caseops:1.0.0`；
2. 保存不可变镜像 digest；
3. 生成并发布 SPDX SBOM；
4. 为镜像生成构建来源证明和 SBOM 证明；
5. 为下载包生成来源证明；
6. 创建 GitHub Release。

## 4. 消费者验证

下载发布包后先验证来源，再验证包内文件：

```bash
gh attestation verify \
  caseops-v1.0.0-release.tar.gz \
  --repo dataPro-lgtm/production-grade-multi-agent-caseops

tar -xzf caseops-v1.0.0-release.tar.gz
sha256sum --check SHA256SUMS
```

验证容器：

```bash
gh attestation verify \
  oci://ghcr.io/datapro-lgtm/production-grade-multi-agent-caseops:v1.0.0 \
  --repo dataPro-lgtm/production-grade-multi-agent-caseops
```

验证通过说明下载物或镜像与指定仓库的 GitHub Actions 构建身份匹配，不代表其没有漏洞。生产部署仍需独立完成身份接入、密钥管理、网络边界、数据治理与威胁评估。

## 5. 常见失败与判定

| 失败 | 含义 | 正确处理 |
|---|---|---|
| 工作区不干净 | 验收对象不是稳定提交 | 提交或明确撤销变更后重跑 |
| 标签与包版本不一致 | 发布身份分叉 | 修正版本，不移动已发布标签 |
| 锁文件哈希失败 | 依赖输入被替换或版本漂移 | 重建锁文件并重新评审 |
| 镜像不是 UID 10001 | 运行时安全基线回退 | 阻断发布 |
| 缺少第 8/9 章证据 | 只有构建证据，没有系统/恢复证据 | 先执行前置门禁 |
| attestation 验证失败 | 交付物来源不满足消费策略 | 不部署，检查仓库、工作流和 digest |
