provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Ticket    = "s12"
      Module    = "bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # State bucket name must be globally unique — scope it with the account id.
  state_bucket_name = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}"
}

# --------------------------------------------------------------------------
# Remote state backend: a single versioned, encrypted S3 bucket. State
# locking uses S3-native locking (backend `use_lockfile = true`) — no
# DynamoDB table needed. Every other module points its backend here.
# --------------------------------------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  bucket = local.state_bucket_name

  # State is precious — do not let `terraform destroy` nuke it by accident.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --------------------------------------------------------------------------
# GitHub Actions OIDC: a provider + an IAM role the CI workflow assumes with
# NO long-lived keys. Phase E's deploy-aws.yml federates into this role.
# --------------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS validates GitHub's cert via its own trust store; these thumbprints are
  # the long-standing GitHub Actions values and are effectively a formality.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Only workflows from this repo may assume the role.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${var.project}-github-deploy"
  description        = "Assumed by GitHub Actions (OIDC) to deploy ${var.project}."
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

# --------------------------------------------------------------------------
# The CI deploy role's permissions (s32 W4, decision Q6) — closing the Phase-F
# TODO that read "replace AdministratorAccess with a scoped policy".
#
# WHY IT WAS BROAD. The role has to manage the entire stack through Terraform:
# VPC, Aurora, App Runner, ECS, ECR, S3, CloudFront, Secrets Manager, CloudWatch,
# SNS, and the IAM roles those services assume. AdministratorAccess was the
# expedient way to make that work on day one, at the cost of a CI credential that
# could do literally anything in the account.
#
# WHAT SCOPING BUYS. Not much against a determined attacker who controls the
# workflow — a role that can write App Runner config can already run arbitrary
# code in the account's services. What it does buy is real: a blast-radius cap on
# *mistakes* (a bad `terraform apply` cannot delete the state bucket or detach the
# billing alarms), and removal of the two capabilities that turn a CI compromise
# into a durable one — creating IAM users/access keys, and granting itself more.
# That is the honest value, so those are the boundaries drawn below.
#
# HOW TO ROLL BACK. Set `deploy_role_scoped = false` and apply: the role reverts
# to AdministratorAccess. Kept as a flag rather than a delete because the failure
# mode of getting this wrong is a broken deploy pipeline, and the fix has to be
# one variable rather than an archaeology exercise.
# --------------------------------------------------------------------------

resource "aws_iam_role_policy_attachment" "github_deploy_admin" {
  count      = var.deploy_role_scoped ? 0 : 1
  role       = aws_iam_role.github_deploy.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

data "aws_iam_policy_document" "github_deploy_scoped" {
  # 1 · The services Terraform manages. Service-level rather than per-resource:
  #     Terraform legitimately creates and destroys these, and an ARN allowlist
  #     would break on every new resource while adding no real protection (any of
  #     these services can reach the others' data anyway).
  statement {
    sid    = "ManageProjectInfrastructure"
    effect = "Allow"
    actions = [
      "apprunner:*",
      "ecs:*",
      "ecr:*",
      "rds:*",
      "ec2:*", # VPC, subnets, security groups, ip-ranges
      "s3:*",
      "cloudfront:*",
      "secretsmanager:*",
      "logs:*",
      "cloudwatch:*",
      "sns:*",
      "application-autoscaling:*",
      "elasticloadbalancing:Describe*", # read-only; an ALB is a possible later step
    ]
    resources = ["*"]
  }

  # 2 · Identity reads + the roles this stack's services assume. Terraform has to
  #     create and attach them (App Runner instance/access roles, the ECS
  #     execution and task roles), so the write actions are allowed — but only on
  #     paths this project owns, and never on IAM *users*.
  statement {
    sid    = "ReadIdentity"
    effect = "Allow"
    actions = [
      "iam:Get*",
      "iam:List*",
      "sts:GetCallerIdentity",
      "sts:AssumeRole",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageProjectServiceRoles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:PassRole",
      "iam:CreateServiceLinkedRole",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project}-*",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-service-role/*",
    ]
  }

  # 3 · The two lines that actually matter. An explicit Deny cannot be overridden
  #     by any Allow, including a future one added by mistake:
  #     · no IAM users or access keys — the usual route from "CI can deploy" to
  #       "someone has a permanent key in this account";
  #     · no touching its own role or the OIDC trust — a compromised workflow must
  #       not be able to widen its own permissions or add a trusted repo.
  statement {
    sid    = "DenyIdentityEscalation"
    effect = "Deny"
    actions = [
      "iam:CreateUser",
      "iam:CreateAccessKey",
      "iam:CreateLoginProfile",
      "iam:UpdateLoginProfile",
      "iam:AttachUserPolicy",
      "iam:PutUserPolicy",
      "iam:CreateOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:AddClientIDToOpenIDConnectProvider",
      "organizations:*",
      "account:*",
    ]
    resources = ["*"]
  }

  # CreatePolicyVersion/SetDefaultPolicyVersion are how a compromised role
  # could widen its OWN permissions by rewriting a managed policy in place —
  # but denying them on every resource would also block a legitimate
  # `terraform apply` from ever updating this role's own least-privilege
  # policy, forcing an out-of-band admin credential for routine permission
  # changes. NotResource keeps the deny in force on every OTHER policy while
  # carving out only this role's own scoped policy (name-derived, not a
  # reference to the resource below, to avoid a cycle). IAM is default-deny,
  # so the carve-out alone grants nothing — the explicit Allow below is what
  # actually lets terraform apply publish a new version of that one policy.
  statement {
    sid    = "AllowSelfPolicyVersionUpdate"
    effect = "Allow"
    actions = [
      "iam:CreatePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.project}-github-deploy",
    ]
  }

  statement {
    sid    = "DenyPolicyVersionEscalation"
    effect = "Deny"
    actions = [
      "iam:CreatePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    not_resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.project}-github-deploy",
    ]
  }

  statement {
    sid    = "DenySelfModification"
    effect = "Deny"
    actions = [
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:UpdateAssumeRolePolicy",
      "iam:DeleteRole",
    ]
    resources = [aws_iam_role.github_deploy.arn]
  }

  # 4 · The Terraform state bucket is the one thing whose loss is unrecoverable
  #     — it already has prevent_destroy + versioning, and this makes a
  #     mis-targeted delete impossible rather than merely blocked by Terraform.
  statement {
    sid       = "DenyStateBucketDestruction"
    effect    = "Deny"
    actions   = ["s3:DeleteBucket", "s3:PutBucketVersioning"]
    resources = [aws_s3_bucket.tfstate.arn]
  }
}

resource "aws_iam_policy" "github_deploy_scoped" {
  count       = var.deploy_role_scoped ? 1 : 0
  name        = "${var.project}-github-deploy"
  description = "Least-privilege deploy permissions for the ${var.project} CI role (s32 W4)."
  policy      = data.aws_iam_policy_document.github_deploy_scoped.json
}

resource "aws_iam_role_policy_attachment" "github_deploy_scoped" {
  count      = var.deploy_role_scoped ? 1 : 0
  role       = aws_iam_role.github_deploy.name
  policy_arn = aws_iam_policy.github_deploy_scoped[0].arn
}
