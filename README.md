# Andrey Lebedev · Portfolio

Six-page static portfolio hosted on AWS Amplify, with professional experience,
research, applications, 3D printing and art. HTML pages use relative links, and
styles, images and browser scripts are stored in `assets/`.

The floating, collapsible CV assistant uses Amazon Bedrock Knowledge Bases to
retrieve career evidence and generate answers grounded in the CV. The first
submitted question opens a sign-in prompt; successful sign-in returns to that
question automatically. Cognito supports email accounts and optional Google,
Facebook/Meta, Apple, Amazon and Microsoft identities. Provider registrations
and Secrets Manager credentials determine which social options are enabled.

The API requires a Cognito access token. DynamoDB enforces per-user quotas of
10 questions per minute and 100 per UTC day, with atomic counters shared across
the user's devices. Conversation ownership is checked server-side. The browser
keeps the visible transcript in memory and the short-lived access token in
tab-scoped session storage. The authenticated profile name personalizes replies
when available; missing names receive generic wording. The tailored-CV recruiter
area is a demonstration.

`knowledge/cv.txt` is the curated knowledge source. It contains structured career
records, responsibilities, achievements, technologies, credentials and textual
interpretations of the PDF's graphical ratings. Inferred CEFR equivalents are
explicitly distinguished from the original dot scores. Personal contact details
are omitted. The file is versioned with the project; repository readers and
visitors using the assistant can access its professional content.

`amplify.yml` assembles the static release, downloads official App Store artwork,
uploads changed CV text to private S3, synchronizes the Bedrock knowledge base and
writes public chat endpoint and sign-in configuration. Embeddings reside in a separate
S3 vector bucket. No AWS credentials or CV source file are included in the static
website artifacts.

Infrastructure and IAM permissions are declared in `aws/amplify.yaml`.
[AWS infrastructure](aws/README.md) describes the resources and the local
CloudFormation deployment sequence.

`scripts/fetch_apple_icons.py` resolves and validates Apple artwork.
`scripts/publish_knowledge.py` publishes and synchronizes CV knowledge.
Python contract tests cover the API handler, knowledge publisher and artwork
processing. The offline JavaScript suites cover chat interactions, the sign-in
gate, OAuth PKCE/state validation, saved-question restoration and safe text
rendering. Test dependencies are `boto3`, `PyYAML` and `jsdom`; CloudFormation
schema validation uses `cfn-lint`. The production frontend has no Node dependencies.
