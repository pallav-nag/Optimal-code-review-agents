# syntax=docker/dockerfile:1.7
# Two runtime images from one build:
#   core — gateway, agents (static/reviewer/security/tests), aggregator, critic, publisher
#   rag  — context service + indexer (ONNX embedding models baked in, Graphify, git)

FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
WORKDIR /app

FROM base AS builder
COPY pyproject.toml ./
# dependency wheels first (cached layer): built against a stub package + stub README,
# so only pyproject.toml changes invalidate it
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p src/graphreview && touch src/graphreview/__init__.py README.md \
    && pip wheel --wheel-dir /wheels ".[rag,graph,static]" && rm /wheels/graphreview-*.whl
COPY README.md ./
COPY src ./src
RUN pip wheel --no-deps --wheel-dir /wheels .

FROM base AS core
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links /wheels "graphreview[static]" && rm -rf /wheels
COPY eval/cases ./eval/cases
COPY eval/fixtures ./eval/fixtures
USER 10001
EXPOSE 8080 9100
ENTRYPOINT ["graphreview"]
CMD ["gateway"]

FROM base AS rag
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links /wheels "graphreview[rag,graph]" && rm -rf /wheels
ENV FASTEMBED_CACHE_PATH=/opt/models REPOS_DIR=/var/lib/graphreview/repos HF_HUB_DISABLE_TELEMETRY=1
# bake the embedding models into the image: no network fetch at pod start
RUN python -c "from fastembed import TextEmbedding, SparseTextEmbedding; \
TextEmbedding('BAAI/bge-small-en-v1.5'); SparseTextEmbedding('Qdrant/bm25')" \
    && mkdir -p /var/lib/graphreview/repos && chown -R 10001 /var/lib/graphreview /opt/models
COPY eval/fixtures ./eval/fixtures
USER 10001
EXPOSE 8001 9100
ENTRYPOINT ["graphreview"]
CMD ["context"]
