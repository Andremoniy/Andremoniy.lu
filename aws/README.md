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

Floating browser chat -> HTTP API Gateway -> Lambda -> Bedrock RetrieveAndGenerate
```

Amplify publishes the six static pages and official App Store artwork. Its build
role has access to the CV object and knowledge-base ingestion operations.
The build uploads text only when its SHA-256 digest changes, synchronizes the
knowledge base on every release, and waits up to 20 minutes for completion.
Ingestion failures prevent successful release publication. The generated
`dist/assets/chat-config.json` contains only the public API endpoint.

Bedrock uses retrieval augmented generation rather than model training. Titan
Text Embeddings v2 produces 1,024-dimensional vectors from 400-token chunks with
15% overlap. S3 Vectors requires a dedicated vector bucket, separate from the
ordinary S3 source bucket. Both stores are private and encrypted at rest; the
source bucket blocks public access and rejects insecure transport.

The public API accepts questions of up to 1,000 characters and limits generated
answers to 600 tokens. API Gateway allows one request per second with a burst of
four; Lambda concurrency is two. Exact-origin CORS covers the apex, `www` and the
configured Amplify branch hostname. Origin restrictions are not authentication
and do not prevent non-browser callers from spoofing the Origin header.

Answers and evidence are rendered as text. Session IDs remain in page memory.
Application logs contain error codes rather than conversation bodies and expire
after 14 days. Account-level Bedrock invocation logging is a separate configuration.
Prompt grounding reduces unsupported answers but cannot guarantee accuracy.

The source bucket, vector bucket and index are retained on stack deletion or
replacement. Retained resources continue to incur storage charges. Request
throttling is not a monthly spending cap. Knowledge ingestion precedes static
publication, so failed releases do not atomically restore previous CV content.

## Local CloudFormation deployment

### Prerequisites

- AWS CLI v2 and an authenticated local profile with CloudFormation, Amplify,
  Route 53, IAM, Lambda, API Gateway, S3, S3 Vectors, Bedrock and CloudWatch Logs
  deployment permissions, including IAM role passing.
- The repository source available locally and the application changes available
  on the configured GitHub branch for the subsequent Amplify release.
- AWS Amplify GitHub App authorization for `Andremoniy/Andremoniy.lu` in the target
  Region, and a suitable GitHub connection token in an existing Secrets Manager
  secret named `andremoniy/amplify/github`, with a JSON field named `token`.
- Bedrock Knowledge Bases, S3 Vectors, Titan Text Embeddings v2 and the generation
  model available in the deployment Region, with required model access established.
  `ChatModelId` defaults to regional `anthropic.claude-3-haiku-20240307-v1:0`.
  The parameter accepts a regional foundation-model ID; inference profiles need
  different ARN and IAM configuration.
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
aws cloudformation validate-template `
  --template-body file://aws/amplify.yaml `
  --region $AwsRegion `
  --profile $AwsProfile
```

### 2. Initial stack creation without custom-domain association

This stage applies to a new stack whose domain is not yet delegated to its Route
53 zone. An existing stack with a working custom domain uses step 4 directly.

```powershell
aws cloudformation deploy `
  --stack-name $StackName `
  --template-file aws/amplify.yaml `
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
  --capabilities CAPABILITY_IAM `
  --parameter-overrides EnableCustomDomain=true `
  --region $AwsRegion `
  --profile $AwsProfile
```

Existing parameter values are reused by the deploy command except for the explicit
override. `DomainName`, `GitHubBranch`, `GitHubTokenSecretName` and `ChatModelId`
are optional deployment overrides when different values are required.
Changing `DomainName` can replace the hosted zone and domain association.

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
