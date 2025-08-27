# docker/val_server/Dockerfile
FROM busybox:1.36.1-musl AS bb

FROM markiantorno/validator-wrapper:latest
# add a tiny busybox for healthcheck/wget
COPY --from=bb /bin/busybox /busybox
