# PostgreSQL Backup and Restore

## Objective

Define a repeatable backup and restore process for Railway-hosted PostgreSQL.

## Backup Strategy

1. **Managed snapshots**  
   Enable Railway PostgreSQL automated backups (daily minimum).
2. **Logical export fallback**  
   Nightly `pg_dump` to encrypted object storage.
3. **Retention policy**  
   - Daily backups: 14 days
   - Weekly backups: 8 weeks
   - Monthly backups: 6 months

## Manual Backup (Logical)

```bash
pg_dump "$DATABASE_URL" --format=custom --no-owner --no-privileges --file "double_edge_$(date +%Y%m%d_%H%M).dump"
```

## Restore Drill (Staging)

1. Provision a staging PostgreSQL database.
2. Restore latest backup:

```bash
pg_restore --clean --if-exists --no-owner --no-privileges --dbname "$STAGING_DATABASE_URL" latest.dump
```

3. Apply latest migrations:

```bash
cd backend
alembic upgrade head
```

4. Validate:
   - API health endpoint is green
   - Authentication works
   - `GET /api/v1/jobs/summary` responds
   - Lead and building list endpoints return data

## Recovery RTO/RPO Targets

- **RTO:** 60 minutes
- **RPO:** 24 hours

## Operational Checklist

- [ ] Backups enabled in Railway project settings
- [ ] Nightly logical dumps scheduled
- [ ] Restore drill executed at least monthly
- [ ] Restore drill outcome logged in session notes/runbook
