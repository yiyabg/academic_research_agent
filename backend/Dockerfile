
# Build stage
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_HTTP_TIMEOUT=120
ENV UV_CONCURRENT_DOWNLOADS=8

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy application
COPY . /app

# Install project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Docling's layout pipeline imports Transformers image utilities, so the CPU
# worker requires torchvision.  The generic PyPI wheel lacks the operators of
# the matching CPU torch wheel in this environment; take the paired binary
# from the official PyTorch CPU index instead.  Dockerfile.bge-gpu deliberately
# removes torchvision after replacing torch with CUDA because BGE is text-only.
ARG PYTORCH_CPU_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCHVISION_CPU_VERSION=0.28.0+cpu
RUN /app/.venv/bin/python -m ensurepip --upgrade && \
    /app/.venv/bin/python -m pip install --upgrade --force-reinstall --no-deps \
      --index-url=${PYTORCH_CPU_INDEX} torchvision==${TORCHVISION_CPU_VERSION} && \
    /app/.venv/bin/python -c "import torch, torchvision; print(torch.__version__, torchvision.__version__)"


# Runtime stage
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
# Tesseract is the fail-local OCR path for scanned scholarly PDF pages.
RUN sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list.d/debian.sources && \
    apt-get -o Acquire::ForceIPv4=true -o Acquire::https::Timeout=30 \
        -o Acquire::Retries=3 update && \
    apt-get -o Acquire::ForceIPv4=true -o Acquire::https::Timeout=30 \
        -o Acquire::Retries=3 install -y --no-install-recommends \
        libgl1 tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim && \
    rm -rf /var/lib/apt/lists/*

# Create the runtime identity before copying, then copy the builder output only
# once with final ownership. Copying .venv separately and then /app duplicated
# the largest layer and made every build perform a costly recursive chown.
RUN adduser --disabled-password --gecos "" appuser && \
    mkdir -p /app/media /app/data /app/chroma_data /app/models_cache && \
    chown appuser:appuser /app /app/media /app/data /app/chroma_data /app/models_cache
COPY --from=builder --chown=appuser:appuser /app /app

# Add venv to path
ENV PATH="/app/.venv/bin:$PATH"
USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/api/v1/health')" || exit 1

CMD ["python", "-m", "cli.commands", "server", "run", "--host", "0.0.0.0", "--port", "8000"]
