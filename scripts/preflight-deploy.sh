#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Pre-deploy validation — catches common silent failures before CDK runs.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ERRORS=0

err() { echo "ERROR: $1" >&2; ERRORS=$((ERRORS + 1)); }
warn() { echo "WARN:  $1" >&2; }
ok() { echo "  OK:  $1"; }

echo "=== Pre-deploy preflight checks ==="
echo ""

# ── 1. Required toolchain versions (Node 22+, Java 17+, pnpm) ────────────
# Mirrors the versions pinned in .mise.toml. We only check presence/version
# here — installing mise itself is a one-time local setup step (see
# scripts/setup-dev.sh) and is intentionally not automated in this script,
# since CI provisions its own toolchain and never calls deploy.sh.
if command -v node >/dev/null 2>&1; then
  NODE_MAJOR=$(node --version | sed 's/^v//' | cut -d. -f1)
  if [ "$NODE_MAJOR" -ge 22 ]; then
    ok "Node $(node --version) found"
  else
    err "Node 22+ required (found $(node --version)). Run: mise install"
  fi
else
  err "Node not found. Install via 'mise install' (see .mise.toml) or https://mise.run"
fi

if command -v java >/dev/null 2>&1; then
  JAVA_VER=$(java -version 2>&1 | head -1 | awk -F '"' '{print $2}' | cut -d. -f1)
  if [ "$JAVA_VER" -ge 17 ]; then
    ok "Java $JAVA_VER found"
  else
    err "Java 17+ required (found $JAVA_VER). Run: mise install"
  fi
else
  err "Java not found — required for Smithy code generation. Run: mise install"
fi

if command -v pnpm >/dev/null 2>&1; then
  ok "pnpm $(pnpm --version) found"
else
  err "pnpm not found. Run: mise install (or npm install -g pnpm)"
fi

# ── 2. Python pip availability ────────────────────────────────────────────
if command -v pip >/dev/null 2>&1 && pip --version >/dev/null 2>&1; then
  ok "pip functional: $(pip --version 2>&1)"
elif command -v pip3 >/dev/null 2>&1 && pip3 --version >/dev/null 2>&1; then
  ok "pip3 functional (bundler resolves this automatically)"
else
  warn "Neither pip nor pip3 is functional. Docker bundling required."
fi

# ── 3. Container engine (Docker or Finch) ─────────────────────────────────
# CDK shells out to $CDK_DOCKER (default: docker) for asset bundling.
CONTAINER_ENGINE=""
if [ -n "${CDK_DOCKER:-}" ]; then
  if command -v "$CDK_DOCKER" >/dev/null 2>&1 && "$CDK_DOCKER" info >/dev/null 2>&1; then
    CONTAINER_ENGINE="$CDK_DOCKER"
    ok "CDK_DOCKER=$CDK_DOCKER (explicit, daemon running)"
  else
    err "CDK_DOCKER=$CDK_DOCKER set but daemon is not reachable"
  fi
elif command -v finch >/dev/null 2>&1 && finch info >/dev/null 2>&1; then
  export CDK_DOCKER=finch
  CONTAINER_ENGINE=finch
  ok "Finch daemon running (exported CDK_DOCKER=finch)"
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  CONTAINER_ENGINE=docker
  ok "Docker daemon running"
else
  warn "No container engine running (docker/finch) — local pip bundling must succeed"
fi

