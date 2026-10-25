# AWS infrastructure

CloudFormation for Amplify Hosting, Route 53 DNS and optional custom-domain association. The static website and its build specification (`amplify.yml`) are at the repository root.

## Resources

- `AWS::Amplify::App` connects to `Andremoniy/Andremoniy.lu`
- `AWS::Amplify::Branch` builds and publishes `main` on push
- `AWS::Route53::HostedZone` creates a **public** DNS zone
- `AWS::Amplify::Domain` connects the apex and `www` to Amplify, **only when explicitly enabled**

## Domain

The registered domain is **andremoniy.com**. The CloudFormation parameter `DomainName` defaults to `andremoniy.com`, and the Route 53 hosted zone and optional Amplify domain association will use that value.

The GitHub repository remains named `Andremoniy.lu`; its name does not affect the custom domain.

## Deployment (two stages)

1. Install the [AWS Amplify GitHub App for eu-west-1](https://github.com/apps/aws-amplify-eu-west-1/installations/new), granting access to the repository.
2. Follow [AWS GitHub access guidance](https://docs.aws.amazon.com/amplify/latest/userguide/setting-up-GitHub-access.html) to generate a suitable GitHub token for the CloudFormation connection.
3. Create an **AWS Secrets Manager** secret in the same Region, named `andremoniy/amplify/github`, whose JSON body has a `token` field. Never put the token in Git or CLI arguments.

**Stage 1: Create Amplify and Route 53, without yet associating the custom domain.**

```bash
aws cloudformation deploy \
  --stack-name andremoniy-portfolio \
  --template-file aws/amplify.yaml \
  --parameter-overrides EnableCustomDomain=false \
  --region eu-west-1
```


Get the `Route53NameServers` output, then set **all four** as the authoritative nameservers at the registrar (in the domain's nameserver / delegation settings). This replaces the registrar's DNS hosting; recreate any existing DNS records you need in Route 53 before switching. The registrar's additional "DNS zone" service is not required for this arrangement.

Allow delegation to propagate; check that public DNS points to the four assigned Route 53 servers. If the first Amplify build has not started, use `aws amplify start-job --app-id APP_ID --branch-name main --job-type RELEASE --region eu-west-1`.

**Stage 2: After the domain registration is complete, NS delegation works, and the site is deployed in Amplify, enable the domain association.**

```bash
aws cloudformation deploy \
  --stack-name andremoniy-portfolio \
  --template-file aws/amplify.yaml \
  --parameter-overrides EnableCustomDomain=true \
  --region eu-west-1
```

Keep the same DomainName value (default: andremoniy.com) in both deployments. Amplify will provision a managed TLS certificate and, when it recognises the Route 53 hosted zone in the same AWS account, automatically manage the required DNS records for the root domain and `www`. Check the Amplify domain association status before treating the domain as live.

## How DNS ownership works

Creating a hosted zone **does not prove** you own the domain; AWS allows multiple hosted zones for the same name. Only the nameservers configured with the domain registrar (and published via the parent TLD's delegation) determine which zone is authoritative for public DNS. By changing the domain's NS delegation you demonstrate control of its registration; certificate authorities additionally verify control through DNS challenge records.

## Notes

- The template **does not purchase or register a domain**. It creates a Route 53 hosted zone, which incurs charges even before delegation.
- Changing `DomainName` on an existing stack replaces the hosted zone and may also replace the Amplify domain association. Verify the name before first deployment.
- Commit-only does not deploy CloudFormation. The GitHub connection uses the secret specified by `GitHubTokenSecretName`.
- Avoid manually creating an additional Route 53 hosted zone for the same domain. If one already exists, import/adopt it instead of creating a duplicate.
- The current build pipeline fetches Apple app artwork and depends on Apple's API being reachable from the Amplify build environment.

References: [CloudFormation HostedZone](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-route53-hostedzone.html), [Amplify Domain](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-amplify-domain.html), [Amplify + Route 53](https://docs.aws.amazon.com/amplify/latest/userguide/to-add-a-custom-domain-managed-by-amazon-route-53.html).
