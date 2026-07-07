# ---- Build stage ----
FROM python:3.11-slim AS builder

WORKDIR /app

# Install dependencies first for better layer caching
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# ---- Runtime stage ----
FROM python:3.11-slim AS runtime

# Run as a non-root user for security
RUN useradd --create-home --uid 1000 appuser
WORKDIR /home/appuser

# Copy installed packages and console scripts from the builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app/src ./src

# The app has no runtime dependency on the build tooling; dropping pip/setuptools/
# wheel removes their known CVEs and shrinks the image.
RUN rm -rf /usr/local/lib/python3.11/site-packages/pip* \
           /usr/local/lib/python3.11/site-packages/setuptools* \
           /usr/local/lib/python3.11/site-packages/wheel* \
           /usr/local/lib/python3.11/site-packages/pkg_resources \
           /usr/local/lib/python3.11/site-packages/_distutils_hack \
           /usr/local/bin/pip*

ENV PYTHONPATH=/home/appuser/src \
    PYTHONUNBUFFERED=1

USER appuser

CMD ["python", "-m", "app.main"]
