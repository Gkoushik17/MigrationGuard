FROM python:3.12-slim

# git is needed for --changed-since
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && useradd --create-home guard \
 && git config --system --add safe.directory /repo

USER guard
WORKDIR /repo
ENTRYPOINT ["migration-guard"]
CMD ["check"]
