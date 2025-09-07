FROM alpine:3.20

# Install useful tools for the sandbox environment
RUN apk add --no-cache \
    jq \
    bash \
    python3 \
    && rm -rf /var/cache/apk/* \
    && ln -sf python3 /usr/bin/python

# Set up a non-root user for security
RUN adduser -D -s /bin/bash sandbox && \
    mkdir -p /home/sandbox && \
    chown -R sandbox:sandbox /home/sandbox

# Set working directory
WORKDIR /home/sandbox

# Set environment variables for the sandbox user
ENV PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV SHELL="/bin/bash"

USER sandbox

# Default command
CMD ["/bin/bash"]
