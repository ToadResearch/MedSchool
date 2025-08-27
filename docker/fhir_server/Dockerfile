# Old code -- just added busybox for healthcheck
# # MedSchool/docker/fhir_server/Dockerfile

# # Stage 1: grab a static busybox we can use for healthchecks (no libs, no shell needed)
# FROM busybox:1.36.1-musl as bb

# # Stage 2: final HAPI image (distroless)
# FROM hapiproject/hapi:latest

# # Copy a single static binary; place it at /busybox (distroless may not have /bin)
# COPY --from=bb /bin/busybox /busybox

# New code -- busybox for healthcheck and added validation engine
# MedSchool/docker/fhir_server/Dockerfile
# ----------------------------------------------------------------------
# jars on Maven Central
ARG HAPI_VERSION=8.2.0          
# tag that exists on Docker Hub
ARG HAPI_IMAGE_TAG=v8.2.0-2     
# r4 | r4b | r5 | dstu3 | dstu2 | dstu2.1
ARG FHIR_FLAVOUR=r4             
# ----------------------------------------------------------------------

# ── Stage 0: helper shell for health-check ──────────────────────────────
FROM busybox:1.36.1-musl AS bb

# ── Stage 1: download validator JARs (has curl) ─────────────────────────
ARG HAPI_VERSION=8.2.0
ARG FHIR_FLAVOUR=r4
# tiny (≈8 MB) image
FROM curlimages/curl:8.8.0 AS downloader
ARG HAPI_VERSION
ARG FHIR_FLAVOUR

# we need to get hapi-fhir-validation and hapi-fhir-validation-resources-r4 
# https://github.com/hapifhir/hapi-fhir
RUN curl -fSL \
      "https://repo1.maven.org/maven2/ca/uhn/hapi/fhir/hapi-fhir-validation/${HAPI_VERSION}/hapi-fhir-validation-${HAPI_VERSION}.jar" \
      -o "/tmp/hapi-fhir-validation.jar" \
 && curl -fSL \
      "https://repo1.maven.org/maven2/ca/uhn/hapi/fhir/hapi-fhir-validation-resources-${FHIR_FLAVOUR}/${HAPI_VERSION}/hapi-fhir-validation-resources-${FHIR_FLAVOUR}-${HAPI_VERSION}.jar" \
      -o "/tmp/hapi-fhir-validation-resources.jar"

# ── Stage 2: final distroless HAPI image ────────────────────────────────
# tag that exists on Docker Hub
ARG HAPI_IMAGE_TAG=v8.2.0-2
FROM hapiproject/hapi:${HAPI_IMAGE_TAG}
COPY --from=bb /bin/busybox /busybox   
COPY --from=downloader /tmp/hapi-fhir-validation.jar /app/libs/
COPY --from=downloader /tmp/hapi-fhir-validation-resources.jar /app/libs/
