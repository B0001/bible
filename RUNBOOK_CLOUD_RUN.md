# Cloud Run runbook: Bible Dash

How to find, rebuild, verify and roll back the Dash app's Cloud Run service.
Written 2026-11-16 for bead `portfolio-6hc.11`.

**Status of this document.** The repository holds no Cloud Run configuration (no
service YAML, no `gcloud` script, no project, region or service name), and this
runbook was written without cloud access. The build and verification steps were
run locally against `origin/main` at `c57e2c0`; the `gcloud` commands were not
run. Step 1 fills in the names the rest depends on. Record them in the table at
the end the first time you go through it.

## Why a rebuild is needed

`main` grades `sample/kjv_sample.txt` (12 public-domain KJV verses) into the
image. Any image built before `151f51a` (2026-10-30) grades
`sample/nasb_sample.txt` instead, so a service still running one serves NASB
text, which is Lockman Foundation copyright. Redeploying from `main` removes it.

## 0. Before you start

```bash
gcloud auth login
gcloud config set project <PROJECT_ID>
```

**Build from a clean checkout of `origin/main`, never from a working directory.**
`~/Downloads/bible` has been left on old branches (`rhyme-runs` on 2026-11-16)
whose tree still contains `sample/nasb_sample.txt` and a Dockerfile that grades
it. Building there re-bakes NASB into the image.

```bash
git clone --depth 1 --branch main https://github.com/B0001/bible.git /tmp/bible-build
cd /tmp/bible-build
git log --oneline -1          # note the commit: it becomes the image tag
test ! -e sample/nasb_sample.txt && grep -c kjv_sample Dockerfile   # expect 1
```

## 1. Find the service and record its current state

```bash
gcloud run services list --platform managed                 # name, region, URL
gcloud run services describe <SERVICE> --region <REGION> --format yaml > /tmp/service-before.yaml
gcloud run revisions list --service <SERVICE> --region <REGION>
```

If `services list` shows nothing in this project, check the others
(`gcloud projects list`). If no project has it, there is no deployment: nothing
is serving NASB, skip to "Request count" and close the bead with that finding.

From `service-before.yaml`, note:

| What | Where | Why it matters |
|---|---|---|
| Image | `spec.template.spec.containers[0].image` | The rollback target, and where to push the new one |
| Revision name | `status.latestReadyRevisionName` | The rollback target |
| Container port | `containers[0].ports[0].containerPort` | Must be 8050 (see below) |
| Env vars | `containers[0].env` | Carry them over; see the two to check |
| Ingress, auth | `metadata.annotations`, IAM policy | Do not change them by accident |

Two env vars to check:

- **`BIBLE_GRADED_CSV`.** If it is set to `out/nasb_graded.csv`, remove it or set
  it to `out/kjv_graded.csv`. The new image has no NASB file. (It is only a
  fallback when `bibles.toml` yields no entry, and the `kjv` entry does resolve
  in the image, but a stale value should not survive.)
- **`DATABASE_URL` / `READS_DB`.** Read tracking uses Postgres if `DATABASE_URL`
  is set, else SQLite at `READS_DB`. The image sets neither, and `/app` is owned
  by root while the process runs as uid 10001, so the default `reads.db` in
  `/app` is not writable. A service with neither variable needs
  `READS_DB=/tmp/reads.db` (per-instance, lost on restart) or a real
  `DATABASE_URL`. Keep whatever the running service already uses.

## 2. Build and push

The image must be `linux/amd64`. Cloud Build produces that; a local build on an
Apple-silicon Mac does not unless told to.

```bash
TAG=$(git rev-parse --short HEAD)
IMAGE=<REGION>-docker.pkg.dev/<PROJECT_ID>/<REPOSITORY>/bible-reader:$TAG

# Either: Cloud Build
gcloud builds submit --tag "$IMAGE" .

# Or: local build
docker build --platform linux/amd64 -t "$IMAGE" .
docker push "$IMAGE"
```

Use the registry path the current image already lives in (step 1).

## 3. Check the image before deploying it

```bash
docker run --rm --entrypoint sh "$IMAGE" -c '
  ls sample out
  grep -rli "lockman\|formless and void" /app || echo "no NASB text"
  wc -l out/kjv_graded.csv'
```

