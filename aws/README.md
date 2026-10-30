# AWS infrastructure

`amplify.yaml` defines the portfolio's hosting, authoritative DNS, optional custom
domain, CV knowledge storage and public chat API. The default domain is
`andremoniy.com`; the connected repository is `Andremoniy/Andremoniy.lu`, branch
`main`.

## Architecture

```text
GitHub knowledge/cv.txt -> Amplify build -> private S3 source bucket
                                              |
                                      Bedrock ingestion
                                              |
                                      S3 vector bucket/index

Introduction + Turnstile -> CloudFront -> HTTP API -> Session Lambda -> DynamoDB
Established chat -> CloudFront -> HTTP API Lambda Authorizer -> Chat Lambda -> Bedrock
Guest limit -> Cognito -> JWT-protected session upgrade -> same conversation
                                      DynamoDB visitor/IP/user/global quotas + history
```

Amplify publishes the seven static pages and official App Store artwork. Its build
role has access to the CV object and knowledge-base ingestion operations.
The build uploads text only when its SHA-256 digest changes, synchronizes the
knowledge base on every release, and waits up to 20 minutes for completion.
Ingestion failures prevent successful release publication. The generated
`dist/assets/chat-config.json` contains the public API endpoint, Cognito sign-in
domain and public browser-client ID. It contains no provider secrets.
Amplify builds also fail when stack-managed chat variables are absent, identifying
the missing variables in the build error. Disconnected configuration is available
only outside Amplify for local previews.

Bedrock uses retrieval augmented generation rather than model training. Titan
Text Embeddings v2 produces 1,024-dimensional vectors from 400-token chunks with
15% overlap. S3 Vectors requires a dedicated vector bucket, separate from the
ordinary S3 source bucket. Both stores are private and encrypted at rest; the
source bucket blocks public access and rejects insecure transport.

The established-session API accepts questions of up to 1,000 characters and limits generated
answers to 600 tokens. API Gateway allows one request per second with a burst of
four. Lambda uses shared account concurrency by default. A positive
`ChatReservedConcurrency` parameter reserves and caps concurrent chat executions
when the account quota allows it. The default value of zero omits the reservation;
it does not disable the function. Exact-origin CORS covers the apex, `www` and the
configured Amplify branch hostname. Origin restrictions are not authentication
and do not prevent non-browser callers from spoofing the Origin header.

Answers are rendered as text without the source-passage display. Session credentials and the OAuth transcript snapshot live in tab-scoped sessionStorage.
Application logs contain Bedrock error codes, request IDs
and the configured model ARN rather than application conversation bodies, and expire
after 14 days. Account-level Bedrock invocation logging is a separate configuration.
Prompt grounding reduces unsupported answers but cannot guarantee accuracy.

The source bucket, vector bucket and index are retained on stack deletion or
replacement. Retained resources continue to incur storage charges. Request
throttling is not a monthly spending cap. Knowledge ingestion precedes static
publication, so failed releases do not atomically restore previous CV content.

## CV assistant scope and Bedrock Guardrails

CloudFormation provisions `ChatGuardrail` and a numbered `ChatGuardrailVersion`.
Lambda attaches that ID and version to every `RetrieveAndGenerate` request and
has `bedrock:ApplyGuardrail` permission scoped to that guardrail. Missing, empty
or draft configuration prevents generation; there is no unguarded retry path.

Denied topics apply to prompts and responses: programming deliverables and
tutorials (including Python code and Git commands), general knowledge and creative
tasks, and personalized medical, legal or financial advice. A high-strength input
prompt-attack filter supplements the CV-only generation prompt. Biographical
questions about technologies, leadership, qualifications, projects, soft skills,
candidate differentiators and working-environment fit remain in scope. The prompt
permits evidence-based synthesis with labelled inferences and interprets recruiter
questions using "you" as referring to Andrey. The knowledge source includes compact,
self-contained passages for those interview questions. Detection is probabilistic; offline tests verify configuration and
intervention handling, rather than managed-service classification quality.

