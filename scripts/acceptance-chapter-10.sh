#!/usr/bin/env bash
set -euo pipefail

repository_root="$(git rev-parse --show-toplevel)"
source_commit="$(git -C "$repository_root" rev-parse HEAD)"
source_ref="$(
  git -C "$repository_root" describe --tags --exact-match 2>/dev/null \
    || git -C "$repository_root" branch --show-current
)"
source_epoch="$(git -C "$repository_root" show -s --format=%ct HEAD)"
image_name="${CASEOPS_RELEASE_IMAGE_NAME:-caseops-cleanroom}"
image_tag="${CASEOPS_RELEASE_IMAGE_TAG:-v1.0.0}"
local_image="${image_name}:${image_tag}"
output_dir="$repository_root/artifacts/release"
work_dir="$(mktemp -d)"

cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT

if ! git -C "$repository_root" diff --quiet \
  || ! git -C "$repository_root" diff --cached --quiet; then
  echo "clean-room acceptance requires a committed source tree" >&2
  exit 1
fi
test -f "$repository_root/artifacts/evaluation/chapter-08-report.json"
test -f "$repository_root/artifacts/operations/chapter-09-gameday.json"

mkdir -p "$work_dir/source" "$output_dir"
git -C "$repository_root" archive --format=tar HEAD | tar -xf - -C "$work_dir/source"

test ! -e "$work_dir/source/.git"
test ! -e "$work_dir/source/.env"
test ! -e "$work_dir/source/caseops.db"
test ! -e "$work_dir/source/artifacts/evaluation/chapter-08-report.json"
test ! -e "$work_dir/source/artifacts/operations/chapter-09-gameday.json"

docker build \
  --tag "$local_image" \
  --build-arg "VERSION=1.0.0" \
  --build-arg "VCS_REF=$source_commit" \
  "$work_dir/source"

runtime_user="$(docker image inspect "$local_image" --format '{{.Config.User}}')"
runtime_version="$(
  docker image inspect "$local_image" \
    --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
)"
runtime_revision="$(
  docker image inspect "$local_image" \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
)"
test "$runtime_user" = "10001:10001"
test "$runtime_version" = "1.0.0"
test "$runtime_revision" = "$source_commit"

docker run --rm \
  --entrypoint python \
  "$local_image" \
  -c '
import caseops
from caseops.api.app import app

assert caseops.__version__ == "1.0.0"
assert app.version == "1.0.0"
print("clean-room runtime import accepted")
'

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  anchore/syft:v1.44.0 \
  "$local_image" \
  -o spdx-json >"$work_dir/caseops-v1.0.0.spdx.json"

image_digest="$(docker image inspect "$local_image" --format '{{.Id}}')"
python3 -m caseops.release_evidence \
  --repository-root "$repository_root" \
  --output "$output_dir" \
  --source-commit "$source_commit" \
  --source-ref "$source_ref" \
  --image-name "$image_name" \
  --image-digest "$image_digest" \
  --sbom "$work_dir/caseops-v1.0.0.spdx.json" \
  --source-date-epoch "$source_epoch"

python3 - "$output_dir" "$source_commit" "$image_digest" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / "release-evidence.json").read_text(encoding="utf-8"))
assert manifest["release"] == "v1.0.0", manifest
assert manifest["source"]["commit"] == sys.argv[2], manifest
assert manifest["runtime"]["digest"] == sys.argv[3], manifest
assert manifest["runtime"]["non_root_uid"] == 10001, manifest
artifact_names = {artifact["path"] for artifact in manifest["artifacts"]}
assert {
    "openapi-v1.0.0.json",
    "release-contract.yaml",
    "chapter-08-report.json",
    "chapter-09-gameday.json",
    "caseops-v1.0.0.spdx.json",
}.issubset(artifact_names), artifact_names

for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
    expected, name = line.split("  ", 1)
    actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
    assert actual == expected, (name, expected, actual)
print(
    "chapter 10 accepted: clean archive -> pinned image -> non-root runtime "
    "-> SPDX SBOM -> checksummed release evidence"
)
PY
