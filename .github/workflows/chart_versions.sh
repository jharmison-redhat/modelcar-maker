#!/bin/bash

set -euo pipefail

publish=false
declare -A publish_charts
declare -A chart_version

while read -rd $'\0' chart; do
  name="$(basename "$chart")"

  version="$(awk '/^version:/{print $2}' "$chart/Chart.yaml")"
  if git show "$BEFORE_SHA:$chart/Chart.yaml" >/dev/null 2>&1; then
    old_version="$(git show "$BEFORE_SHA:$chart/Chart.yaml" | awk '/^version:/{print $2}')"
  else
    old_version=""
  fi
  echo "$name: $version (old: $old_version)"

  if [ "$version" != "$old_version" ]; then
    publish=true
    publish_charts["$name"]="$chart"
    chart_version["$name"]="$version"
  fi
done < <(find charts -type d -mindepth 1 -maxdepth 1 -print0)

charts='{"include": ['
for chart in "${!publish_charts[@]}"; do
  charts+='{"name": "'"$chart"'", "path": "'"${publish_charts[$chart]}"'", "version": "'"${chart_version[$chart]}"'"}, '
done
charts="${charts%, }]}"

echo "publish=${publish}" >> "$GITHUB_OUTPUT"
echo "matrix=$charts" >> "$GITHUB_OUTPUT"
