# Light image for strigil (CLI only, no GUI). Multi-stage to keep size down.
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml ./
COPY strigil/ ./strigil/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

FROM python:3.12-slim
WORKDIR /strigil
ENV PYTHONUNBUFFERED=1
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/strigil /usr/local/bin/strigil
ENTRYPOINT ["strigil"]
CMD ["--url", "https://example.com", "--out-dir", "/strigil/output"]