When Bedrock reports `INTERVENED`, Lambda returns and stores the fixed portfolio
scope refusal and omits source excerpts. Refused requests still consume the
normal chat allowances. Guardrail evaluations are separately billable Bedrock
usage. The `ChatGuardrailId` and `ChatGuardrailVersion` outputs identify the
deployed policy. A policy revision requires a new immutable version; changing
the version resource's Description forces CloudFormation to publish a replacement
snapshot after the guardrail update. The runtime diagram shows this as an attached
input/output policy, rather than a separate network proxy.

## Guest sessions, optional authentication and quotas

`POST /chat/session` is public through the existing CloudFront distribution and
HTTP API. The lightweight Session Lambda validates trimmed Name (1–80 characters)
and Company (1–120), rejects control characters, malformed objects and oversized
bodies, reserves creation-abuse counters, verifies Turnstile success/hostname/action,
and creates the session and initial history summary atomically. HTTP APIs do not
support REST API JSON-schema validators, so body validation intentionally lives
here. This Lambda has no Bedrock permissions.

The backend issues independent random 256-bit visitor and session credentials.
Only SHA-256 hashes of credentials are stored. The visitor credential is retained
in first-party localStorage for 180 days and must resolve to a backend-issued
visitor record. The session credential is tab-scoped, authorises one conversation
and expires after `SessionTokenSeconds` (default/max 3,600 seconds). The public
conversation ID is independent of the Bedrock continuation ID. Both guest and
registered users must establish a session and confirm an introduction each time.
The browser keeps introduction values in sessionStorage for same-tab confirmation;
it does not add persistent profile preferences or non-essential storage.

`POST /chat` uses `SessionAuthorizer`, a REQUEST Lambda Authorizer with simple
responses, two mandatory identity sources (Authorization and X-Visitor-Token), and
zero caching TTL. It validates credential hashes, visitor binding, expiry,
revocation, conversation ownership and inactivity using consistent reads. It
passes verified metadata through API Gateway context. Chat Lambda never validates
Cognito JWTs or chat credentials cryptographically. It rechecks session lifecycle
with an atomic 45-second processing lock, enforces ownership and quotas, calls
Bedrock, then atomically stores the exchange and summary. The lock also prevents
upgrade during an in-flight answer. Only the matching lock holder releases it.
Revocation or session-store failure fails closed.

Guest exhaustion offers **Sign in / Register** and **Maybe later** with the
explanation of abuse protection. Global exhaustion never offers sign-in. Cognito
retains authorization code + PKCE, state validation, exact return paths, 15-minute
access tokens, verified email and optional social providers. The browser snapshots
the session, transcript and pending question before OAuth navigation. Other page
exits close the conversation, with inactivity as a fallback.

`POST /chat/session/upgrade` retains the existing Cognito JWT authorizer and
`portfolio-chat/ask` scope. Session Lambda checks access-token client/type/scope,
loads matching Cognito userInfo, requires verified email, and validates the old
session plus visitor credential. A transaction revokes the old chat credential,
issues a replacement and attaches subject/verified email to the existing summary.
The replacement expires no later than the Cognito access token. It cannot upgrade
another visitor's session or change an already associated account. Subsequent
questions use the same Lambda Authorizer. Previous guest exchanges remain guest
records; the summary and subsequent exchanges contain the verified identity.
Sign-out closes/revokes the conversation and returns to the guest introduction.

| Parameter | Default | Scope |
|---|---:|---|
| GuestQuestionsPerDay | 10 | Backend-issued visitor / UTC day |
| GuestIpQuestionsPerMinute | 3 | Trusted viewer IP / UTC minute |
| GuestIpQuestionsPerDay | 50 | Trusted viewer IP / UTC day |
| ChatQuestionsPerMinute | 10 | Verified mailbox / UTC minute |
| ChatQuestionsPerDay | 100 | Verified mailbox / UTC day |
| ChatGlobalQuestionsPerDay | 1000 | Everyone / UTC day |
| ChatGlobalQuestionsPerMonth | 1000 | Everyone / UTC calendar month |
| SessionCreationsPerMinute | 5 | Establishment attempts / IP / UTC minute |
| SessionCreationsPerDay | 1000 | Establishment attempts / global / UTC day |

