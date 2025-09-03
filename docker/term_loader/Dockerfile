# docker/term_loader/Dockerfile
FROM eclipse-temurin:17-jre

ARG HAPI_VERSION=8.6.0
RUN apt-get update && apt-get install -y --no-install-recommends curl bzip2 && rm -rf /var/lib/apt/lists/*

# Fetch HAPI FHIR CLI
RUN curl -fsSL -o /tmp/cli.tar.bz2 \
      "https://github.com/hapifhir/hapi-fhir/releases/download/v${HAPI_VERSION}/hapi-fhir-${HAPI_VERSION}-cli.tar.bz2" \
 && mkdir -p /opt/hapi-cli \
 && tar -xjf /tmp/cli.tar.bz2 -C /opt/hapi-cli --strip-components=1 \
 && ln -s /opt/hapi-cli/bin/hapi-fhir-cli /usr/local/bin/hapi-fhir-cli \
 && rm -f /tmp/cli.tar.bz2

ENV TARGET_FHIR_BASE=http://hapi:8080/fhir
# Give the import process some heap; tune as needed
ENV JAVA_TOOL_OPTIONS="-Xms1g -Xmx6g"

WORKDIR /work
COPY loader.sh /usr/local/bin/loader
RUN chmod +x /usr/local/bin/loader

ENTRYPOINT ["loader"]
