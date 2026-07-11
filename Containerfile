FROM registry.access.redhat.com/ubi9/python-312:latest as base

WORKDIR /opt/app-root/src

FROM base as builder

USER 0

RUN pip install --no-cache-dir pip-tools tox

COPY pyproject.toml ./

RUN pip-compile pyproject.toml && \
    pip install --no-cache-dir -r requirements.txt

COPY ./ ./

RUN tox -e build

FROM base as final

USER 0

RUN dnf -y install skopeo && dnf -y clean all

RUN --mount=type=bind,from=builder,source=/opt/app-root/src/dist,target=/opt/app-root/src \
    pip install --no-cache-dir ./*.whl

USER 1001

WORKDIR /modelcar-maker
ENV HF_TOKEN ""

ENTRYPOINT ["modelcar-maker"]
CMD []
