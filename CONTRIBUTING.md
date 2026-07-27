# Contributing to CaseOps

## Before opening a change

1. Open an issue for behavior or architecture changes.
2. Keep deterministic business rules outside prompts and model adapters.
3. Add tests for success, failure, tenant isolation and side effects.
4. Update the relevant ADR when a boundary or dependency direction changes.

## Local verification

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps -e .
make verify
make security
docker compose up --build -d
```

Pull requests must not contain credentials or real personal data. Use the synthetic C-102 fixtures.

## Commit and review expectations

- Explain the operational effect, not only the code diff.
- Identify schema, API and event compatibility changes.
- Include rollback or downgrade considerations for migrations.
- Do not weaken a quality or security gate without an ADR.
- Regenerate both lock files in Python 3.12 and review the dependency diff when
  changing project requirements.
