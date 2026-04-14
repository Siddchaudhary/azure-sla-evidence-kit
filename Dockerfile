# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
RUN pip install uv

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ src/
COPY slas/ slas/
COPY templates/ templates/

# Install dependencies (non-editable for production)
RUN uv pip install --system .

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application files
COPY --from=builder /app/src /app/src
COPY --from=builder /app/slas /app/slas
COPY --from=builder /app/templates /app/templates
COPY pyproject.toml .

# Create data directory
RUN mkdir -p /home/appuser/.azsla && chown -R appuser:appuser /home/appuser/.azsla

# Switch to non-root user
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOST=0.0.0.0
ENV PORT=8000

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/api/docs')" || exit 1

# Run the application
CMD ["python", "-m", "azsla.web.main"]
