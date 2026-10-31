# Andrey Lebedev · Portfolio

Seven-page static portfolio hosted on AWS Amplify, with professional experience,
research, applications, 3D printing and art. HTML pages use relative links, and
styles, images and browser scripts are stored in `assets/`.

The floating CV assistant starts in guest mode. Its existing greeting is followed
by mandatory Name and Company fields and **Start chatting**. Both fields are
self-declared; they are never authentication credentials. A selected question
waits until a lightweight Session Lambda validates the introduction and a Managed
Cloudflare Turnstile token, creates a retained conversation summary and issues a
time-limited opaque chat credential. No Bedrock call happens at this stage.

Every question passes through CloudFront, API Gateway and an uncached Lambda
Authorizer before reaching Chat Lambda. Backend-issued visitor credentials persist
in first-party localStorage for 180 days, where permitted. Chat credentials,
transcript and OAuth continuation are tab-scoped. New conversations always require
confirmation of name/company, including for signed-in users. Session credentials
are stored only as hashes in DynamoDB and are bound to the visitor credential and
one conversation. Idle conversations end after five minutes. Explicit reset,
sign-out and page exit close a conversation; OAuth navigation preserves it.

Guest limits are 10 questions per visitor per UTC day and 3/minute plus 50/UTC day
per keyed pseudonymous IP. Cognito is optional until a guest limit is reached.
Email verification and optional social providers are preserved. A JWT-protected
upgrade verifies the existing session and Cognito user, rotates the chat credential,
and associates verified email with the same conversation. Pending questions and
the visible transcript survive sign-in. Signed-in limits are 10/minute and
100/UTC day. Global limits are 1,000/UTC day and 1,000/UTC calendar month,
regardless of authentication. All allowances are configurable and reserved in one
DynamoDB transaction before inference. Rate limits are not an AWS spending cap.

The existing persistent user/global quota rows, normalized verified mailbox keys
and guarded legacy-counter migration remain. Guest visitor/IP rows expire after
48 hours. Shared IP/global rows contain no name, company or email. CloudFront
overwrites the trusted viewer-IP header; arbitrary forwarding headers are ignored.
Separate session-route throttling, IP/global creation counters and single-use
Turnstile verification protect establishment before expensive model calls.

Amazon Bedrock Knowledge Bases retrieves CV evidence and Amazon Nova Pro generates
grounded replies. Every generation retains the published Bedrock Guardrail,
denied topics and prompt-attack filters; failures never trigger an unguarded retry.
Name and company are explicitly untrusted context, used sparingly for
personalisation and never to override grounding or infer a visitor's authority.

The encrypted retained history table stores introductions even if no question is
asked. Summaries and exchanges include name/company, visitor and conversation
ownership, timestamps, authentication status, and verified email when available.
The Bedrock continuation ID is distinct from the public conversation ID.
History has no automatic expiry; existing records remain untouched, with
point-in-time recovery and retention on stack deletion. See the accessible
[Privacy & Cookies Notice](privacy.html) for storage, purposes, retention and rights.
No advertising, analytics or optional tracking has been introduced.

The first saved question and answer also initiate one owner-notification event.
An atomic `firstInteractionAt`/`firstQuestion` marker in the retained summary
feeds DynamoDB Streams → notification Lambda → SNS → the confirmed owner email.
Opening the chat, submitting an introduction, later messages, account upgrades
and closing the conversation do not send further notifications. Publication runs
outside the chat request path. A leased record in the existing state table
deduplicates retries and keeps the email snapshot for 30 days. Bounded stream
failures go to an encrypted SQS metadata queue for 14 days. Delivery remains
at least once; a crash after SNS accepts a publication can still duplicate an email.
`ChatNotificationEmail` defaults to `andremoniy@gmail.com`; an empty value disables
the optional resources. Confirm the SNS subscription before testing. See
[notification deployment and operations](aws/README.md#chat-session-email-notifications).

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
introduction, session authorisation, guest/IP/global quotas, secure upgrades, OAuth PKCE/state validation, saved-question restoration and safe text
rendering. Test dependencies are `boto3`, `PyYAML` and `jsdom`; CloudFormation
schema validation uses `cfn-lint`. The production frontend has no Node dependencies.

Backend sources are `aws/chat_handler.py`, `aws/session_handler.py` and
`aws/notification_handler.py`.
Run `python scripts/sync_chat_code.py` after changes; the CloudFormation inline
code is checked with `--check`. Tests run against that deployable inline code:

```powershell
python -m pip install boto3 PyYAML 'moto[dynamodb]' cfn-lint cairosvg
python scripts/sync_chat_code.py --check
python -m unittest discover -s tests -p 'test_*.py'
cfn-lint aws/amplify.yaml
# Install jsdom outside the production website and set NODE_PATH if needed.
node tests/test_chat_ui.cjs
node tests/test_chat_edge.cjs
node tests/test_auth.cjs
python scripts/build_architecture_diagrams.py --png
```

Offline tests mock external verification and model services; production has no
verification bypass or development secret. Cloudflare's official test keys can
be used only in an isolated development setup with matching hostname validation;
the automated suites need no production secrets or AWS credentials.

Deployment requires a manually configured Managed Turnstile widget and an existing
Secrets Manager secret. Follow the exact existing-stack update and Amplify release
steps in [aws/README.md](aws/README.md). Nothing deploys merely by running the tests.
