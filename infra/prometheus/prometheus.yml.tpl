# DocMasker Prometheus scrape configuration (template).
#
# The application is NOT part of the monitoring profiles: `just monitoring-stand`
# starts Prometheus while the app runs under `just dev` / `just prod` (or lives
# outside compose entirely). Targets therefore come from .env and are rendered
# by entrypoint.sh at container start — Prometheus does not expand env
# variables in its config.
#
# In-project defaults: BACKEND_METRICS_HOST=back, WORKER_METRICS_HOST=worker
#   (both services share `net` with Prometheus; no published port needed).
# External mode: override them with host.docker.internal or central hosts
#   (the app publishes its ports on loopback only, so a remote Prometheus needs
#   its own reachable address — deployment-specific).

global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/alert-rules.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
        labels:
          env: "${LOG_ENV}"

  # Stack liveness (G3): these jobs exist so up{} can cover the whole stack.
  - job_name: loki
    static_configs:
      - targets: ["loki:3100"]
        labels:
          env: "${LOG_ENV}"

  - job_name: alertmanager
    metrics_path: /metrics
    static_configs:
      - targets: ["alertmanager:9093"]
        labels:
          env: "${LOG_ENV}"

  - job_name: grafana
    static_configs:
      - targets: ["grafana:3000"]
        labels:
          env: "${LOG_ENV}"

  - job_name: docmasker-back
    metrics_path: /metrics
    static_configs:
      - targets: ["${BACKEND_METRICS_HOST}:${BACKEND_PORT}"]
        labels:
          env: "${LOG_ENV}"

  # The worker is long-running: scraped directly, NOT pushed to Pushgateway.
  - job_name: docmasker-worker
    metrics_path: /metrics
    static_configs:
      - targets: ["${WORKER_METRICS_HOST}:${WORKER_METRICS_PORT}"]
        labels:
          env: "${LOG_ENV}"

  # The backup sidecar exposes a narrow stdlib /metrics on the private `net`
  # (no host publication). Same env override pattern as the app targets.
  - job_name: docmasker-pg-backup
    metrics_path: /metrics
    static_configs:
      - targets: ["${BACKUP_METRICS_HOST}:${BACKUP_METRICS_PORT}"]
        labels:
          env: "${LOG_ENV}"
