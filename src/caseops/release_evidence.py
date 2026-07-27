from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from caseops import __version__
from caseops.api.app import create_app
from caseops.config import Settings

SCHEMA_VERSION = "caseops.release-evidence.v1"
EXPECTED_DATABASE_REVISION = "0007"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_timestamp(source_date_epoch: int | None) -> str:
    if source_date_epoch is None:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
    return datetime.fromtimestamp(source_date_epoch, UTC).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _render_openapi() -> dict[str, Any]:
    settings = Settings(
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        api_keys={"release-evidence-key": "tenant-release"},
        service_version=__version__,
    )
    app = create_app(settings=settings)
    schema = app.openapi()
    app.state.engine.dispose()
    app.state.telemetry.shutdown()
    return schema


def build_release_evidence(
    *,
    repository_root: Path,
    output_dir: Path,
    source_commit: str,
    source_ref: str,
    image_name: str,
    image_digest: str | None = None,
    sbom_path: Path | None = None,
    source_date_epoch: int | None = None,
) -> Path:
    repository_root = repository_root.resolve()
    output_dir = output_dir.resolve()
    if not COMMIT_PATTERN.fullmatch(source_commit):
        raise ValueError("source_commit must be a 40-character lowercase Git SHA")
    if image_digest is not None and not SHA256_PATTERN.fullmatch(image_digest):
        raise ValueError("image_digest must use sha256:<64 lowercase hex>")
    if sbom_path is not None and not sbom_path.is_file():
        raise ValueError(f"SBOM does not exist: {sbom_path}")

    contract_source = repository_root / "deploy" / "release-contract.yaml"
    if not contract_source.is_file():
        raise ValueError(f"release contract does not exist: {contract_source}")

    output_dir.mkdir(parents=True, exist_ok=True)
    release_name = f"v{__version__}"
    expected_files = {
        f"openapi-{release_name}.json",
        "release-contract.yaml",
        "release-evidence.json",
        "SHA256SUMS",
        "chapter-08-report.json",
        "chapter-09-gameday.json",
        f"caseops-{release_name}.spdx.json",
    }
    for name in expected_files:
        target = output_dir / name
        if target.is_file():
            target.unlink()

    openapi_path = output_dir / f"openapi-{release_name}.json"
    _write_json(openapi_path, _render_openapi())
    shutil.copyfile(contract_source, output_dir / "release-contract.yaml")

    optional_sources = {
        repository_root
        / "artifacts"
        / "evaluation"
        / "chapter-08-report.json": "chapter-08-report.json",
        repository_root
        / "artifacts"
        / "operations"
        / "chapter-09-gameday.json": "chapter-09-gameday.json",
    }
    for source, name in optional_sources.items():
        if source.is_file():
            shutil.copyfile(source, output_dir / name)
    if sbom_path is not None:
        shutil.copyfile(
            sbom_path.resolve(),
            output_dir / f"caseops-{release_name}.spdx.json",
        )

    evidence_files = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name not in {"release-evidence.json", "SHA256SUMS"}
    )
    artifacts = [
        {
            "path": path.name,
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in evidence_files
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "release": release_name,
        "generated_at": _utc_timestamp(source_date_epoch),
        "source": {"commit": source_commit, "ref": source_ref},
        "runtime": {
            "image": image_name,
            "digest": image_digest,
            "database_revision": EXPECTED_DATABASE_REVISION,
            "non_root_uid": 10001,
        },
        "gates": [
            {
                "name": "code-quality-and-security",
                "authority": ".github/workflows/ci.yml",
            },
            {
                "name": "system-evaluation",
                "authority": "scripts/acceptance-chapter-08.sh",
                "evidence": "chapter-08-report.json",
            },
            {
                "name": "operations-gameday",
                "authority": "scripts/acceptance-chapter-09.sh",
                "evidence": "chapter-09-gameday.json",
            },
            {
                "name": "clean-room-reproduction",
                "authority": "scripts/acceptance-chapter-10.sh",
            },
        ],
        "artifacts": artifacts,
        "consumer_verification": {
            "checksums": "sha256sum --check SHA256SUMS",
            "release_bundle": (
                f"gh attestation verify caseops-{release_name}-release.tar.gz "
                "--repo dataPro-lgtm/production-grade-multi-agent-caseops"
            ),
            "container": (
                "gh attestation verify "
                "oci://ghcr.io/datapro-lgtm/"
                f"production-grade-multi-agent-caseops:{release_name} "
                "--repo dataPro-lgtm/production-grade-multi-agent-caseops"
            ),
        },
        "limits": [
            "Attestation proves artifact origin and build identity, not artifact safety.",
            (
                "The conformance mode does not include provider token "
                "or monetary pricing data."
            ),
            (
                "Deployment-specific identity, TLS, key management, "
                "and threat assessment remain required."
            ),
        ],
    }
    manifest_path = output_dir / "release-evidence.json"
    _write_json(manifest_path, manifest)

    checksum_targets = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    checksum_path = output_dir / "SHA256SUMS"
    checksum_path.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in checksum_targets),
        encoding="utf-8",
    )
    return manifest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the deterministic CaseOps v1.0 release evidence directory."
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("artifacts/release"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument(
        "--image-name",
        default="ghcr.io/datapro-lgtm/production-grade-multi-agent-caseops",
    )
    parser.add_argument("--image-digest")
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--source-date-epoch", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = build_release_evidence(
        repository_root=args.repository_root,
        output_dir=args.output,
        source_commit=args.source_commit,
        source_ref=args.source_ref,
        image_name=args.image_name,
        image_digest=args.image_digest,
        sbom_path=args.sbom,
        source_date_epoch=args.source_date_epoch,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
