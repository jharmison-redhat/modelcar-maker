ARG MODEL=ibm-granite/granite-3.3-8b-instruct
ARG NAME=ibm-granite--granite-3_3-8b-instruct-modelcar
ARG REF=main

FROM registry.access.redhat.com/ubi9/ubi-minimal:latest as base

FROM base as download

ARG ORAS_VERSION
RUN --mount=type=tmpfs,target=/tmp \
    --mount=type=tmpfs,target=/var/cache \
    --mount=type=cache,id=dnf-cache,target=/var/cache/yum \
    --mount=type=tmpfs,target=/root/.cache \
    --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    microdnf -y --disablerepo=* --enablerepo=ubi-* install tar gzip python3-pip findutils \
 && python3 -m pip install huggingface_hub

ENV HF_HOME=/tmp/hf
WORKDIR /working
ARG MODEL
RUN --mount=type=cache,id=hf-cache,target=/tmp/hf \
    --mount=type=secret,id=hf-token \
    export HF_TOKEN=$(cat /run/secrets/hf-token ||:) \
 && huggingface-cli download ${MODEL} \
 && mkdir -p models \
 && huggingface-cli download ${MODEL} --local-dir ./models \
 && mv models/README.md ./modelcard.md \
 && rm -rf models/.{cache,gitattributes} \
 && find . -exec touch -d 1970-01-01T00:00:00Z {} \;

FROM base as modelcar

ARG NAME
ARG MODEL
ARG REF

LABEL \
    name=${NAME}-modelcar \
    io.k8s.display-name=${NAME}-modelcar \
    description="A very small RHEL image which contains ${MODEL} downloaded to /models" \
    io.k8s.description="A very small RHEL image which contains ${MODEL} downloaded to /models" \
    maintainer="James Harmison <jharmiso@redhat.com>" \
    release=1 \
    summary="${MODEL} model car image" \
    url="https://github.com/jharmison-redhat/modelcar-maker" \
    vcs-ref="${REF}"

COPY --from=download /working/ /
