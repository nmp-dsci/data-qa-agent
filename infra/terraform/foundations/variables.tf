variable "aws_region" {
  description = "AWS region for all data-qa-agent infrastructure."
  type        = string
  default     = "ap-southeast-2"
}

variable "aws_profile" {
  description = "Local AWS CLI profile. In CI this is unset (OIDC provides creds) — pass -var aws_profile=\"\" or set it null via TF_VAR_aws_profile."
  type        = string
  default     = "data-qa"
}

variable "project" {
  description = "Short project slug used as a prefix for resource names."
  type        = string
  default     = "data-qa"
}

variable "vpc_cidr" {
  description = "CIDR block for the project VPC."
  type        = string
  default     = "10.42.0.0/16"
}

# ---- Database (Aurora Serverless v2) -------------------------------------
variable "db_name" {
  description = "Initial database name. Matches the local stack (POSTGRES_DB=dataqa)."
  type        = string
  default     = "dataqa"
}

variable "db_master_username" {
  description = "Aurora master username. Matches the local admin role."
  type        = string
  default     = "postgres"
}

variable "db_engine_version" {
  description = "Aurora PostgreSQL engine version. Must support Serverless v2 scale-to-zero if min ACU = 0."
  type        = string
  default     = "16.6"
}

variable "db_min_acu" {
  description = "Aurora Serverless v2 minimum capacity (ACUs). 0 = scale-to-zero / auto-pause (near-$0 idle). Bump to 0.5 if an apply rejects 0 for the chosen engine version."
  type        = number
  default     = 0
}

variable "db_max_acu" {
  description = "Aurora Serverless v2 maximum capacity (ACUs)."
  type        = number
  default     = 2
}

# ---- Container images -----------------------------------------------------
variable "ecr_repositories" {
  description = "Service images pushed to ECR. Frontend is static (S3/CloudFront), so it is not here."
  type        = list(string)
  default     = ["backend-api", "data-agent", "data-pipeline", "db-migrate"]
}

variable "image_tag" {
  description = "Image tag the App Runner services + ECS jobs run. `latest` + App Runner auto-deploy = every push goes live; CI can pin a sha."
  type        = string
  default     = "latest"
}

# ---- App (Phase D) ---------------------------------------------------------
variable "google_client_id" {
  description = "Google OAuth Web client id (public; the ID-token audience). Same client as s11."
  type        = string
  default     = ""
}

variable "admin_emails" {
  description = "Comma-separated emails mapped to the admin role."
  type        = string
  default     = ""
}

variable "llm_provider" {
  description = "LLM provider the agent uses (deepseek | anthropic). The data-qa/llm-api-key secret feeds the matching *_API_KEY."
  type        = string
  default     = "deepseek"
}

# 0.25 vCPU / 0.5 GB is App Runner's smallest tier — fine for the thin API.
variable "backend_cpu" {
  type    = string
  default = "256"
}
variable "backend_memory" {
  type    = string
  default = "512"
}

# The agent loads a ~130MB ONNX embedding model and runs the Node/Pyodide
# sandbox — 0.5 GB would OOM. 1 vCPU / 2 GB is the safe start (~+$9/mo idle
# memory vs the smallest tier); downsize to 1 GB later if it proves roomy.
variable "agent_cpu" {
  type    = string
  default = "1024"
}
variable "agent_memory" {
  type    = string
  default = "2048"
}
