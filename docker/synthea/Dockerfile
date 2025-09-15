FROM eclipse-temurin:17-jdk-jammy AS builder
# We do NOT install gradle. Use the project's wrapper instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
git unzip ca-certificates && rm -rf /var/lib/apt/lists/*


WORKDIR /opt
RUN git clone --branch v3.3.0 --single-branch --depth=1 https://github.com/synthetichealth/synthea.git
WORKDIR /opt/synthea


# Ensure wrapper is executable; build with the pinned Gradle version.
RUN chmod +x ./gradlew && ./gradlew --no-daemon clean build -x test


# Runtime image
FROM eclipse-temurin:17-jre-jammy
WORKDIR /app
# Copy the built jar
COPY --from=builder /opt/synthea/build/libs/synthea-with-dependencies.jar /app/synthea.jar
# Copy default configs (you can override via volume or ENV)
COPY synthea.properties /app/synthea.properties
COPY generate_data.sh /app/generate_data.sh
RUN chmod +x /app/generate_data.sh


ENTRYPOINT ["/app/generate_data.sh"]