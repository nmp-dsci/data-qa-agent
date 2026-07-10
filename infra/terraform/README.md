# Terraform — data-qa-agent AWS deployment (s12)

Cloud infrastructure for deploying the local stack to AWS. Cloud-neutral Terraform;
the Azure Bicep in `../` stays as a reference and is **not** touched by this.

- **Account:** `089783391188`  ·  **Region:** `ap-southeast-2` (Sydney)  ·  **Profile:** `data-qa`
- **Database:** Aurora Serverless v2 (PostgreSQL 16, scale-to-zero)
- **Compute:** App Runner (backend-api + data-agent) + ECS Fargate one-shot jobs (migrate, pipeline)
- **Frontend:** static Vite build in S3 behind CloudFront  ·  **Images:** ECR  ·  **Secrets:** Secrets Manager

## Layout

| Module | State | Run by | What it creates |
|--------|-------|--------|-----------------|
| `bootstrap/` | **local** | you, once, with admin creds | S3 state bucket (S3-native locking), GitHub-OIDC provider + CI deploy role |
| `foundations/` | remote (S3) | you, or CI on merge (`deploy-aws.yml`) | VPC + subnets, Aurora Serverless v2, ECR repos, Secrets Manager entries, S3 data bucket, App Runner services, ECS one-shot jobs, S3+CloudFront frontend, CloudWatch alarms + SNS |

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

### 2. Foundations

The backend bucket name is already wired in `foundations/backend.tf`
(`data-qa-tfstate-089783391188`).

```bash
cd ../foundations
terraform init            # connects to the remote backend from step 1
terraform plan            # review — nothing billable until apply
terraform apply
```

### 3. Verify (Phase A definition-of-done)

`terraform apply` completing cleanly (VPC, Aurora, ECR, Secrets, S3 in the
outputs) **is** Phase A done.

Note on pgvector: the cluster is **private by design** (no public access; the
security group only admits traffic from inside the VPC), so you can't `psql` to
it from your laptop — and you don't need to. The `vector` + `pgcrypto`
extensions are created by the **db-migrate job** as part of `alembic upgrade
head` (revision `0001` executes `db/init/01_schema.sql`), which runs *inside*
the VPC in Phase D. That is where pgvector is confirmed. Aurora PostgreSQL 16
allow-lists `vector`, and the job connects as the master role, so the
`CREATE EXTENSION` succeeds there.

## Deploying the app (Phases C–E)

Merging to `main` is the push-button deploy: `.github/workflows/deploy-aws.yml`
(also runnable via *workflow_dispatch*) assumes the OIDC role, then runs
build/push → `terraform apply` → the migrate job → the frontend deploy → the
cloud smoke test. The same steps run manually via the scripts (each defaults to
the `data-qa` SSO profile and the Terraform outputs):

```bash
./scripts/aws_build_push.sh     # build the 4 service images (linux/amd64) → ECR
./scripts/run_job.sh migrate    # one-shot ECS job: alembic upgrade head
./scripts/run_job.sh pipeline   # one-shot ECS job: dlt + dbt (full CSVs from S3)
VITE_API_URL=$(terraform -chdir=infra/terraform/foundations output -raw backend_api_url) \
  ./scripts/deploy_frontend.sh  # Vite build → S3 + CloudFront invalidation
./scripts/cloud_smoke.sh        # health, auth, token guard, governed SQL, frontend
```

Pushing `:latest` auto-deploys both App Runner services. The pipeline job reads
the full CSVs from the `data-qa-source-data-*` S3 bucket (`DATA_S3_BUCKET`)
instead of local disk.

## Notes / knobs

- **Scale-to-zero:** `db_min_acu = 0` (near-$0 when idle). If an apply rejects `0`
  for the engine version, set `-var db_min_acu=0.5` (or bump `db_engine_version`).
- **The LLM API key** is the only secret you set by hand, in Phase D:
  ```bash
  aws secretsmanager put-secret-value \
    --secret-id data-qa/llm-api-key \
    --secret-string 'sk-...' \
    --profile data-qa --region ap-southeast-2
  ```
  DB password + `JWT_SECRET` are Terraform-generated into Secrets Manager.
- **Alarms (Phase E):** CloudWatch alarms (billing > `billing_alarm_usd` USD in
  us-east-1; backend/agent ≥ 5 5xx per 5 min) notify `alert_email` via SNS. The
  email subscription needs a one-time confirmation click, and the billing metric
  needs "Receive Billing Alerts" enabled once in Billing → Preferences.
- **Tear down:** `terraform destroy` in `foundations/` removes everything here and
  rebuilds cleanly from Alembic migrations. The state bucket is `prevent_destroy`.
- **CI (Phase E):** `deploy-aws.yml` assumes `data-qa-github-deploy` via OIDC — no keys.
