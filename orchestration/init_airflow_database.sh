#!/bin/sh
set -eu

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${AIRFLOW_DB:?AIRFLOW_DB is required}"

case "${AIRFLOW_DB}" in
    *[!A-Za-z0-9_]*)
        echo "AIRFLOW_DB must contain only letters, numbers, and underscores." >&2
        exit 2
        ;;
esac

export PGPASSWORD="${POSTGRES_PASSWORD}"

until pg_isready -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" -d postgres; do
    sleep 1
done

database_exists="$(
    psql -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname = '${AIRFLOW_DB}'"
)"

if [ "${database_exists}" = "1" ]; then
    echo "Airflow metadata database ${AIRFLOW_DB} already exists."
else
    createdb -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" "${AIRFLOW_DB}"
    echo "Created Airflow metadata database ${AIRFLOW_DB}."
fi
