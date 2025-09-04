# Use the official Rust image as the base image
FROM rust:1.85-slim as builder

# Install required dependencies for building
RUN apt-get update && apt-get install -y pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy the Cargo.toml file
COPY Cargo.toml ./

# Copy the source code
COPY src ./src

# Build the application in release mode (this will generate Cargo.lock)
RUN cargo build --release

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
