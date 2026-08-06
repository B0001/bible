# Kubernetes deployment

The Dash app as a Deployment behind a Service and Ingress. This is the only
long-running service among these repos; the sibling projects are batch Jobs.

```bash
docker build -t bible-reader:0.1.0 .
kubectl apply -k k8s/overlays/dev     # namespace bible-dev
kubectl apply -k k8s/overlays/prod    # namespace bible-prod
```

## Layout

| Path | Purpose |
|---|---|
| `base/` | Deployment, Service, Ingress, ConfigMap, PVC |
| `overlays/dev/` | 1 worker, small resources, `bible-dev.example.com`, `imagePullPolicy: Always` |
| `overlays/prod/` | 4 workers, HPA, PDB, TLS, RWX volume, topology spread |

## Probes

All three hit `/health` (`dash_app.py:244`), a plain 200 from the Flask server
under Dash. It deliberately does not touch the CSVs or SQLite, so it reports
"process is serving" rather than "data is good". Data loads at *import* time,
before gunicorn binds the port, so a successful connect already proves the load
worked — that is what `startupProbe` is really waiting out (24 × 5s = 2 min).

## Read tracking is in Postgres — the pods are stateless

There is no PersistentVolumeClaim here, and that is the point. Read tracking
used to be SQLite on a shared volume, which capped the deployment at one replica
for **correctness**, not capacity: SQLite allows a single writer, ReadWriteOnce
volumes cannot attach to pods on two nodes, and SQLite's POSIX advisory locking
is unreliable over NFS (`database is locked`, or worse).

`dash_app` now selects its backend from `DATABASE_URL`:

| `DATABASE_URL` | Backend | Used by |
|---|---|---|
| set, `postgres://…` | Postgres, pooled | Kubernetes |
| unset | SQLite at `READS_DB` | local dev, tests |

The DSN comes from a **Secret** (`bible-reader-db`, key `url`), not the
ConfigMap. `READS_DB` is deliberately *not* set in the ConfigMap: if the Secret
were missing and `READS_DB` were set, the app would quietly fall back to
per-pod SQLite and every replica would show a different set of read verses.
Leaving it unset makes that failure visible instead.

### The Secret

**dev** creates it, along with an in-cluster Postgres (`postgres.yaml`:
StatefulSet + Service + Secret) so `kubectl apply -k k8s/overlays/dev` works with
nothing external. That Postgres is dev-only — one replica, no backups, no
failover, password in git.

**prod ships neither.** It expects a managed instance (RDS, Cloud SQL, an
operator) and a Secret created out of band:

```bash
kubectl create secret generic bible-reader-db -n bible-prod \
  --from-literal=url='postgresql://user:pass@host:5432/reads?sslmode=require'
```

Note this Secret is *not* name-suffixed by Kustomize in prod, because it is not
one of the overlay's resources.

### Connection budget is the new ceiling

Peak connections = `replicas × GUNICORN_WORKERS × READS_POOL_MAX`. At the prod
HPA's `maxReplicas: 12` that is `12 × 4 × 4 = 192`, which **exceeds a stock
Postgres `max_connections` of 100**. Before raising `maxReplicas`, do one of:
put PgBouncer in front, raise `max_connections`, or lower `READS_POOL_MAX`.
Otherwise pods fail to acquire connections and read tracking degrades to empty.

### Failure behaviour

If Postgres is unreachable, read tracking degrades to "nothing marked read" and
logs a warning — the reader keeps serving verses. The probes therefore
deliberately do **not** check the database: failing readiness on a database blip
would take down a service that is still perfectly able to serve.

The schema (`CREATE TABLE IF NOT EXISTS reads`) is applied on first use, so no
migration step is needed. There is no automated migration of existing SQLite
rows — if you have a `reads.db` worth keeping, copy it across before cutover.

## Configuration

Env comes from the `bible-reader-env` ConfigMap. `DASH_DEBUG` must stay `"0"` —
Dash debug mode exposes an interactive traceback console, which is remote code
execution for anyone who can reach the pod.

Because the ConfigMap is a plain resource rather than a generated one, it has no
content hash, so editing it does **not** trigger a rollout. Follow a config
change with:

```bash
kubectl rollout restart deployment/bible-reader-prod -n bible-prod
```

## Before deploying

- Replace `bible.example.com` / `bible-dev.example.com` with real hosts.
- `ingressClassName: nginx` and the `cert-manager.io/cluster-issuer` annotation
  assume ingress-nginx and cert-manager. Adjust for your cluster.
- Pin the image by digest in prod (`newName@sha256:…`); a moving tag makes the
  running revision unidentifiable after the fact.
- The image bakes in only the graded *sample* data. Real Hebrew/Greek corpora
  need `scripts/convert_*.py` + `parser.py` output mounted into `out/`, and the
  memory request raised — polars holds the graded corpus in memory.
