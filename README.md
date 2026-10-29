# Andrey Lebedev · Portfolio

Six-page static portfolio hosted on AWS Amplify, with professional experience,
research, applications, 3D printing and art. HTML pages use relative links, and
styles, images and browser scripts are stored in `assets/`.

The floating, collapsible CV assistant uses Amazon Bedrock Knowledge Bases to
retrieve career evidence and generate answers with source passages. Conversation
state remains in browser memory. The tailored-CV recruiter area is a demonstration.

`knowledge/cv.txt` is the curated knowledge source. It contains structured career
records, responsibilities, achievements, technologies, credentials and textual
interpretations of the PDF's graphical ratings. Inferred CEFR equivalents are
explicitly distinguished from the original dot scores. Personal contact details
are omitted. The file is versioned with the project; repository readers and
visitors using the assistant can access its professional content.

`amplify.yml` assembles the static release, downloads official App Store artwork,
uploads changed CV text to private S3, synchronizes the Bedrock knowledge base and
writes the public chat endpoint configuration. Embeddings reside in a separate
S3 vector bucket. No AWS credentials or CV source file are included in the static
website artifacts.

Infrastructure and IAM permissions are declared in `aws/amplify.yaml`.
[AWS infrastructure](aws/README.md) describes the resources and the local
CloudFormation deployment sequence.

`scripts/fetch_apple_icons.py` resolves and validates Apple artwork.
`scripts/publish_knowledge.py` publishes and synchronizes CV knowledge.
Python contract tests cover the API handler, knowledge publisher and artwork
processing. The offline JavaScript DOM suite covers chat interactions and safe
text rendering. Test dependencies are `boto3`, `PyYAML` and `jsdom`; CloudFormation
schema validation uses `cfn-lint`. The production frontend has no Node dependencies.
