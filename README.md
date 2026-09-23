# Zepic Data Platform

DE flagship — Phase 1.2: a local PostgreSQL operational source.

## Start

Requires Docker Desktop (or Docker Engine) running with Compose support.
Run commands from this repository root. A local `.env` has been created for
this checkout; for a fresh clone, copy `.env.example` to `.env` and replace
the example password first. `.env` is ignored by Git.

```sh
docker compose config --quiet
docker compose up -d --wait
docker compose ps
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"'
```

PostgreSQL 16 listens on localhost only. `POSTGRES_PORT` changes the host port;
the container port stays 5432. `POSTGRES_HOST` is for future host-side Python
clients; another Compose service would use hostname `postgres`.

On a Mac where Docker Desktop is installed but the Compose plugin is not on
the CLI search path, replace `docker compose` with:

```sh
/Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose
```

## Files

- `docker-compose.yml`: PostgreSQL service, healthcheck and persistent volume.
- `database/schema.sql`: agreed 9-table schema, indexes and triggers.
- `database/verify.sql`: SQL smoke checks for a disposable initialized database.
- `scripts/`, `src/`: reserved for later simulator implementation.
- `PROJECT_HANDOFF.md`: locked business decisions and phase boundary.

## Verify

Run on a disposable initialized database. Test rows roll back, but PostgreSQL
identity sequence increments do not roll back. Do not use on populated data:
some negative test statements deliberately target whole tables.

```sh
docker compose exec -T postgres sh -c 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < database/verify.sql
```

Expected: 9 tables, 32 indexes (including PK/UNIQUE indexes), 10 triggers,
and a final `PASS` message. A healthcheck alone does not verify the schema.

## Persistence and schema changes

`docker compose down` removes the container but retains database data.
The initialization SQL runs only when the data volume is empty. Editing
`schema.sql` or changing initialization credentials in `.env` does not modify
an existing database. SQL initialization is transactional and fails on errors.

Only when intentionally discarding **all local database data**, reset with
`docker compose down -v`, followed by `docker compose up -d --wait`.
An initialization failure may also require this reset for a disposable volume.
Use migrations once data must be preserved.
