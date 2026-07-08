# otitbup appliance image.
#   docker build -f packaging/docker/Dockerfile -t otitbup .
# Ships the SSH/SFTP/crypto extras (the common on-appliance set); add
# vendor PLC extras (siemens/rockwell/opcua/beckhoff) if you need them.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.
RUN useradd --create-home --home-dir /var/lib/otitbup --shell /usr/sbin/nologin otitbup

WORKDIR /opt/otitbup
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .[ssh,sftp,crypto]

USER otitbup
WORKDIR /var/lib/otitbup
VOLUME ["/var/lib/otitbup", "/etc/otitbup"]

# HEALTHCHECK hits the unauthenticated liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status==200 else 1)" \
    || exit 1

EXPOSE 8080
ENTRYPOINT ["otitbup", "-c", "/etc/otitbup/otitbup.yml"]
CMD ["serve", "--host", "0.0.0.0"]
