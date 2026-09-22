# Terraform — data-qa-agent AWS deployment

Cloud infrastructure for the walk-in portfolio demo. Since s52 the deployed
shape is the same as transcript-rag-agent's: **one App Runner service, no VPC,
no database, no Secrets Manager**. Chat replays a recorded pack baked into the
image; the exhibit tabs read a static JSON dump shipped with the frontend
(`frontend/public/exhibits/`, produced by `make export-exhibits` from the local
dev stack). Postgres, the SQL editor, Explore and the eval loop live in local
dev only (`make up`).

- **Account:** `089783391188`  ·  **Region:** `ap-southeast-2` (Sydney)  ·  **Profile:** `data-qa`
- **Compute:** App Runner `data-qa-demo` (0.25 vCPU / 512 MB, min = max = 1)
- **Frontend:** static Vite build in S3 behind CloudFront  ·  **Images:** ECR `data-qa/demo`
- **Run-rate:** ≈$5/month (App Runner provisioned memory + ECR + GST); see `.lavish/s52_aws-cost-profile.html`

## Layout

| Module | State | Run by | What it creates |
|--------|-------|--------|-----------------|
| `bootstrap/` | **local** | you, once, with admin creds | S3 state bucket (S3-native locking), GitHub-OIDC provider + CI deploy role |
| `demo/` | remote (S3, `demo/terraform.tfstate`) | CI on merge (`deploy-aws.yml`), or you | ECR repo, App Runner service + autoscaling config, S3+CloudFront frontend, SNS alert topics, 5xx + billing alarms |
| `foundations/` | remote (S3, `foundations/terraform.tfstate`) | **retired (s52)** — destroy only | the Aurora-backed stack: VPC, Aurora Serverless v2, Secrets Manager, ECS one-shot jobs, App Runner backend-api. Kept in the repo until the destroy has run; see the cutover runbook below |

`bootstrap` uses local state because it creates the very bucket the others use as a
remote backend (chicken-and-egg). Everything else stores state in that bucket.

## First-time run order

Make sure your SSO session is live and exported (the S3 backend reads the env
var, not the provider block): `aws sso login --profile data-qa && export AWS_PROFILE=data-qa`.
CI needs neither — GitHub-OIDC credentials are picked up automatically.

### 1. Bootstrap (once)

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply           # creates state bucket, lock table, OIDC role
```

Commit nothing from here except code — the local `terraform.tfstate` this writes is
gitignored. (After bootstrap you may optionally migrate this module's own state into
the bucket, but it's fine to leave local.)

### 2. Demo stack

```bash
cd ../demo
terraform init
terraform apply -target=aws_ecr_repository.demo   # the repo must exist before the first push
../../../scripts/aws_build_push.sh                 # the image must exist before the service can start
terraform apply                                    # everything else (imports included, first time)
```

The first apply also **imports** the frontend bucket, CloudFront distribution,
origin access control, bucket policy and both SNS alert topics/subscriptions
from the retired `foundations/` state (the `import` blocks in `demo/main.tf`),
so the public URL `https://deqfc8b0u8s64.cloudfront.net` and the alarm email
survive the cutover. After that first apply the `import` blocks are inert.

### 3. Cutover runbook (s52, one-time)

Order matters: the new stack must be serving before the old one is destroyed,
and the resources shared between them must be forgotten by `foundations/`
before `terraform destroy` runs there — otherwise the destroy deletes the
frontend bucket and the alert topics the demo stack just imported.

1. **Snapshot** the cluster (done 2026-09-22: `data-qa-aurora-pre-dbless-20260922`).
   `aurora.tf` has `skip_final_snapshot = true`, so this is the only copy.
2. **Merge** the s52 PR. `deploy-aws.yml` applies `demo/`, starts the App Runner
   deployment, rebuilds the frontend against the new backend URL, and smokes it.
   `foundations/` keeps running untouched — the two stacks coexist.
3. **Verify** the live site by hand (door → chat → exhibit tabs).
4. **Forget the shared resources** in the old state, then destroy the rest:
   ```bash
   cd infra/terraform/foundations && export AWS_PROFILE=data-qa
   terraform init
   for r in aws_s3_bucket.frontend aws_s3_bucket_public_access_block.frontend \
            aws_cloudfront_origin_access_control.frontend aws_cloudfront_distribution.frontend \
            aws_s3_bucket_policy.frontend aws_sns_topic.alerts aws_sns_topic.alerts_use1 \
            aws_sns_topic_subscription.alerts_email aws_sns_topic_subscription.alerts_email_use1 \
            aws_s3_bucket.source_data aws_s3_bucket_versioning.source_data \
            aws_s3_bucket_server_side_encryption_configuration.source_data \
            aws_s3_bucket_public_access_block.source_data; do
     terraform state rm "$r"
   done
   terraform plan -destroy      # read it: no bucket, distribution or topic may appear
   terraform destroy
   ```
   The source-data bucket is forgotten rather than destroyed: it holds the raw
   CSVs and costs cents. Aurora, the VPC, the 12 secrets, the ECS cluster, the
   public IPv4 and the old `data-qa-backend-api` service all go.