One revision-checked DynamoDB transaction reserves all applicable question
allowances. Guests consume visitor, IP and global counters; authenticated users
consume verified-user and global counters. Conflicts are reread and retried with
bounded backoff, so failed reservations cannot partially charge another counter.
Rejected authentication and establishment never consume question quotas.
Requests reserved before a later model failure do count. HTTP 429 includes scope,
reset timestamp, Retry-After and whether sign-in may help; the UI shows local reset
time. Clearing local storage cannot reset IP counters, and changing IP does not
reset an existing visitor's allowance. Shared-IP visitors may use Cognito to move
to verified-user limits while global limits still apply.

Existing `quota#<verified mailbox hash>` and `quota#global` rows renew UTC fields
in place, preserving counts during rollout. Guarded migration still seeds old
time-window counters on first use. Guest rows (`quota#guest-<visitor>` and
`quota#ip-<HMAC>`) use the same table and expire after 48 hours. Creation counters
expire after 48 hours; Turnstile replay hashes after 10 minutes. Temporary session
and visitor records expire with their credentials. TTL deletion is asynchronous;
expiry and revocation are always checked in code. User-specific rows include
name/company and verified email where applicable. Shared/global counters contain
none of these attributes. Existing authenticated/global rows retain their previous
manual-deletion policy. IP HMAC keys are server-only; avoid rotation inside active
quota windows because rotation changes the pseudonymous counter keys.

The history table is unchanged: summaries exist at establishment, before the first
question, and carry conversation/visitor IDs, self-declared introduction,
authentication status, start/last activity times, effective end time and reason.
Every exchange has timestamps and introduction metadata. Five minutes of idle
time ends the conversation without a scheduled write; `endsAt` plus `inactivity`
records that effective ending. Explicit close writes `closedAt` and
`session_closed`. Summary updates preserve Bedrock continuation and prior history.
No history is deleted or backfilled with invented names or companies. History has
no automatic expiry, retains PITR and remains retained on stack deletion.

## Turnstile setup and local testing

Before deployment, create a **Managed** Cloudflare Turnstile widget. Allow
`andremoniy.com`, `www.andremoniy.com` and the actual production Amplify branch
hostname. Disable pre-clearance: no Cloudflare zone/proxy or third-party cookie is
required. The browser sets action `chat_session` and appearance `interaction-only`.
Turnstile is loaded only when the visitor opens the introduction. Siteverify
success must match that action and the request Origin hostname; the latter must
already belong to the stack's exact origin allow list. Tokens are single-use and
expire after five minutes; the backend also writes conditional replay hashes.
After an expired/failed challenge, the browser resets the widget and preserves the
introduction and selected question. Per-message challenges are never required.

Create a Secrets Manager secret in eu-west-1, e.g.
`andremoniy/chat/turnstile`, containing JSON `{"secret":"YOUR_TURNSTILE_SECRET"}`.
Use the console or a protected local JSON file; never commit it or include its
value in shell history. With a protected file, the manual command is:

```powershell
aws secretsmanager create-secret --name andremoniy/chat/turnstile `
  --secret-string file://C:/secure/turnstile-secret.json --region eu-west-1 --profile default
