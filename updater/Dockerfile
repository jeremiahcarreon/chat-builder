# The updater: checks GitHub for new Chat Builder releases and installs them.
# Built from the repository root: docker build -f updater/Dockerfile .
FROM alpine:3.20

RUN apk add --no-cache bash coreutils curl docker-cli docker-cli-compose gzip

COPY deploy/chat-builder /usr/local/bin/chat-builder
RUN chmod 755 /usr/local/bin/chat-builder

CMD ["chat-builder", "watch"]