Expected: `sample/` has `kjv_sample.txt` and no `nasb_sample.txt`; `out/` has
only `kjv_graded.csv`, 13 lines; `no NASB text`. The words "NASB" and "Lockman"
do appear in two comments (`bibles.toml`, `scripts/convert_scrollmapper.py`);
those are not scripture text.

Optional local smoke test:

```bash
docker run --rm -p 8050:8050 -e READS_DB=/tmp/reads.db "$IMAGE" &
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8050/health   # 200
```

## 4. Deploy without traffic, verify, then shift

```bash
gcloud run deploy <SERVICE> --region <REGION> --image "$IMAGE" \
  --port 8050 --no-traffic --tag candidate
```

- `--port 8050`: the container listens on `DASH_PORT`, default 8050. Cloud Run's
  default is 8080, and a revision started on the wrong port never becomes ready.
- `deploy` keeps the service's existing env vars, scaling and auth settings. Add
  `--remove-env-vars BIBLE_GRADED_CSV` or `--update-env-vars ...` only for the
  fixes found in step 1.
- `--no-traffic --tag candidate` gives the new revision its own URL (printed by
  the command) while the old one keeps serving.

Verify the candidate URL:

```bash
U=<candidate URL>
curl -s -o /dev/null -w '%{http_code}\n' "$U/health"                  # 200
curl -s "$U/_dash-layout" | grep -o -i 'nasb\|lockman\|King James Version (English)' | sort | uniq -c
```

Expected: `King James Version (English)` appears and `nasb` / `lockman` do not.
`/_dash-layout` carries the bible picker and its attributions, so it lists every
text the revision serves. (Checked locally against `main` on 2026-11-16: `/health`
returned 200 and the layout listed only the KJV entry.) Then open the URL in a
browser: Genesis 1:1 should end "the heaven and the earth" (KJV); NASB reads
"the heavens and the earth".

If the service requires authentication, add
`-H "Authorization: Bearer $(gcloud auth print-identity-token)"` to the curls.

Shift traffic:

```bash
gcloud run services update-traffic <SERVICE> --region <REGION> --to-latest
```

Re-run the two curls against the service's main URL.

## 5. Remove the NASB images

The copyright fix is not complete while an old revision or image can still be
served or pulled.

```bash
gcloud run revisions list --service <SERVICE> --region <REGION>
gcloud run revisions delete <OLD_REVISION> --region <REGION>      # each pre-151f51a revision
gcloud artifacts docker images list <REGION>-docker.pkg.dev/<PROJECT_ID>/<REPOSITORY>/bible-reader
gcloud artifacts docker images delete <OLD_IMAGE>@<DIGEST>
```

Keep the old revision until step 4 has passed on the main URL. After deleting
it, the rollback below has nothing to roll back to, which is intended: the only
earlier state is the one being removed.

## Rollback

Before step 5 only, and only for a broken deploy, not as a place to stay:

```bash
gcloud run services update-traffic <SERVICE> --region <REGION> --to-revisions <OLD_REVISION>=100
```

This puts NASB text back in service. Fix forward and redeploy as soon as possible.

## Request count (30-day usage)

The portfolio plan keeps this app frozen unless it has more than 1,000 monthly
active users. Cloud Run keeps request logs for 30 days by default:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="<SERVICE>" AND httpRequest.requestUrl:"_dash-update-component"' \
  --freshness=30d --format='value(httpRequest.remoteIp)' | sort -u | wc -l
```

That counts distinct client IPs that interacted with the app, a rough upper
bound on users. Dropping the `requestUrl` clause counts every caller, including
health checks and crawlers. Cloud Console > Cloud Run > the service > Metrics
shows the request count without a query.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Revision never becomes ready, "failed to listen on PORT" | `--port 8050` missing |
| `exec format error` in the revision log | arm64 image; rebuild with `--platform linux/amd64` |
| Read marks do not stick, or differ between requests | SQLite in `/tmp` on more than one instance; use `DATABASE_URL` or `--max-instances 1` |
| `unable to open database file` | Neither `DATABASE_URL` nor a writable `READS_DB` is set |
| Picker shows only KJV | Expected: the image bundles only the KJV sample. Other texts need their graded CSVs in `out/` |

## Deployment record

Fill in on first use.

| Field | Value |
|---|---|
| Project ID | |
| Region | |
| Service name | |
| Service URL | |
| Artifact Registry repository | |
| Read-tracking backend (`DATABASE_URL` or `READS_DB`) | |
| Last deployed commit and date | |
