# Terraform — data-qa-agent AWS deployment (s12)

Cloud infrastructure for deploying the local stack to AWS. Cloud-neutral Terraform;
the Azure Bicep in `../` stays as a reference and is **not** touched by this.

- **Account:** `089783391188`  ·  **Region:** `ap-southeast-2` (Sydney)  ·  **Profile:** `data-qa`
- **Database:** Aurora Serverless v2 (PostgreSQL 16, scale-to-zero)
- **Compute (later phases):** App Runner  ·  **Images:** ECR  ·  **Secrets:** Secrets Manager

## Layout

| Module | State | Run by | What it creates |
|--------|-------|--------|-----------------|
| `bootstrap/` | **local** | you, once, with admin creds | S3 state bucket (S3-native locking), GitHub-OIDC provider + CI deploy role |
| `foundations/` | remote (S3) | you now; CI later | VPC + subnets, Aurora Serverless v2, ECR repos, Secrets Manager entries, S3 data bucket |

`bootstrap` uses local state because it creates the very bucket the others use as a
remote backend (chicken-and-egg). Everything else stores state in that bucket.

## First-time run order

Make sure your SSO session is live: `aws sso login --profile data-qa`.

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
- **Tear down:** `terraform destroy` in `foundations/` removes everything here and
  rebuilds cleanly from Alembic migrations. The state bucket is `prevent_destroy`.
- **CI (Phase E):** GitHub Actions assumes `data-qa-github-deploy` via OIDC — no keys.
