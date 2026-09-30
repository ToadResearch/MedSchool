# Keep this aligned with the minimum Rust version required by Cargo.lock.
FROM rust:1.92-slim AS builder

# Install required dependencies for building
RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy the manifest and lockfile so dependency resolution is reproducible.
COPY Cargo.toml Cargo.lock ./

# Copy the source code
COPY src ./src

# Fail if the manifest and checked-in lockfile drift apart.
RUN cargo build --locked --release

# Use a smaller base image for the final image
FROM debian:bookworm-slim

# Install runtime dependencies if needed
RUN apt-get update \
   && apt-get install -y --no-install-recommends ca-certificates curl \
   && rm -rf /var/lib/apt/lists/*


# Copy the compiled binary from the builder stage
COPY --from=builder /app/target/release/middleman /usr/local/bin/middleman

# Expose the port the app runs on
EXPOSE 3000

# Command to run the application
CMD ["middleman"]
