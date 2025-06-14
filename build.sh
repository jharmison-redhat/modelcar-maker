#!/bin/bash

set -e

cd "$(dirname "$(realpath "$0")")"

rebuild=false
models=()
while ((${#@} > 0)); do
    case "$1" in
    --rebuild,-r)
        rebuild=true
        ;;
    *)
        models+=("$1")
        ;;
    esac
    shift
done

if ! (("${#models}" > 0)); then
    mapfile -t models <models.txt
fi

common_args=(--pull=newer)

if [ -n "$HF_TOKEN" ]; then
    common_args+=(--secret id=hf-token,env=HF_TOKEN)
elif [ -e .hf-token ]; then
    common_args+=(--secret "id=hf-token,src=$(pwd)/.hf-token,type=file")
elif [ -e "$HOME/.cache/huggingface/token" ]; then
    common_args+=(--secret "id=hf-token,src=$HOME/.cache/huggingface/token,type=file")
fi

MODELCAR_REGISTRY="${MODELCAR_REGISTRY:-quay.io}"
MODELCAR_REPO="${MODELCAR_REPO:-jharmison/models}"

function nologin {
    echo "Log in to ${MODELCAR_REGISTRY} or export REGISTRY_AUTH_FILE to point to a configuration where you are logged in." >&2
    exit 1
}
function branch {
    git branch 2>/dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/\1/'
}
function modelcar_exists {
    skopeo inspect docker://"${1}" >/dev/null 2>&1
}

if ! podman login --get-login "${MODELCAR_REGISTRY}" >/dev/null 2>&1; then
    nologin
fi

common_args+=(--build-arg=REF=$(branch))

for model in "${models[@]}"; do
    if [[ $model =~ ^#.* ]]; then
        continue
    fi
    tag="$(echo "$model" | sed -e 's/\//--/g' -e 's/\./_/g' -e 's/.*/\L&/')-modelcar"
    image="${MODELCAR_REGISTRY}/${MODELCAR_REPO}:${tag}"
    if ! $rebuild; then
        if modelcar_exists "$image"; then
            echo "$model already available at $image, to rebuild anyways pass --rebuild" >&2
            continue
        fi
    fi
    model_args=("--build-arg=MODEL=${model}")
    model_args+=("--build-arg=NAME=${tag}")
    podman build . "${common_args[@]}" "${model_args[@]}" -t "$image"
    podman push "$image" || nologin
done
