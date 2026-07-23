# Security Policy

## Supported versions

CaseOps is under active development. Security fixes are applied to the latest release on `main`. Earlier chapter tags are immutable learning snapshots and may contain issues fixed in later releases.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's private GitHub Security Advisory reporting channel and include:

- affected version or commit;
- reproduction steps;
- expected and observed impact;
- any known workaround.

The maintainer will acknowledge a complete report within five business days. Disclosure timing will be coordinated after the issue is reproduced and a remediation plan exists.

Never include real customer, claimant, credential or payment data in a report. All repository data is synthetic.

## Scope notes

The development API Key adapter is not a substitute for an enterprise identity provider. Production deployments must replace development credentials, protect secrets outside source control, terminate TLS at a trusted ingress, and complete a deployment-specific threat assessment.