```

Pass the **secret name**, not its ARN, as `TurnstileSecretName`. The Session IAM
role can read only that secret. For an existing customer-managed KMS key, separately
grant the role access to decrypt that key or use the AWS-managed Secrets Manager
key. The stack generates separate origin and IP-HMAC secrets; neither is public.
Only `TurnstileSiteKey` reaches Amplify's public configuration. Rotate the Turnstile
secret in Secrets Manager and recycle/update Session Lambda configuration to clear
its warm-process cache. Never disable validation to make deployment succeed.

Offline tests use moto plus mocked Siteverify, Cognito and Bedrock with success,
failure, replay, expiry and interleaved transaction cases. They require no live
secrets or AWS calls. Cloudflare test keys are suitable for an isolated development
environment, never production; hostname/action checks must still match. A local
static preview without backend configuration remains disconnected. No production
test-key bypass or secret is built into the application.

References: [Cloudflare server validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/),
[HTTP API Lambda authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-lambda-authorizer.html).

## Email alias protection

A Cognito pre-sign-up Lambda rejects email addresses containing `+` in the local
part and requests the base address instead. This applies before local or federated
account creation. It uses no database lookup: Cognito's email-username uniqueness
prevents duplicate base-address accounts atomically. The trigger leaves email
verification enabled and never auto-confirms an account.

Cognito's hosted sign-up flow does not provide a supported pre-sign-up response
field for silently replacing the supplied email address. Rejection ensures new
accounts store an untagged contact email while avoiding verification to a silently
rewritten recipient. Literal `+` addresses are therefore unsupported by this site's
registration policy. Existing tagged accounts are not migrated or deleted; their
verified aliases share a canonical quota identity. Gmail dot variations can still
produce distinct Cognito accounts but share the same application allowance.

## CloudFront API perimeter

The chat endpoint is the HTTPS CloudFront URL in `ChatApiURL`. A viewer-request
function accepts `POST /chat`, `/chat/session` and `/chat/session/upgrade`, plus
their `OPTIONS` preflights, from configured website origins. Establishment needs
no bearer token; chat and upgrade require bearer headers. API Gateway validates
chat sessions through Lambda and Cognito JWTs on upgrade. CORS checks are additional browser restrictions and
can be spoofed by non-browser clients.

Chat responses and errors are not cached. The distribution forwards authorization
and CORS headers, uses HTTPS to the API origin, and replaces the viewer's origin
verification header with a generated server-only token from Secrets Manager.
Lambda compares that token before reading application state or invoking Bedrock.
Authenticated direct-origin requests without the token are rejected before quota
consumption and generation. The underlying API Gateway endpoint remains publicly
reachable, so direct-origin attempts can still incur API and Lambda charges.

CloudFront uses pay-as-you-go pricing and its default certificate, with
`PriceClass_100`. There is no paid WAF web ACL, paid pricing-plan subscription,
custom API domain or separate certificate. Shield Standard supplies basic network
DDoS protection; CloudFront alone does not provide WAF bot/rate rules. Applicable
free-tier allowances are account-wide, and proxy requests, origin transfer and
excess usage can incur charges. The origin, IP-HMAC and Turnstile secrets have
Secrets Manager storage/API charges. This perimeter
is not an account-wide billing cap.

The switch from the API Gateway URL to the CloudFront URL requires a stack update
and an Amplify rebuild. The stack creates the distribution and updates
`CHAT_API_URL`; the rebuild publishes that URL to the browser configuration.

## Identity provider configuration

Cognito's classic hosted sign-in UI is available immediately for email-account
registration, verification and sign-in. Social providers are optional resources
enabled by nonempty secret-name parameters. Only enabled providers appear on
the sign-in page. Each requires an external provider app registration, consent
configuration and credentials; CloudFormation does not create those third-party
registrations. The `SocialIdentityCallbackURL` output is the external provider
redirect URL. Website callbacks are the apex, `www` and Amplify branch origins
with the path `/auth-callback.html`.

| Provider | CloudFormation parameter | Fields in the Secrets Manager JSON secret |
| --- | --- | --- |
| Google | `GoogleIdentitySecretName` | `client_id`, `client_secret` |
| Facebook / Meta | `FacebookIdentitySecretName` | `client_id`, `client_secret`, `api_version` |
| Sign in with Apple | `AppleIdentitySecretName` | `client_id` (Services ID), `team_id`, `key_id`, `private_key` |
| Login with Amazon | `AmazonIdentitySecretName` | `client_id`, `client_secret` |
| Microsoft OIDC | `MicrosoftIdentitySecretName` | `client_id`, `client_secret`, `oidc_issuer` (tenant-specific HTTPS issuer) |

Provider credentials are resolved from Secrets Manager during stack deployment
and never included in the frontend. The deployment identity requires access to
those secrets. Microsoft support uses OIDC for the configured tenant; it is not
an automatic integration with every Microsoft tenant. Provider consent or app
publication requirements can restrict sign-in to test users before approval.
Apple can provide a name only on the initial authorization; existing or
privacy-restricted profiles can lack a name. Accounts without a readable name
still have access to the chat.

Existing deployments require a stack update followed by an Amplify release
containing the callback page and authentication scripts. The update makes the
API private immediately; old website releases cannot chat until the matching
frontend is published. The production publisher rejects missing authentication
environment variables instead of producing a disconnected release.

## Local CloudFormation deployment

### Prerequisites

- AWS CLI v2 and an authenticated local profile with CloudFormation, Amplify,
  CloudFront, Secrets Manager, Route 53, IAM, Cognito, DynamoDB, Lambda,
  API Gateway, S3, S3 Vectors, Bedrock and CloudWatch Logs
  deployment permissions, including IAM role passing.
- The repository source available locally and the application changes available
  on the configured GitHub branch for the subsequent Amplify release.
- AWS Amplify GitHub App authorization for `Andremoniy/Andremoniy.lu` in the target
  Region, and a suitable GitHub connection token in an existing Secrets Manager
  secret named `andremoniy/amplify/github`, with a JSON field named `token`.
- Bedrock Knowledge Bases, S3 Vectors, Titan Text Embeddings v2 and the generation
  model available in the deployment Region, with required model access established.
  `ChatModelId` defaults to `amazon.nova-lite-v1:0`;
  `ChatInferenceProfileId` defaults to `eu.amazon.nova-lite-v1:0`.
  This profile supports Knowledge Bases from Ireland and routes generation within
  the EU. Model ID and profile must refer to the same model. An empty profile ID
  selects direct regional inference for models that support it. The IAM policy
  authorizes invocation and `GetInferenceProfile` for the selected profile, and
  model invocation explicitly in Frankfurt, Stockholm, Ireland and
  Paris, without depending on an inference-profile condition in downstream calls.
  These are the default profile's destination Regions when invoked from Ireland;
  a different model/profile or source Region can require a different destination
  list. Organizational policies must permit its destination Regions.
- A registered domain. The stack creates a hosted zone but does not register a
  domain. Existing stacks use their existing stack name and parameter values.
  An existing externally managed zone requires adoption/import instead of a second
  authoritative zone.

The commands below use PowerShell from the repository root.

### 1. Local profile and template validation

```powershell
$AwsProfile = 'default'
$AwsRegion = 'eu-west-1'
$StackName = 'andremoniy-portfolio'

