FROM registry.access.redhat.com/ubi9/python-312:latest

WORKDIR /opt/app-root/src

RUN pip install --no-cache-dir pip-tools

COPY --chown=1001:1001 pyproject.toml ./

RUN pip-compile pyproject.toml && \
    pip install --no-cache-dir -r requirements.txt

COPY --chown=1001:1001 ./ ./

RUN pip install --no-cache-dir .

WORKDIR /modelcar-maker
ENV HF_TOKEN

ENTRYPOINT ["modelcar-maker"]
CMD []
