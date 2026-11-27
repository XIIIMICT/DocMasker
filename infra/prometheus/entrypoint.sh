#!/bin/sh
# Render prometheus.yml from prometheus.yml.tpl at container start.
#
# Prometheus expands ${VAR} only in a few fields, never in scrape target
# addresses. Rendering here (the Alertmanager entrypoint pattern) keeps the
# repo template usable both for the in-project stack (service names) and for
# external/central targets (host.docker.internal or a server address).
set -eu

: "${BACKEND_METRICS_HOST:?BACKEND_METRICS_HOST is empty — backend scrape target is invalid}"
: "${BACKEND_PORT:?BACKEND_PORT is empty — backend scrape target is invalid}"
: "${WORKER_METRICS_HOST:?WORKER_METRICS_HOST is empty — worker scrape target is invalid}"
: "${WORKER_METRICS_PORT:?WORKER_METRICS_PORT is empty — worker scrape target is invalid}"
: "${BACKUP_METRICS_HOST:?BACKUP_METRICS_HOST is empty — backup scrape target is invalid}"
: "${BACKUP_METRICS_PORT:?BACKUP_METRICS_PORT is empty — backup scrape target is invalid}"
: "${LOG_ENV:?LOG_ENV is empty — scrape labels would be meaningless}"

# '/' and '@' can appear in a host; use '|' as sed delimiter. Values come from
# compose/.env, not from a request, and are validated above.
sed \
  -e "s|\${BACKEND_METRICS_HOST}|${BACKEND_METRICS_HOST}|g" \
  -e "s|\${BACKEND_PORT}|${BACKEND_PORT}|g" \
  -e "s|\${WORKER_METRICS_HOST}|${WORKER_METRICS_HOST}|g" \
  -e "s|\${WORKER_METRICS_PORT}|${WORKER_METRICS_PORT}|g" \
  -e "s|\${BACKUP_METRICS_HOST}|${BACKUP_METRICS_HOST}|g" \
  -e "s|\${BACKUP_METRICS_PORT}|${BACKUP_METRICS_PORT}|g" \
  -e "s|\${LOG_ENV}|${LOG_ENV}|g" \
  /etc/prometheus/prometheus.yml.tpl > /tmp/prometheus.yml

# Fail loudly on an unresolved placeholder instead of silently scraping
# a literal "${...}" host forever.
if grep -q '\${' /tmp/prometheus.yml; then
  echo "prometheus entrypoint: unresolved variable in /tmp/prometheus.yml" >&2
  exit 1
fi

/bin/promtool check config /tmp/prometheus.yml

# Compose command supplies storage/retention/lifecycle flags as "$@".
exec /bin/prometheus --config.file=/tmp/prometheus.yml "$@"