aws sts get-caller-identity --profile $AwsProfile

$TemplateBucket = aws cloudformation describe-stacks `
  --stack-name $StackName `
  --query "Stacks[0].Outputs[?OutputKey=='CvSourceBucket'].OutputValue | [0]" `
  --output text `
  --region $AwsRegion `
  --profile $AwsProfile

aws s3 cp aws/amplify.yaml "s3://$TemplateBucket/cloudformation/amplify.yaml" `
  --region $AwsRegion `
  --profile $AwsProfile

aws cloudformation validate-template `
  --template-url "https://$TemplateBucket.s3.$AwsRegion.amazonaws.com/cloudformation/amplify.yaml" `
  --region $AwsRegion `
  --profile $AwsProfile
```

The template exceeds the 51,200-byte direct-upload limit. For an existing stack,
the commands reuse its private CV source bucket under `cloudformation/`, outside
the knowledge source's `cv/` prefix. The deployment identity needs S3 upload and
read permissions for that prefix. For a new stack, `$TemplateBucket` must instead
name an existing private deployment bucket in the target Region before upload
and validation; the new stack's CV bucket does not exist yet.

### 2. Initial stack creation without custom-domain association

This stage applies to a new stack whose domain is not yet delegated to its Route
53 zone. An existing stack with a working custom domain uses step 4 directly.

```powershell
aws cloudformation deploy `
  --stack-name $StackName `
  --template-file aws/amplify.yaml `
  --s3-bucket $TemplateBucket `
  --s3-prefix cloudformation `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides EnableCustomDomain=false `
  --region $AwsRegion `
  --profile $AwsProfile
```

