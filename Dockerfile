FROM python:3.12-slim

# git + ssh: Askcite keeps its own read-only mirror of your repositories
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY askcite ./askcite
RUN pip install --no-cache-dir .

RUN useradd --create-home askcite && mkdir -p /data && chown askcite /data
USER askcite
ENV ASKCITE_DATA_DIR=/data ASKCITE_CONFIG_DIR=/config
EXPOSE 8080
CMD ["askcite", "run", "--host", "0.0.0.0"]
