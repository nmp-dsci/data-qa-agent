# Terraform — data-qa-agent AWS deployment (s12)

Cloud infrastructure for deploying the local stack to AWS. Cloud-neutral Terraform;
the Azure Bicep in `../` stays as a reference and is **not** touched by this.

- **Account:** `089783391188`  ·  **Region:** `ap-southeast-2` (Sydney)  ·  **Profile:** `data-qa`
- **Database:** Aurora Serverless v2 (PostgreSQL 16, scale-to-zero)
- **Compute (later phases):** App Runner  ·  **Images:** ECR  ·  **Secrets:** Secrets Manager

## Layout

| Module | State | Run by | What it creates |
|--------|-------|--------|-----------------|
| `bootstrap/` | **local** | you, once, with admin creds | S3 state bucket + DynamoDB lock, GitHub-OIDC provider + CI deploy role |
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

Enable pgvector on the new cluster, exactly like the local pgvector container:

```bash
HOST=$(terraform output -raw aurora_endpoint)
# password: pull from Secrets Manager (never echo it into shell history in shared envs)
psql "host=$HOST port=5432 dbname=dataqa user=postgres sslmode=require" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;" -c "\dx"
```

Seeing `vector` in the extension list = Phase A done.

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
