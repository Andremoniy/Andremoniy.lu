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

Browser sign-in -> Cognito -> configured social provider or email account
Floating chat -> CloudFront -> HTTP API Gateway JWT authorizer -> Lambda -> Bedrock
                                                                  |
                                  DynamoDB user/global quotas and session ownership
```

Amplify publishes the six static pages and official App Store artwork. Its build
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

The authenticated API accepts questions of up to 1,000 characters and limits generated
answers to 600 tokens. API Gateway allows one request per second with a burst of
four. Lambda uses shared account concurrency by default. A positive
`ChatReservedConcurrency` parameter reserves and caps concurrent chat executions
when the account quota allows it. The default value of zero omits the reservation;
it does not disable the function. Exact-origin CORS covers the apex, `www` and the
configured Amplify branch hostname. Origin restrictions are not authentication
and do not prevent non-browser callers from spoofing the Origin header.

Answers are rendered as text without the source-passage display. Session IDs remain in page memory.
Application logs contain Bedrock error codes, service error messages, request IDs
and the configured model ARN rather than application conversation bodies, and expire
after 14 days. Account-level Bedrock invocation logging is a separate configuration.
Prompt grounding reduces unsupported answers but cannot guarantee accuracy.

The source bucket, vector bucket and index are retained on stack deletion or
replacement. Retained resources continue to incur storage charges. Request
throttling is not a monthly spending cap. Knowledge ingestion precedes static
publication, so failed releases do not atomically restore previous CV content.

## Authentication and per-user quotas

The first submitted question is held in the browser until the visitor signs in.
The OAuth flow uses authorization code with SHA-256 PKCE, a random state value,
an exact same-origin callback, and a 10-minute sign-in-flow expiry. The callback
exchanges the code and returns to the original portfolio page with the pending
question. Access tokens expire after 15 minutes and live in tab-scoped session
storage; refresh tokens are discarded. Expired sessions prompt sign-in again.
`auth-callback.html` is part of the Amplify release. The Cognito sign-in cookie
can simplify subsequent authentication without extending an access token.

API Gateway validates token signature, issuer, audience and expiry and requires
the `portfolio-chat/ask` scope. Lambda additionally checks the token type and
client ID. CORS is an additional browser restriction, not the authentication
mechanism. Sign-out clears local tokens and ends the Cognito login session.

`ChatQuestionsPerMinute` and `ChatQuestionsPerDay` default to 10 and 100.
`ChatGlobalQuestionsPerDay` and `ChatGlobalQuestionsPerMonth` default to 1,000 and
10,000 across all users. DynamoDB uses fixed UTC minute/day/calendar-month windows.
A single transaction conditionally increments all four counters before generation;
parallel requests cannot bypass a limit or partially consume another counter when
one allowance is exhausted. Valid requests that reach quota consumption count even
if a subsequent model call fails. Invalid input and rejected
conversation ownership do not consume quota. Throttled responses include reset
timing. TTL cleans up old counters, while window keys enforce resets immediately.
Verified mailboxes identify the user quota across devices and provider accounts.
The canonical form is lowercase with the `+tag` removed; consumer Gmail addresses
also ignore dots and normalize `googlemail.com` to `gmail.com`. Dots in other
domains remain significant. Only the canonical mailbox's SHA-256 hash appears in
the quota key, never the raw or tagged address. Without a verified email, the
Cognito subject identifies the quota. Unverified email claims cannot consume
another mailbox's quota. Conversation ownership remains tied to the Cognito
subject, and accounts are not automatically linked by email.

Global limits stop model invocations across every account once exhausted, while
the static site remains available. The UI distinguishes shared exhaustion from
a personal quota. These are request-count limits, not dollar-denominated spending
caps: API, identity, database and traffic charges can continue after model calls
stop. Existing external budget alerts are independent of these counters.

The state table also stores hashed conversation IDs, their owner ID and a
five-minute inactivity deadline. Lambda checks ownership and the deadline before
resuming a Bedrock session. A separate encrypted history table retains timestamped
questions and answers, grouped by conversation and linked to the Cognito subject.
Its summary records the effective end time, extended on each exchange or set by
an explicit session-close request. History has no automatic expiry, has
point-in-time recovery enabled and is retained on stack deletion. Neither table
stores access tokens, profile names or email addresses.
The user pool stores identity attributes. Lambda retrieves the visitor's name
from Cognito's authenticated userInfo endpoint, verifies the returned subject,
sanitizes the name and includes it as data in the generation prompt. A name is
used naturally when shared by the provider; it is not invented when unavailable.

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
function accepts only `POST /chat` and its `OPTIONS` preflight from configured
website origins. POST requests require a bearer header; API Gateway still performs
the actual JWT validation. CORS checks are additional browser restrictions and
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
excess usage can incur charges. The origin token adds one Secrets Manager secret
(standard list price approximately USD 0.40/month, plus API calls). This perimeter
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
    ChatGlobalQuestionsPerMonth=10000 `
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
