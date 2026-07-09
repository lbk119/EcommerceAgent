FROM debian:bookworm-slim

RUN groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /workspace

USER sandbox:sandbox

CMD ["sleep", "3600"]