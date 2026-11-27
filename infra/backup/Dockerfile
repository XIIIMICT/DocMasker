# PostgreSQL logical backup sidecar.
#
# The base image supplies pg_dump / pg_restore / psql / createdb / dropdb at the
# same major version as the local stand (18). A pg_dump client may target an
# OLDER server, so this image can also back up an external production server;
# the reverse is not true, which is why no generic client image is used.
# python3 is added only to run the stdlib-only scheduler and /metrics endpoint.
FROM postgres:18-alpine

RUN apk add --no-cache python3

WORKDIR /opt/pgbackup
COPY pg_backup.py /opt/pgbackup/pg_backup.py

RUN mkdir -p /var/backups/docmasker \
    && chown postgres:postgres /var/backups/docmasker

# Dumps are written by the unprivileged `postgres` account.
USER postgres
EXPOSE 9113

ENTRYPOINT ["python3", "/opt/pgbackup/pg_backup.py"]
