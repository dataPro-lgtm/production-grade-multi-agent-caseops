from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from caseops.release_evidence import build_release_evidence


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_evidence_binds_source_runtime_and_artifacts(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    output = tmp_path / "release"
    commit = "a" * 40

    manifest_path = build_release_evidence(
        repository_root=root,
        output_dir=output,
        source_commit=commit,
        source_ref="v1.0.0",
        image_name="ghcr.io/example/caseops",
        image_digest=f"sha256:{'b' * 64}",
        source_date_epoch=1_785_084_800,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "caseops.release-evidence.v1"
    assert manifest["release"] == "v1.0.0"
    assert manifest["source"] == {"commit": commit, "ref": "v1.0.0"}
    assert manifest["runtime"]["digest"] == f"sha256:{'b' * 64}"
    assert manifest["runtime"]["database_revision"] == "0007"
    assert {
        "openapi-v1.0.0.json",
        "release-contract.yaml",
    }.issubset({artifact["path"] for artifact in manifest["artifacts"]})
    openapi = json.loads((output / "openapi-v1.0.0.json").read_text(encoding="utf-8"))
    assert openapi["info"]["version"] == "1.0.0"

    checksum_lines = (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    checksums = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in checksum_lines}
    for name, digest in checksums.items():
        assert _digest(output / name) == digest


def test_release_evidence_rejects_unverifiable_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="40-character"):
        build_release_evidence(
            repository_root=Path(__file__).parents[1],
            output_dir=tmp_path,
            source_commit="main",
            source_ref="main",
            image_name="ghcr.io/example/caseops",
        )
