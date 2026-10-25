# AWS infrastructure

Initial CloudFormation stack for the static portfolio hosted by **AWS Amplify Hosting**. The website source and `amplify.yml` build configuration remain at the repository root.

## Resources

- `AWS::Amplify::App` connected to `https://github.com/Andremoniy/Andremoniy.lu`
- `AWS::Amplify::Branch` for `main`, with automatic builds on new commits

A custom domain / Route 53 hosted zone is **not included yet**, pending the domain decision.

## First deployment

1. Choose the AWS Region (the examples use `eu-west-1`). Install the [AWS Amplify GitHub App for that Region](https://github.com/apps/aws-amplify-eu-west-1/installations/new), granting access to the `Andremoniy.lu` repository.
2. Create a GitHub personal access token following [AWS's instructions](https://docs.aws.amazon.com/amplify/latest/userguide/setting-up-GitHub-access.html) (AWS documents the `admin:repo_hook` scope for a classic PAT).
3. In **AWS Secrets Manager**, in the **same Region as the stack**, create a secret named `andremoniy/amplify/github`, with a JSON key `token` containing the PAT. Never put the token in Git, a CloudFormation parameter value, or shell history.
4. From the repository root, run:

   ```bash
   aws cloudformation deploy \
     --stack-name andremoniy-portfolio \
     --template-file aws/amplify.yaml \
     --region eu-west-1
   ```

   If the secret has a different name, supply `--parameter-overrides GitHubTokenSecretName=YOUR_SECRET_NAME`.
5. Check the CloudFormation `WebsiteURL` output and Amplify's build status. If no first build has been started, trigger it manually:

   ```bash
   aws amplify start-job \
     --app-id YOUR_AMPLIFY_APP_ID \
     --branch-name main \
     --job-type RELEASE \
     --region eu-west-1
   ```

## Notes

- The CloudFormation deployment principal needs permission to retrieve the secret from Secrets Manager, and to create Amplify resources.
- The root `amplify.yml` controls the static site build; this template intentionally does not duplicate the build spec.
- Secrets Manager and Amplify Hosting incur AWS charges.
- The GitHub PAT is used to establish the Amplify GitHub App connection; it is not stored in the template or source tree.
- Deploying this template creates AWS resources. Committing it alone does **not** deploy the stack.
