#!/usr/bin/env bash
set -euo pipefail

chart="$(dirname "$0")/.."
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

render() {
  helm template "$@" > "$tmpdir/$1.yaml"
}

job_name() {
  perl -0ne 'print "$1\n" if /kind: Job\nmetadata:\n  name: ([^\n]+)/' "$1"
}

helm lint "$chart"
if helm template invalid-schema "$chart" --set workspace.type=invalid >/dev/null 2>&1; then
  printf '%s\n' 'expected invalid workspace.type to fail schema validation' >&2
  exit 1
fi

# Empty defaults are intentionally inactive.
render default "$chart"
! grep -q '^kind: Job$' "$tmpdir/default.yaml"
! grep -q '^kind: DaemonSet$' "$tmpdir/default.yaml"
helm template default "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 --set imagePuller.create=true > "$tmpdir/generated.yaml"
grep -q '^kind: Job$' "$tmpdir/generated.yaml"
grep -A2 'name: registry-credentials' "$tmpdir/generated.yaml" | grep -q 'medium: Memory'
! grep -A5 'name: authfile-builder' "$tmpdir/generated.yaml" | grep -q 'name: workspace'

# Explicit Docker credentials retain their Secret mount and do not create generated credentials.
helm template external "$chart" -f "$chart/example-external-registry.yaml" > "$tmpdir/explicit.yaml"
grep -q 'name: push-secret' "$tmpdir/explicit.yaml"
! grep -q 'name: registry-credentials' "$tmpdir/explicit.yaml"

# An external registry without explicit credentials uses the ephemeral service-account mode.
helm template unauthenticated "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 --set modelcar.image.registry=registry.example.test --set modelcar.image.repository=models --set modelcar.imageStream.create=false > "$tmpdir/unauthenticated.yaml"
grep -q 'name: registry-credentials' "$tmpdir/unauthenticated.yaml"

# Job identities are deterministic, react to execution inputs, and remain DNS-label length.
helm template identity "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 > "$tmpdir/identity-a.yaml"
helm template identity "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 > "$tmpdir/identity-b.yaml"
cmp "$tmpdir/identity-a.yaml" "$tmpdir/identity-b.yaml"
helm template identity "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 --set backoffLimit=5 > "$tmpdir/identity-changed.yaml"
test "$(job_name "$tmpdir/identity-a.yaml")" != "$(job_name "$tmpdir/identity-changed.yaml")"
helm template 12345678901234567890123456789012345678901234567890123 "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 > "$tmpdir/long.yaml"
long_name="$(job_name "$tmpdir/long.yaml")"
test "${#long_name}" -le 63