### 3. Stack outputs and DNS delegation

```powershell
aws cloudformation describe-stacks `
  --stack-name $StackName `
  --query 'Stacks[0].Outputs' `
  --output table `
  --region $AwsRegion `
  --profile $AwsProfile
```

The `Route53NameServers` output identifies the authoritative nameservers required
at the domain registrar before custom-domain association. Certificate validation
depends on that delegation being effective. Existing mail and other DNS services
also depend on their records being present in the authoritative zone.

### 4. Custom-domain association or existing-stack update

```powershell
aws cloudformation deploy `
  --stack-name $StackName `
  --template-file aws/amplify.yaml `
  --s3-bucket $TemplateBucket `
  --s3-prefix cloudformation `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides EnableCustomDomain=true `
  --region $AwsRegion `
  --profile $AwsProfile
```

Existing parameter values are reused by the deploy command except for the explicit
override. `DomainName`, `GitHubBranch`, `GitHubTokenSecretName`, `ChatModelId` and `ChatInferenceProfileId`
are optional deployment overrides when different values are required.
Changing `DomainName` can replace the hosted zone and domain association.

An existing stack using a different model requires explicit model overrides during
migration to Nova Lite; updated template defaults do not replace retained parameter
values. Both overrides keep the invoked profile and IAM model permissions aligned.

```powershell
aws cloudformation deploy `
  --stack-name $StackName `
  --template-file aws/amplify.yaml `
  --s3-bucket $TemplateBucket `
  --s3-prefix cloudformation `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides `
    ChatModelId=amazon.nova-lite-v1:0 `
    ChatInferenceProfileId=eu.amazon.nova-lite-v1:0 `
    ChatQuestionsPerMinute=10 `
    ChatQuestionsPerDay=100 `
    ChatGlobalQuestionsPerDay=1000 `
    ChatGlobalQuestionsPerMonth=1000 `
  --region $AwsRegion `
  --profile $AwsProfile
```

Optional provider-secret parameters can be appended to the same
`--parameter-overrides` list when corresponding app registrations are available,
for example `GoogleIdentitySecretName=andremoniy/chat/google`. Empty defaults
leave that provider disabled while email-account sign-in remains available.

### 5. Amplify release after stack completion

```powershell
$AmplifyAppId = aws cloudformation describe-stacks `
  --stack-name $StackName `
  --query "Stacks[0].Outputs[?OutputKey=='AmplifyAppId'].OutputValue | [0]" `
  --output text `
  --region $AwsRegion `
  --profile $AwsProfile

$GitHubBranch = 'main'
aws amplify start-job `
  --app-id $AmplifyAppId `
  --branch-name $GitHubBranch `
  --job-type RELEASE `
  --region $AwsRegion `
  --profile $AwsProfile
```

The release uses the branch's stack-managed knowledge-storage and API environment
variables. Subsequent GitHub pushes trigger Amplify builds; CloudFormation changes
require a separate stack update. The `WebsiteURL`, `CustomDomainURL` and
`ChatApiURL` outputs identify the resulting endpoints.

## Hybrid-access rollout to the existing stack (manual only)

Do not create a replacement stack. Complete Turnstile configuration and secret
creation above first. These PowerShell commands preserve existing stack parameters
except the explicitly supplied values. In particular the existing monthly value
may be 10,000; a changed template default does not replace it, so explicitly pass
1,000. Existing user/global usage counts are retained and may immediately block
requests if already above the new allowance.

```powershell
$AwsProfile = 'default'
$AwsRegion = 'eu-west-1'
$StackName = 'andremoniy-portfolio' # use the actual existing stack name
$TurnstilePublicSiteKey = 'REPLACE_WITH_PUBLIC_SITE_KEY'
$TemplateBucket = aws cloudformation describe-stacks --stack-name $StackName `
  --query "Stacks[0].Outputs[?OutputKey=='CvSourceBucket'].OutputValue | [0]" `
  --output text --region $AwsRegion --profile $AwsProfile
