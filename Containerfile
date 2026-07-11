FROM registry.access.redhat.com/ubi9/python-312:latest as base

WORKDIR /opt/app-root/src

FROM base as builder

RUN pip install --no-cache-dir pip-tools tox

COPY --chown=1001:1001 pyproject.toml ./

RUN pip-compile pyproject.toml && \
    pip install --no-cache-dir -r requirements.txt

COPY --chown=1001:1001 ./ ./

RUN tox -e build

FROM base as final

USER 0

RUN dnf -y install skopeo && dnf -y clean all

COPY --from=builder /opt/app-root/src/*.whl ./

RUN pip install --no-cache-dir ./*.whl

USER 1001

WORKDIR /modelcar-maker
ENV HF_TOKEN ""

ENTRYPOINT ["modelcar-maker"]
CMD []
