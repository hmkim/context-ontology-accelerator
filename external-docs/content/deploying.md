# Deploying Context Ontology Accelerator

This guide walks you through deploying Context Ontology Accelerator into your AWS account.

## Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| AWS Account | — | Target deployment account |
| AWS CLI v2 | 2.x | Credential management, stack operations |
| Node.js | 22+ | CDK CLI, web-app build |
| Python | 3.12 | Backend services |
| pnpm | 10+ | TypeScript package management |
| uv | 0.4+ | Python package management |
| Java | 17+ | Smithy code generation |
| Docker | — | Container image builds |

### Build Architecture (arm64)

All container images this project builds — the Context Manager / MCP server
(Bedrock AgentCore Runtime), the VKG (Ontop) ECS service, the ontology-engine
and document-ingestion tasks — target **`linux/arm64`**:

- **Amazon Bedrock AgentCore Runtime only supports arm64**, so the serve and MCP
  images *must* be arm64.
- The ECS Fargate and Lambda compute is arm64 by design (cost/performance).

If your **build host is arm64** (e.g. an Apple Silicon Mac or a Graviton
instance), builds are native and need no extra setup.

If your **build host is x86_64**, Docker must be able to emulate arm64 —
otherwise `pip` installs x86_64 wheels for native extensions (e.g.
`pydantic_core`) into an arm64 image, and the mismatch is **not caught at build
time**. The container starts, then crashes at runtime with a cryptic error:

```
ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
```

To build arm64 images on an x86_64 host, install the QEMU binfmt handlers once
per boot:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

Then confirm `docker buildx inspect` lists `linux/arm64` under **Platforms**.
`scripts/preflight-deploy.sh` also checks for arm64 build capability and warns
if it is missing.

!!! note "CI with pre-built images"
    If you supply pre-built images via the `*_image_uri` CDK context values
    (e.g. from a Graviton CI builder), the local build path is skipped and this
    requirement does not apply to the deploying host — just ensure the images
    you push are arm64.

## AWS Account Setup

Context Ontology Accelerator deploys into a single AWS account and region. Ensure the deploying principal has `AdministratorAccess` or equivalent permissions for the initial deployment.

### Region Selection

Context Ontology Accelerator defaults to `us-east-1` if no region is set. To deploy to a different region, export `AWS_DEFAULT_REGION` (used by the AWS CLI and preflight checks) **and** `CDK_DEFAULT_REGION` (used by CDK/`bin/app.ts` for AZ resolution and region-specific config) before running any deploy command:

```bash
export AWS_DEFAULT_REGION=us-west-2
export CDK_DEFAULT_REGION=us-west-2
```

Both must be set consistently — if only one is set, CDK and the AWS CLI can silently target different regions.

### Region Prerequisites

Context Ontology Accelerator **cannot be deployed to every AWS region**. It depends on several
services that are not available everywhere, so the deployable set is the intersection of the regions
where all of them exist.

