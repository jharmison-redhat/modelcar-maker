#!/usr/bin/env bash
set -euo pipefail

chart="$(dirname "$0")/.."
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

fail() {
  printf 'chart test failed: %s\n' "$*" >&2
  exit 1
}

require_pattern() {
  local pattern="$1" file="$2"
  if ! grep -Eq "$pattern" "$file"; then
    fail "expected $file to match $pattern"
  fi
}

forbid_pattern() {
  local pattern="$1" file="$2"
  if grep -Eq "$pattern" "$file"; then
    fail "expected $file not to match $pattern"
  fi
}

render() {
  local name="$1"
  shift
  helm template "$name" "$@" > "$tmpdir/$name.yaml"
}

job_name() {
  perl -0ne 'print "$1\n" if /kind: Job\nmetadata:\n  name: ([^\n]+)/' "$1"
}

invalid_schema() {
  local name="$1"
  shift
  if helm template "$name" "$chart" "$@" >/dev/null 2>&1; then
    fail "expected $name values to fail schema validation"
  fi
}

helm lint --strict "$chart"
invalid_schema invalid-workspace --set workspace.type=invalid
invalid_schema invalid-resource-list --set resources.requests.cpu[0]=1
invalid_schema invalid-resource-quantity --set resources.limits.memory=true
invalid_schema invalid-emptydir-medium --set workspace.emptyDir.medium=Disk
invalid_schema invalid-emptydir-size --set workspace.emptyDir.sizeLimit=true
invalid_schema invalid-pvc-size --set workspace.type=pvc --set workspace.pvc.size=true
invalid_schema invalid-pod-security --set podSecurityContext.runAsNonRoot=not-a-boolean
invalid_schema invalid-container-security --set securityContext.readOnlyRootFilesystem=not-a-boolean

# Empty defaults are intentionally inactive, including their Helm notes.
render default "$chart"
forbid_pattern '^kind: Job$' "$tmpdir/default.yaml"
forbid_pattern '^kind: DaemonSet$' "$tmpdir/default.yaml"
helm install default "$chart" --dry-run=client --debug > "$tmpdir/default-notes.yaml" 2>&1
require_pattern 'No models are configured, so this release is inactive' "$tmpdir/default-notes.yaml"

# Generated service-account credentials use an isolated memory volume; the main container gets the flag.
render generated "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0
require_pattern 'helm.sh/resource-policy: keep' "$tmpdir/generated.yaml"
require_pattern 'name: registry-credentials' "$tmpdir/generated.yaml"
require_pattern 'medium: Memory' "$tmpdir/generated.yaml"
require_pattern 'mountPath: /modelcar-maker/\.docker$' "$tmpdir/generated.yaml"
require_pattern '^          args:$' "$tmpdir/generated.yaml"
require_pattern '^            - --service-account-auth$' "$tmpdir/generated.yaml"
forbid_pattern 'authfile-builder' "$tmpdir/generated.yaml"
forbid_pattern '/registry-credentials/\.docker' "$tmpdir/generated.yaml"

# Explicit Docker credentials retain their Secret key and direct config.json mount and do not generate credentials.
render explicit "$chart" -f "$chart/example-external-registry.yaml"
require_pattern 'name: push-secret' "$tmpdir/explicit.yaml"
require_pattern 'key: \.dockerconfigjson' "$tmpdir/explicit.yaml"
require_pattern 'path: config.json' "$tmpdir/explicit.yaml"
require_pattern 'mountPath: /modelcar-maker/.docker/config.json' "$tmpdir/explicit.yaml"
require_pattern '^          args: \[\]$' "$tmpdir/explicit.yaml"
forbid_pattern 'name: registry-credentials' "$tmpdir/explicit.yaml"
forbid_pattern 'service-account-auth' "$tmpdir/explicit.yaml"

# An external registry without explicit credentials uses the ephemeral service-account mode.
render unauthenticated "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 --set modelcar.image.registry=registry.example.test --set modelcar.image.repository=models --set modelcar.imageStream.create=false
require_pattern 'name: registry-credentials' "$tmpdir/unauthenticated.yaml"
require_pattern '^            - --service-account-auth$' "$tmpdir/unauthenticated.yaml"

# Job identities are deterministic, respond to each execution-input category, and remain RFC 1123 labels.
helm template identity "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 > "$tmpdir/identity.yaml"
helm template identity "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 > "$tmpdir/identity-repeat.yaml"
if ! cmp -s "$tmpdir/identity.yaml" "$tmpdir/identity-repeat.yaml"; then
  fail 'identical execution inputs must render identically'
fi
baseline_name="$(job_name "$tmpdir/identity.yaml")"
[[ -n "$baseline_name" ]] || fail 'expected a Job name'

mutations=(
  'models[0].repo=other/model'
  'image.tag=changed'
  'modelcar.dockerConfigJson=explicit'
  'certificateAuthorities.openshiftClusterCa=true'
  'workspace.modelCleanup=false'
  'resources.requests.cpu=1'
  'podSecurityContext.runAsUser=1000'
  'securityContext.readOnlyRootFilesystem=true'
  'modelcar.env.LOG_LEVEL=debug'
  'podAnnotations.example=test'
  'nodeSelector.node=worker'
  'serviceAccount.name=builder'
  'image.pullPolicy=Always'
  'backoffLimit=5'
)
for mutation in "${mutations[@]}"; do
  helm template identity "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 --set "$mutation" > "$tmpdir/mutation.yaml"
  if [[ "$baseline_name" == "$(job_name "$tmpdir/mutation.yaml")" ]]; then
    fail "Job name did not change for $mutation"
  fi
done

render long.name "$chart" --set models[0].repo=TinyLlama/TinyLlama-1.1B-Chat-v1.0 --set fullnameOverride=abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrs.
long_name="$(job_name "$tmpdir/long.name.yaml")"
[[ ${#long_name} -le 63 ]] || fail 'Job name exceeds 63 characters'
[[ "$long_name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fail "Job name is not an RFC 1123 label: $long_name"