5. **Delete `foundations/`** from the repo (and `scripts/run_job.sh`,
   `scripts/rollback_apprunner.sh`, `scripts/ops_ingest.py`, the `db-migrate` /
   `data-pipeline` Dockerfiles' ECR wiring) in a follow-up commit.
6. Untracked leftovers worth a look: CloudFront distribution `E1G4HP2K4CRHKI`
   (`d3trakue69dnqh.cloudfront.net`) was never in either state — a stray from
   an errored apply — and the old ECR repos' images (≈40 GB of layers) vanish
   with the foundations destroy.

## Deploying the app

Merging to `main` is the push-button deploy: `.github/workflows/deploy-aws.yml`
(also runnable via *workflow_dispatch*) assumes the OIDC role, then runs
build/push → `terraform apply` (demo) → `start-deployment` → the frontend
deploy → the cloud smoke test. About five minutes. The same steps run manually
via the scripts (each defaults to the `data-qa` SSO profile and the Terraform
outputs):

```bash
./scripts/aws_build_push.sh     # build the demo image (linux/amd64) → ECR data-qa/demo
aws apprunner start-deployment --service-arn "$(terraform -chdir=infra/terraform/demo output -raw apprunner_service_arn)"
./scripts/wait_apprunner.sh     # until the service is RUNNING on the new image
VITE_API_URL=$(terraform -chdir=infra/terraform/demo output -raw backend_api_url) \
  ./scripts/deploy_frontend.sh  # Vite build (+ exhibits/) → S3 + CloudFront invalidation
./scripts/cloud_smoke.sh        # health, demo door, a replayed answer, the DB-less contract, frontend + exhibit dump
```

`auto_deployments_enabled` is **off** on the service (App Runner bills $1/month
per service for the ECR-push trigger), which is why the release step is an
explicit `start-deployment`. Refreshing the exhibit tabs is a frontend deploy:
run `make export-exhibits` against the local dev stack, commit
`frontend/public/exhibits/`, merge.

### MCP surface (s35 rung 3, mounted in s36)

There is no separate MCP service to deploy. The surface is mounted on
backend-api at `/mcp`, and the **client** authenticates with a `dpk_` key minted
for `surface='mcp'` — so there is no server-side key for Terraform to hold, and
nothing here to configure. Mint a key via the admin API (`POST
/admin/service-accounts`, or the Settings tab) and hand it to whoever runs the
client.

`var.mcp_allowed_hosts` is gone with it. The SDK's DNS-rebinding host allowlist
is now defence-in-depth rather than the primary control (the key gate sits in
front of the transport), and it is off unless `MCP_ALLOWED_HOSTS` is set on
backend-api. That removes the two-step apply the standalone service needed: App
Runner assigns a hostname at create time and a service cannot reference its own
`service_url`, so the allowlist could not be derived here and every remote
request `421`'d until a second apply.

`SLACK_SIGNING_SECRET` follows a "placeholder, set by hand" pattern — it comes
from a Slack app's config, not from Terraform.
Unset/placeholder closes the Slack endpoint with a 404, the correct default
for an environment not wired to a workspace.

## Notes / knobs

- **No secrets, by construction.** The image holds no keys; `JWT_SECRET` is
  generated per process when `DB_DISABLED=1` (sessions belong to one constant
  visitor). `AUTH_MODE=google` with no `GOOGLE_CLIENT_ID` keeps `dev-login`
  closed (403) without advertising an owner door.
- **Alarms:** `data-qa-demo-5xx` (≥ 5 5xx per 5 min) and the whole-account
  `data-qa-billing-over-30usd` notify `alert_email` via the imported SNS topics.
  The billing metric needs "Receive Billing Alerts" enabled once in Billing →
  Preferences (already done).
- **Tear down:** `terraform destroy` in `demo/` removes the service, repo and
  alarms; the frontend bucket must be emptied first (`aws s3 rm --recursive`).
  The state bucket is `prevent_destroy`.
- **CI:** `ci.yml` runs `terraform fmt`/`validate` on every module and a
  `plan` of `demo/` on PRs; `deploy-aws.yml` assumes `data-qa-github-deploy`
  via OIDC — no keys.