python scripts/sync_chat_code.py --check
python -m unittest discover -s tests -p 'test_*.py'
cfn-lint aws/amplify.yaml
aws s3 cp aws/amplify.yaml "s3://$TemplateBucket/cloudformation/hybrid-chat.yaml" `
  --region $AwsRegion --profile $AwsProfile
aws cloudformation validate-template `
  --template-url "https://$TemplateBucket.s3.$AwsRegion.amazonaws.com/cloudformation/hybrid-chat.yaml" `
  --region $AwsRegion --profile $AwsProfile
aws cloudformation deploy --stack-name $StackName --template-file aws/amplify.yaml `
  --s3-bucket $TemplateBucket --s3-prefix cloudformation --capabilities CAPABILITY_IAM `
  --parameter-overrides TurnstileSiteKey=$TurnstilePublicSiteKey `
    TurnstileSecretName=andremoniy/chat/turnstile `
    GuestQuestionsPerDay=10 GuestIpQuestionsPerMinute=3 GuestIpQuestionsPerDay=50 `
    ChatQuestionsPerMinute=10 ChatQuestionsPerDay=100 `
    ChatGlobalQuestionsPerDay=1000 ChatGlobalQuestionsPerMonth=1000 `
  --no-execute-changeset --region $AwsRegion --profile $AwsProfile
# Inspect the reported change set: no table or Cognito replacement is expected.
$ChangeSetId = 'REPLACE_WITH_THE_REPORTED_CHANGE_SET_ARN'
aws cloudformation describe-change-set --change-set-name $ChangeSetId `
  --stack-name $StackName --region $AwsRegion --profile $AwsProfile
# Only after reviewing the change set:
aws cloudformation execute-change-set --change-set-name $ChangeSetId `
  --stack-name $StackName --region $AwsRegion --profile $AwsProfile
aws cloudformation wait stack-update-complete --stack-name $StackName `
  --region $AwsRegion --profile $AwsProfile
```

The generic deployment examples earlier also require `TurnstileSiteKey` and
`TurnstileSecretName` on this first update (and on new stacks). Subsequent updates
reuse those parameter values. This repository change does not execute any of
these deployment commands.

For Amplify, stage the committed code on the configured production GitHub branch
only when ready to release; pushes trigger automatic builds. Deploy the backend
first, then run/retry the Amplify build after stack completion so it receives
`TURNSTILE_SITE_KEY` alongside existing endpoint and Cognito variables. For an
explicit rebuild after the code is pushed:

```powershell
$AmplifyAppId = aws cloudformation describe-stacks --stack-name $StackName `
  --query "Stacks[0].Outputs[?OutputKey=='AmplifyAppId'].OutputValue | [0]" `
  --output text --region $AwsRegion --profile $AwsProfile
aws amplify start-job --app-id $AmplifyAppId --branch-name main --job-type RELEASE `
  --region $AwsRegion --profile $AwsProfile
```

Coordinate this backend/frontend release: the old frontend's Cognito bearer token
will no longer satisfy the new chat authorizer. Existing tabs must reload and
establish a new conversation; the public endpoint and Cognito account configuration
are preserved. No Cognito-user migration or password reset is needed. Existing
history and quota tables are neither replaced nor emptied. Legacy conversations
remain readable administratively; they do not become guest credentials. No
automatic metadata backfill is attempted. Confirm existing parameter overrides,
especially monthly quota and optional providers, before executing the change set.

After deployment, smoke-test Managed Turnstile on each allowed hostname, actual
JWT issuer/audience/signature rejection, missing/forged chat credentials, guest
limits, email/social sign-in, same-conversation upgrade and sign-out. Check that
the public config contains only the public site key and no secret. Inspect IAM
and logs for the expected metadata/error codes without credentials or raw IPs.
Offline tests and cfn-lint do not replace these managed-service checks. Review
the controller's legitimate-interest assessment, processor terms and retained
history policy before enabling the revised privacy notice in production.