Regional availability changes continuously as AWS launches services in new regions, so this guide
does not list specific regions — **check each dependency below in your target region before you
deploy**. The fastest way to check all of them at once is the
[AWS Regional Services List](https://aws.amazon.com/about-aws/global-infrastructure/regional-product-services/),
filtered to your region.

#### Services to verify

Every service in this table must be available in your target region. The first two are the narrowest
constraints in practice — if either is missing, the region is not usable.

| Service | Used for | Check |
|---------|----------|-------|
| **Amazon Bedrock AgentCore Runtime** | Hosts the Serve (query) and MCP server runtimes | [Supported regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html) — note this page lists availability per AgentCore *feature*; you need **Runtime** |
| **Amazon DataZone** / SageMaker Unified Studio | Namespace domain, data-asset catalog | [Endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/datazone.html) |
| **Amazon Neptune** | Knowledge-graph store | [Endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/neptune.html) |
| **Amazon OpenSearch Serverless** | Vector search for retrieval | [Endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/opensearch-service.html) — Serverless is available in fewer regions than managed OpenSearch |
| **Amazon Bedrock** + Guardrails | All LLM and embedding calls; content filtering | [Endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock.html) — also check the models themselves (see below) |
| **Amazon Athena** (incl. federated query) | SQL over mapped data sources | [Endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/athena.html) |

The remaining services the solution uses — Lambda, API Gateway, ECS on Fargate, S3, DynamoDB,
Cognito, CloudFront, WAF, Glue, Step Functions, SSM, CloudWatch — are available in effectively all
commercial regions and are not usually the limiting factor.

!!! warning "GovCloud and China regions are not supported"
    These partitions lack several required dependencies, and the stacks assume the `aws` partition in
    ARN construction. Deploying there would need code changes beyond region configuration.

#### Bedrock model availability

Availability of the *service* is not enough — the specific **foundation models must also be
enabled in your region**, and the solution's default model IDs are **US cross-region inference
profiles** (`us.anthropic.…`, `us.cohere.embed-v4:0`) that cannot be invoked from a non-US source
region.

If you deploy outside the US, look each model up in
[Regional availability by models](https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html)
(it shows In-Region / Geo / Global support per region), then replace the default IDs with a profile
that is invocable from your region — a geographic profile (`eu.`, `jp.`, `au.`), a `global.` profile,
or the bare in-region model ID. The models to check:

| Model | Used for |
|-------|----------|
| Claude Sonnet 4.6 | Induction, grounding |
| Claude Haiku 4.5 | Rerank, enrichment, document ingestion |
| Claude Sonnet 5 | Serve query resolution (NL-to-SPARQL, synthesis) |
| Cohere Embed v4 | All embeddings |

Not every model offers every profile type in every region — Cohere Embed v4, for example, publishes
only `us.` and `eu.` geographic profiles. Also
[request model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html) for
each model in the deploy region, and note that `global.` profiles require
[additional IAM/SCP permissions](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html)
beyond what the stacks grant by default.

##### Where the model IDs live

Only the Serve query LLM is configurable without a code change (`bedrockLlmModelId` in the SSM deploy
config, `/{prefix}/config`). Every other model ID is a hardcoded default and must be edited:

| What | Where |
|------|-------|
| Embedding model — single source of truth | `libs/common/.../constants.py` (`DEFAULT_EMBED_MODEL_ID`), `libs/ts-shared/src/constants.ts` (`DEFAULT_BEDROCK_MODEL_ID`) |
| Induction / grounding / rerank LLMs (CDK env) | `infra/lib/stacks/services/ontology-stack.ts` (`LLM_MODEL_ID` + rerank/enrichment IDs) |
| Document-ingestion trigger LLM | `infra/lib/stacks/services/sources-stack.ts` (`BEDROCK_MODEL_ARN`) |
| Ontology-engine runtime fallbacks | `packages/ontology-engine/src/.../inducer/` (`services/llm.py`, `services/grounding.py`, `routers/`, `strategies/rigor_ontology.py`), `main.py` |
| Serve runtime fallbacks | `packages/context-manager/src/.../config.py`, `clients/bedrock.py`, `lexical/baseline_retriever.py` |
| Shared Bedrock client default | `libs/common/.../bedrock.py` (`DEFAULT_MODEL_ID`) |

Most runtime defaults are also overridable via environment variables (`BEDROCK_MODEL_ID`,
`BEDROCK_EMBED_MODEL_ID`, `LLM_MODEL_ID`) if you would rather set them per stack than edit source.
Cost attribution in `libs/common/.../bedrock_metrics.py` is keyed by model ID, so add the new IDs
there too or per-model cost metrics will be missing.

!!! warning "Changing the embedding model is a data migration"
    All embeddings must use the same model — existing indexes were written with the previous one and
    must be re-ingested, or retrieval degrades silently. See the note on `DEFAULT_EMBED_MODEL_ID` in
    `libs/common/src/semantic_context_common/constants.py`.

!!! warning "Only `us-east-1` has been tested"
    `us-east-1` is the default and the only region this solution has been deployed and validated in.
    Other regions that pass the checks above are expected to work, but are unverified — budget time
    for troubleshooting on a first deploy elsewhere.

#### Cross-region and AZ constraints

These apply **no matter which region you deploy to**:

- **`us-east-1` is always involved.** CloudFront-scope WAF WebACLs and CloudFront ACM certificates
  exist only in `us-east-1`, so the `*-edge-waf` stack always deploys there and `uiCertificateArn`
  must be a `us-east-1` certificate. Amazon ECR Public is likewise `us-east-1`-only (build-time).
- **Availability Zones can be restricted within a region.** A service being present in a region does
  not mean it is present in every AZ of that region. In `us-east-1`, AgentCore Runtime supports only
  3 of 6 AZs and the OpenSearch Serverless VPC endpoint is offered in a different subset, so
  `bin/app.ts` resolves the intersection at synth time (`infra/lib/utils/agentcore-az.ts`) and pins
  the VPC to it. If your region turns out to have similar AZ restrictions, add it to
  `AGENTCORE_RESTRICTED_AZ_IDS` in that file — otherwise the AgentCore Runtime or the AOSS endpoint
  can fail to create on an unsupported zone.
- **One region per `SCL_PREFIX`.** See the multi-region warning under Environment Variables below.

### Bootstrap CDK

If this is the first CDK deployment to the account/region:

```bash
npx cdk bootstrap aws://<ACCOUNT_ID>/<REGION>
```


## Installation

```bash
git clone <repo-url> && cd ontology-accelerator

# Install pinned tool versions (Python 3.12, Java 17, Node 22, pnpm 10.27.0)
curl https://mise.run | sh
mise install

# make setup runs smithy-generate.sh (make generate) + setup-dev.sh
# (uv sync --all-packages, pnpm install, pre-commit hooks)
make setup
```

`make deploy-dev` re-checks required toolchain versions and re-runs Smithy codegen (`make generate`) automatically if `smithy-generated/` is missing or stale, so a manual re-run is only needed if you want generated artifacts refreshed without doing a full deploy.

## Deploy

```bash
make deploy-dev
```

This runs `scripts/deploy.sh` which:

1. **Preflight checks** — verifies toolchain versions (Node, Java, pnpm), Docker, regenerates Smithy artifacts if missing/stale, authenticates to ECR Public, checks VPC quota
2. **Builds all packages** — compiles TypeScript, bundles Lambdas
3. **Synthesizes CloudFormation** — generates templates from CDK
4. **Deploys all stacks** — CDK handles ordering via dependency graph

!!! note "ECR Public authentication is automatic"
    `make deploy-dev` authenticates to ECR Public (`us-east-1`) as part of its preflight checks. No manual `docker login` step is needed.
    The `us-east-1` region here is intentional and unrelated to your deploy region (see "Region Selection" above) — Amazon ECR Public is a single-region service; its registry and control-plane API exist only in `us-east-1` regardless of which region you deploy Context Ontology Accelerator into.


### Stacks Deployed

| Stack | Purpose |
|-------|---------|
| `coa-dev-network` | VPC, subnets, security groups |
| `coa-dev-auth` | Cognito User Pool, OIDC configuration |
| `coa-dev-guardrail` | Bedrock guardrails |
| `coa-dev-storage` | Neptune (graph DB), OpenSearch Serverless |
| `coa-dev-authnz` | DynamoDB tables for roles, grants, Cedar policies |
| `coa-dev-vkg` | Virtual Knowledge Graph (Ontop) |
| `coa-dev-namespace` | Namespace service (SMUS domain) |
| `coa-dev-metric-service` | Metric authoring and validation |
| `coa-dev-api` | API Gateway (routes, authorizer) |
| `coa-dev-serve` | Context Manager (query orchestration) |
| `coa-dev-sources` | Data source ingestion (Glue, JDBC) |
| `coa-dev-data-layer` | REST API for queries |
| `coa-dev-edge-waf` | CloudFront WAF WebACL (us-east-1, auto-created unless an existing ARN is supplied) |
| `coa-dev-web` | CloudFront + S3 (React frontend) |
| `coa-dev-ontology` | Ontology engine (induction, reasoning) |
| `coa-dev-mcp` | MCP Server on AgentCore Runtime |

Total fresh deploy: **~1.5 hours** for CDK/CloudFormation provisioning of all 16 stacks, once dependencies are installed and Smithy artifacts are generated. Neptune and OpenSearch Serverless provisioning, AgentCore Runtime setup, and cross-stack SSM dependency waits account for most of that time — individual stacks vary widely and several run in parallel, so a per-stack breakdown understates the real end-to-end wall-clock time. CDK resolves the deploy order automatically from the dependency graph declared in `bin/app.ts` — you don't need to deploy stacks individually or in this order by hand.

!!! warning "First-time setup adds significant time"
    The ~1.5 hour figure above is CDK/CloudFormation provisioning time only. On a fresh machine or first-time deploy, budget additional time on top of that for: installing `mise`-managed toolchains, `uv sync`/`pnpm install`, Smithy/Gradle codegen (`make generate` — first run downloads Gradle wrappers, openapi-generator, and builds TypeScript clients from scratch), Docker image builds, and CDK bootstrap. Subsequent deploys after initial setup are faster since dependencies and generated artifacts are cached, though CloudFormation provisioning time for a fresh set of stacks remains similar.

### Configuration Options

Override defaults via environment variables:

```bash
# Custom resource prefix (default: coa)
SCL_PREFIX=myproject make deploy-dev

# Use an existing VPC
SCL_VPC_ID=vpc-abc123 make deploy-dev

# Custom domain for the web app and API — all five are required together
# (all-or-nothing; CDK fails synth if only some are set)
SCL_UI_DOMAIN=ontology.example.com \
SCL_UI_CERT_ARN=arn:aws:acm:us-east-1:123456789012:certificate/ui-cert-id \
SCL_API_DOMAIN=api.ontology.example.com \
SCL_API_CERT_ARN=arn:aws:acm:us-west-2:123456789012:certificate/api-cert-id \
SCL_HOSTED_ZONE_ID=Z0123456789ABCDEFGHIJ \
make deploy-dev
```

!!! note "Custom domain certificate regions"
    `SCL_UI_CERT_ARN` must be an ACM certificate in `us-east-1` (CloudFront requirement) regardless of your deploy region. `SCL_API_CERT_ARN` must be in the same region you're deploying to (API Gateway requirement). CDK validates both at synth time and fails fast with a clear error if either is in the wrong region.

!!! warning "Multi-region deployments"
    S3 buckets and IAM roles are globally scoped, not region-isolated. Deploying the same `SCL_PREFIX` + `env` to a second region **will collide** with an existing deployment. Use a distinct `SCL_PREFIX` per region (e.g., `coa-w2` for `us-west-2`) — do not rely on region alone to disambiguate.

### Internal Environment Variables

These are set by infrastructure stacks and are not user-configurable:

| Variable | Set By | Purpose |
|----------|--------|---------|
| `SCL_MCP_MODE` | `mcp-stack.ts` | Switches the container entrypoint between Context Manager (default) and MCP Server. When set to `"true"`, the container starts in MCP mode. |
| `BULK_REVIEW_PAGE_BUDGET` | `worker.py` default | Per-invocation table budget for the bulk-review worker (default `1000`); when a source has more tables, the worker processes one page, re-enqueues a continuation, and resumes across chained invocations rather than silently capping. |
| `BULK_REVIEW_WALL_CLOCK_BUDGET_S` | `worker.py` default | Per-invocation wall-clock budget in seconds (default `240`), a second guard under the 5-minute Lambda timeout that stops the worker after the current search page and continues in a fresh invocation when neared. |
| `REVIEW_QUEUE_URL` | `sources-stack.ts` | SQS review-queue URL the bulk-review worker re-enqueues page continuations to, wiring its own self-continuation. |

### API Request Limits and Rate Limiting

Context Ontology Accelerator throttles inbound API traffic at three layers. All limits are **soft** —
they ship with conservative defaults and can be tuned per deployment. Defaults
are defined in `infra/lib/constants.ts`.

| Layer | Default | Applies to |
|-------|---------|------------|
| WAF per-IP rate limit | **2000 requests / 5 min per source IP** (block) | Every request, at the edge (CloudFront) and API (before auth) |
| API Gateway stage-wide throttle | **50 rps / 100 burst** | All API methods |
| API Gateway per-operation throttle | **5 rps / 10 burst** | Expensive, job-launching operations only |

**Expensive operations** (the routes that receive the tighter 5 rps / 10 burst
throttle, because each request launches a long-running job or a heavy graph
write):

- `POST /namespaces/{namespaceId}/induce` — ontology induction
- `POST /namespaces/{namespaceId}/sources/{sourceId}/rescan` — source rescan
- `POST /namespaces/{namespaceId}/import-osi` — metric OSI import
- `POST /namespaces/{namespaceId}/proposals/{proposalId}/infer-constraints`
- `POST /namespaces/{namespaceId}/proposals/{proposalId}/validate`
- `POST /namespaces/{namespaceId}/proposals/{proposalId}/compile-constraints`
- `POST /namespaces/{namespaceId}/proposals/{proposalId}/accept`

**Overriding the limits.** The values are construct props with defaults in
`infra/lib/constants.ts`, not environment variables.

- The **API Gateway throttles** are `ApiStackProps` fields — pass them where
  `ApiStack` is instantiated in `infra/bin/app.ts`:
    - `throttleRateLimit` / `throttleBurstLimit` — stage-wide throttle
    - `expensiveThrottleRateLimit` / `expensiveThrottleBurstLimit` — per-operation throttle
- The **WAF per-IP limit** (`WafWebAclProps.rateLimit`) is not currently plumbed
  through to `bin/app.ts`. The `WafWebAcl` construct is created inside `ApiStack`
  and `EdgeWafStack`, so to change it either edit the
  `DEFAULT_WAF_RATE_LIMIT_PER_5MIN` default in `constants.ts` or add a
  pass-through prop on those two stacks. Setting `rateLimit: 0` on the construct
  disables the rate rule (used when bringing your own WebACL — see below).

To bring an entirely pre-built WebACL instead of the auto-created one, set the
`api_web_acl_id` (REGIONAL, API stage) or `cloudfront_web_acl_id` (CloudFront
edge) CDK context value; the `WafWebAcl` construct — and its rate rule — is then
skipped entirely for that surface.

**When to tune for production.** The defaults suit a modest authenticated-user
population. Raise the stage-wide and per-operation throttles if legitimate
concurrent usage triggers 429s; raise the WAF limit if a shared-egress client
(corporate NAT, VPN) legitimately exceeds 2000 req/5min from one IP.

**Monitoring.** Throttling is visible in CloudWatch:

- API Gateway `4XXError` metric — includes HTTP **429 Too Many Requests**
  returned when a caller exceeds a throttle (the burst bucket is empty).
- WAF `BlockedRequests` metric, rule `{prefix}-{env}-{api|cloudfront}-waf-rate-limit-per-ip`
  — counts IPs blocked by the rate-based rule (the API stage WebACL uses the
  `api` prefix, the CloudFront edge WebACL uses `cloudfront`).

A client receiving **HTTP 429** should back off and retry with jitter; the limit
is per-second steady-state with a short burst allowance, so a brief pause clears
it.

## Post-Deployment

### 1. Create Your First User

By default (`idpType: COGNITO`, no `oidcSettings` configured), the deployment
creates a Cognito User Pool. Add users via the AWS Console or CLI:

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <POOL_ID> \
  --username user@example.com \
  --user-attributes Name=email,Value=user@example.com Name=email_verified,Value=true
```

The User Pool ID is in the `coa-dev-auth` stack outputs or SSM at `/<prefix>/userpool-id`.

!!! note "Using your own identity provider instead"
    Context Ontology Accelerator can use an external OIDC-compliant IdP (Okta, Azure AD,
    Auth0, Keycloak, etc.) instead of Cognito — set `idpType: "OIDC"` with
    `oidcSettings` in the SSM config at `/<prefix>/config` **before** running
    `make deploy-dev` (this must be configured pre-deploy; switching IdP types
    afterward requires a redeploy of the `coa-dev-auth` stack). See the
    [Authentication Setup Guide](authentication-setup.md) for the full config
    reference and a step-by-step Okta walkthrough. In OIDC mode, the
    `coa-dev-auth` stack does **not** create a Cognito User Pool at all — the
    commands above won't apply. Instead, create/manage users directly in your
    IdP; access is controlled entirely by [grants](role-permission-management.md)
    against the user's email or IdP group.

### 2. Access the Web App

The web app URL is in the `coa-dev-web` stack's CloudFormation output (`WebAppUrl`). Sign in with the Cognito user you created (or your external IdP's credentials, if using OIDC mode).

### 3. Grant Platform Admin

To give a user full access, grant the `platform-admin` role via the API or web app Permissions page.

## Updating

To deploy updates after pulling new code:

```bash
git pull
make deploy-dev
```

CDK performs incremental updates — only changed stacks are redeployed.

## Tearing Down

```bash
make destroy-dev
```

This runs `scripts/destroy.sh dev`, which orchestrates teardown of resources CFN can't cleanly delete on its own before running `cdk destroy --all`:

1. **Deletes AgentCore Runtimes** (serve + mcp) directly via the Bedrock AgentCore API.
2. **Waits for AgentCore-owned ENIs to detach** from their security groups — see the [AgentCore ENI wait](#agentcore-eni-wait-can-take-hours) note below. This step is currently a no-op pending a shorter, reliable detach signal.
3. **Deletes VKG's per-namespace ECS services** (created outside CloudFormation by the ontology-reload Lambda) and waits for them to reach `INACTIVE`.
4. **Force-deletes the DataZone domain** via `delete-domain --skip-deletion-check`, which cascades through every project, asset, and asset type under it — CloudFormation's own delete can't do this (see [FAQ](#why-does-namespace-stack-fail-to-delete-with-domain-not-empty)).
5. **Runs `cdk destroy --all`**, then verifies no stacks remain.

Same environment-variable overrides as `make deploy-dev` — pass the same `SCL_PREFIX` and region you deployed with:

```bash
SCL_PREFIX=myproject AWS_DEFAULT_REGION=us-west-2 make destroy-dev
```

Additional destroy-specific variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SCL_DESTROY_YES` | unset | Set to `1` to skip the interactive confirmation prompt (e.g. in CI). |
| `SCL_ENI_WAIT_MAX_SECONDS` | `600` | Max wait for AgentCore ENI detach (step 2 — currently disabled). |
| `SCL_ECS_WAIT_MAX_SECONDS` | `300` | Max wait for VKG ECS services to reach `INACTIVE`. |
| `SCL_DOMAIN_WAIT_MAX_SECONDS` | `300` | Max wait for the DataZone domain to finish deleting. |

Steps 1-4 are idempotent — if the script exits early or a step warns and continues, re-running it later picks up from an already-deleted/in-progress state.

!!! warning
    This deletes all resources including databases and stored data. Neptune and OpenSearch data is not recoverable after deletion.

If `make destroy-dev` reports stacks that didn't fully delete, it also scans their CloudFormation events for two known failure signatures and prints the exact fix — see [Troubleshooting](#troubleshooting) below. For anything else, inspect the specific failure with:

```bash
aws cloudformation describe-stack-events --stack-name <PREFIX>-dev-<stack> --region <REGION>
```

### Manual destroy (advanced / non-`dev` environments)

`make destroy-dev` always targets `env=dev`. For any other environment, or if you need to run the underlying steps individually, call the script directly with the environment name:

```bash
cd <repo-root>
AWS_DEFAULT_REGION=<REGION> SCL_PREFIX=<PREFIX> ./scripts/destroy.sh <env>
```

Or fall back to a plain `cdk destroy` (skips all the pre-cleanup steps above — expect the known failures documented in Troubleshooting):

```bash
cd infra
AWS_DEFAULT_REGION=<REGION> npx cdk destroy --all \
  --context env=dev \
  --context resource_prefix=<PREFIX>
```

## Troubleshooting

### AgentCore ENI wait can take hours

**Symptom:** the `*-mcp` and `*-serve` stacks fail to delete their security groups with `has a dependent object` (`DependencyViolation`), even after `make destroy-dev` deletes the AgentCore Runtimes.

**Cause:** AgentCore Runtime provisions VPC network interfaces (ENIs) directly, outside CloudFormation. Runtime deletion doesn't synchronously release them — in practice this has been observed to take **several hours, and sometimes days**, not the few minutes originally expected. CloudFormation has no visibility into these ENIs and tries to delete the security group immediately, before they're gone.

**Fix:** there isn't a fast one. `make destroy-dev` already deletes the Runtimes as its first step to start the detach clock as early as possible, but you generally need to **wait and retry later** (a few hours to a day) rather than intervene:

```bash
# Re-run later — steps 1-4 are idempotent and safe to repeat.
make destroy-dev
```

Do not attempt to manually detach or delete the ENIs yourself — they're AWS-managed and manual intervention doesn't speed up the release. If it's still stuck after a day or more, treat it as an AWS-side issue rather than something to fix locally.

### Why does namespace stack fail to delete with "domain not empty"?

**Symptom:** the `*-namespace` stack fails to delete with `Domain cannot be deleted because there are existing projects under this domain`, or the `SystemProject`/`DefaultProjectProfile` resources fail with `failed to stabilize due to internal failure` or `deletion prevented by project ...`.

**Cause:** every namespace creates its own DataZone project, and CloudFormation's native `DeleteDomain`/`DeleteProject` calls can't force past a shared asset type (`CoaRelationalTable`) that's still referenced by other projects' assets — which is always true while more than one namespace exists. `make destroy-dev` handles this automatically (step 4: `delete-domain --skip-deletion-check`, a true force-delete at the domain level). If you're running a bare `cdk destroy` instead of `make destroy-dev`, you'll hit this.

**Fix:** use `make destroy-dev` instead of a raw `cdk destroy`. If you're already stuck on a bare `cdk destroy`, resolve the SMUS domain ID from SSM and force-delete it directly, then retry:

```bash
DOMAIN_ID=$(aws ssm get-parameter --name /<PREFIX>/smus/domain-id --region <REGION> --query Parameter.Value --output text)
aws datazone delete-domain --identifier "$DOMAIN_ID" --skip-deletion-check --region <REGION>
# wait for it to finish deleting, then retry:
make destroy-dev
```

### Why does the VKG stack fail to delete the ECS cluster?

**Symptom:** the `*-vkg` stack fails with `The Cluster cannot be deleted while Services are active` (`ClusterContainsServicesException`).

**Cause:** per-namespace VKG services are created outside CloudFormation by the ontology-reload Lambda, so CloudFormation has no record of them and can't clean them up itself. `make destroy-dev` deletes these directly (step 3) before `cdk destroy` reaches the cluster.

**Fix:** use `make destroy-dev` instead of a raw `cdk destroy`. If already stuck, delete the services manually then retry:

```bash
CLUSTER=<PREFIX>-dev-vkg-cluster
for SVC in $(aws ecs list-services --cluster "$CLUSTER" --region <REGION> --query 'serviceArns[]' --output text); do
  aws ecs update-service --cluster "$CLUSTER" --service "$SVC" --desired-count 0 --region <REGION>
  aws ecs delete-service --cluster "$CLUSTER" --service "$SVC" --force --region <REGION>
done
make destroy-dev
```

### `GetBucketTagging: AccessDenied` or `NoSuchTagSet` on a bucket cleanup custom resource

**Symptom:** a stack fails to delete with something like:

```
User: arn:...assumed-role/<prefix>-storage-CustomS3AutoDeleteObjectsCustomReso-XXXXX
is not authorized to perform: s3:GetBucketTagging on resource: "arn:aws:s3:::<bucket>"
```

or

```
Received response status [FAILED] from custom resource.
Message returned: NoSuchTagSet: The TagSet does not exist
```

**Cause:** this is a known, currently-open timing issue in CDK's `Custom::S3AutoDeleteObjects` custom resource — its delete handler checks the bucket's `aws-cdk:auto-delete-objects` tag before proceeding, and that check can race the bucket policy that grants it access. This isn't caused by a missing IAM grant in Context Ontology Accelerator's own code (the grant is present and correctly ordered in the template) — it's an upstream CDK library limitation.

**Fix:** re-tag the bucket so the custom resource recognizes it, then retry:

```bash
aws s3api put-bucket-tagging --bucket <bucket-name> --region <REGION> \
  --tagging 'TagSet=[{Key=aws-cdk:auto-delete-objects,Value=true}]'
make destroy-dev
```

`make destroy-dev` detects this failure signature automatically in its post-destroy check and prints the exact command with the specific bucket name filled in — you don't need to identify the bucket yourself.

### A bucket fails to delete because it's "not empty"

**Symptom:** a bucket resource (commonly an access-logs bucket, e.g. `AccessLogsBucketCD784A59`) fails to delete with `The bucket you tried to delete is not empty`.

**Cause:** same upstream `Custom::S3AutoDeleteObjects` race as above, just surfacing as leftover objects instead of a tag-check failure.

**Fix:** force-delete every object version, then retry:

```bash
aws s3api delete-objects --bucket <bucket-name> --region <REGION> --delete "$(
  aws s3api list-object-versions --bucket <bucket-name> --region <REGION> \
    --output json --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}')"
make destroy-dev
```

`make destroy-dev` detects this failure signature automatically and prints the exact command with the specific bucket name filled in.

After a `DELETE_FAILED` stack is resolved (via any of the fixes above), verify no orphaned resources remain:

```bash
aws cloudformation list-stacks --region <REGION> \
  --stack-status-filter DELETE_FAILED | grep <PREFIX>-dev
```

This should return nothing.
