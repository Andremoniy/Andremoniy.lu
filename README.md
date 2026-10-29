# Andrey Lebedev · Portfolio

Seven-page static portfolio hosted on AWS Amplify, with professional experience,
research, applications, 3D printing and art. HTML pages use relative links, and
styles, images and browser scripts are stored in `assets/`.

The floating, collapsible CV assistant uses Amazon Bedrock Knowledge Bases to
retrieve career evidence and generate answers grounded in the CV. The first
submitted question opens a sign-in prompt; successful sign-in returns to that
question automatically. Cognito supports email accounts and optional Google,
Facebook/Meta, Apple, Amazon and Microsoft identities. Provider registrations
and Secrets Manager credentials determine which social options are enabled.

A versioned Amazon Bedrock Guardrail is attached to every knowledge-base
generation request. Denied topics cover programming deliverables and tutorials,
general-purpose tasks and personalized professional advice, with input and output
blocking and a high-strength prompt-attack filter. The generation prompt limits
the assistant to Andrey's background and portfolio; questions about his technical
experience, soft skills, differentiators and suitable working environments remain
in scope. Evidence-based interpretations are labelled, and recruiter questions
using "you" refer to Andrey as the candidate. Guardrail interventions return and store a fixed
scope refusal without source excerpts. Guardrail failures do not trigger an
unguarded retry. Managed filtering is probabilistic and requires live validation
after deployment; guardrail evaluations incur additional Bedrock usage charges.

CloudFront fronts the API, rejects unsupported requests at the edge and forwards
authorized chat requests without caching. The API requires a Cognito access token
and a private CloudFront origin token. DynamoDB enforces per-user quotas of
10 questions per minute and 100 per UTC day, with atomic counters shared across
the user's devices, plus shared limits of 1,000 questions per UTC day and 10,000
per UTC calendar month. Verified email aliases share a normalized mailbox quota
identity; its hash identifies quota keys, and a separate `email` attribute stores
the verified, lowercase address without a plus tag on user-specific records.
Shared quota records have no user email. New registrations require
a base email address without a `+tag`. Conversation ownership is checked server-side. The browser
keeps the visible transcript in memory and the short-lived access token in
tab-scoped session storage. The authenticated profile name personalizes replies
when available; missing names receive generic wording. The tailored-CV recruiter
area is a demonstration.

An encrypted DynamoDB history table retains questions and answers as separate,
timestamped exchanges, grouped by conversation and linked to the Cognito user ID.
The verified normalized email is also stored on history summaries and exchanges;
access tokens are never stored. A summary records the start, last
activity and effective end time. Its inactivity deadline is five minutes after
the latest reply; this deadline marks the end without a scheduled database write.
Signing out, starting a new conversation or leaving the page sends a close request.
Page-close delivery is best effort; inactivity handles missing close requests.
Collapsing the assistant does not close the session. Later questions start a new
Bedrock conversation. History has no automatic expiry, has point-in-time recovery
enabled and is retained if the CloudFormation stack is deleted. The chat notice
discloses transcript storage. The website does not expose a history-reading API.

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
