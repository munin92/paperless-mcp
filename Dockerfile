FROM python:3.13-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 mcp \
    && mkdir -p /home/mcp/outbox \
    && chown -R mcp:mcp /home/mcp

USER 10001

EXPOSE 8000

CMD ["paperless-mcp-oidc"]