# ── 3b. arm64 build capability ───────────────────────────────────────────
# All container images target linux/arm64 (Amazon Bedrock AgentCore Runtime
# only supports arm64; the ECS Fargate and Lambda compute is arm64 by design).
# When the build host is not arm64, Docker must be able to emulate it (buildx +
# QEMU/binfmt) — otherwise native extensions (e.g. pydantic_core) are silently
# built for the wrong architecture and the container fails at RUNTIME, not build
# time, with a cryptic:
#   ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
# This check surfaces that class of failure early. It only warns (never blocks):
# a CI pipeline that supplies pre-built images via the *_image_uri CDK context
# does not build locally and is unaffected.
if [ -n "$CONTAINER_ENGINE" ]; then
  HOST_ARCH="$(uname -m)"
  if [ "$HOST_ARCH" = "aarch64" ] || [ "$HOST_ARCH" = "arm64" ]; then
    ok "Host is arm64 ($HOST_ARCH) — native image builds match the target arch"
  elif docker buildx inspect 2>/dev/null | grep -q 'linux/arm64'; then
    ok "buildx can target linux/arm64 (arm64 image builds supported)"
  elif [ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
    ok "QEMU aarch64 emulation registered (binfmt_misc) — arm64 builds supported"
  else
    warn "Build host is $HOST_ARCH with no arm64 emulation (buildx/QEMU) detected."
    warn "Images target arm64; without emulation, native extensions build for the"
    warn "wrong arch and the container fails at RUNTIME with:"
    warn "  ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'"
    warn "Fix: install QEMU binfmt handlers, e.g."
    warn "  docker run --privileged --rm tonistiigi/binfmt --install arm64"
    warn "or build on an arm64 host / arm64 buildx builder."
    warn "(Ignore if deploying CI pre-built images via the *_image_uri context.)"
  fi
fi

# ── 4. Smithy-generated OpenAPI specs ────────────────────────────────────
OPENAPI_DIR="$REPO_ROOT/smithy-generated/openapi"
SPEC_COUNT=0
if [ -d "$OPENAPI_DIR" ]; then
  SPEC_COUNT=$(find "$OPENAPI_DIR" -name "*.json" -size +100c 2>/dev/null | wc -l | tr -d ' ' || echo "0")
fi

if [ "$SPEC_COUNT" -gt 0 ]; then
  ok "Smithy OpenAPI specs present ($SPEC_COUNT files)"
else
  warn "smithy-generated/openapi/ missing or empty — running 'make generate'..."
  if (cd "$REPO_ROOT" && make generate); then
    SPEC_COUNT=$(find "$OPENAPI_DIR" -name "*.json" -size +100c 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    if [ "$SPEC_COUNT" -gt 0 ]; then
      ok "Smithy code generation complete ($SPEC_COUNT OpenAPI specs)"
    else
      err "'make generate' ran but smithy-generated/openapi/ is still empty."
    fi
  else
    err "'make generate' failed. Fix Smithy/Gradle errors above before deploying."
  fi
fi

# ── 5. ECR Public authentication ─────────────────────────────────────────
# ECR Public is hosted only in us-east-1 regardless of deploy region.
# Re-authenticating is cheap and idempotent, so we always refresh here
# rather than probing whether the existing token is still valid.
if [ -n "$CONTAINER_ENGINE" ]; then
  if command -v aws >/dev/null 2>&1 && aws sts get-caller-identity >/dev/null 2>&1; then
    if aws ecr-public get-login-password --region us-east-1 2>/dev/null \
        | "$CONTAINER_ENGINE" login --username AWS --password-stdin public.ecr.aws >/dev/null 2>&1; then
      ok "Authenticated to ECR Public (us-east-1)"
    else
      err "Failed to authenticate to ECR Public. Base image pulls will fail."
    fi
  else
    warn "AWS credentials not available — skipping ECR Public authentication"
  fi
else
  warn "No container engine — skipping ECR Public authentication"
fi

# ── 6. VPC limit check (requires AWS credentials) ────────────────────────
# Resolution order matches CDK's own precedence (infra/bin/app.ts uses
# CDK_DEFAULT_REGION as the source of truth) so preflight validates the
# same region CDK will actually deploy to.
REGION="${CDK_DEFAULT_REGION:-${AWS_DEFAULT_REGION:-${AWS_REGION:-us-east-1}}}"
if command -v aws >/dev/null 2>&1 && aws sts get-caller-identity >/dev/null 2>&1; then
  VPC_COUNT=$(aws ec2 describe-vpcs --region "$REGION" --query 'length(Vpcs)' --output text 2>/dev/null || echo "?")
  VPC_LIMIT_RAW=$(aws service-quotas get-service-quota --service-code vpc --quota-code L-F678F1CE --region "$REGION" --query 'Quota.Value' --output text 2>/dev/null || echo "5")
  VPC_LIMIT="${VPC_LIMIT_RAW%%.*}"
  [[ "$VPC_LIMIT" =~ ^[0-9]+$ ]] || VPC_LIMIT=5
  if [ "$VPC_COUNT" != "?" ]; then
    if [ "$VPC_COUNT" -ge "$VPC_LIMIT" ]; then
      err "VPC limit reached: $VPC_COUNT/$VPC_LIMIT VPCs in $REGION."
      err "Delete unused VPCs or request a quota increase before deploying."
    else
      ok "VPC headroom: $VPC_COUNT/$VPC_LIMIT used in $REGION"
    fi
  fi
else
  warn "AWS credentials not available — skipping VPC limit check"
fi

# ── 7. Stale cdk.context.json ────────────────────────────────────────────
if [ -f "$REPO_ROOT/infra/cdk.context.json" ]; then
  warn "infra/cdk.context.json exists — cached lookups may be stale."
  warn "If deploy fails with 'resource not found', delete it: rm infra/cdk.context.json"
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
if [ $ERRORS -gt 0 ]; then
  echo "PREFLIGHT FAILED: $ERRORS error(s) found. Fix before deploying."
  exit 1
else
  echo "Preflight passed."
fi
