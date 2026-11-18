# Deploying on AWS

```
             HTTPS (Function URL)
 client ───────────────────────────► Lambda (container image from ECR)
                                       │  FastAPI via Mangum
                                       ├── cold start: index from S3 -> /tmp
                                       ├── LLM key from SSM Parameter Store
                                       ├── Gemini / Groq API (outside AWS)
                                       └── logs -> CloudWatch (7 days)
```

## Why this setup

| Choice | Reason |
|---|---|
| Lambda + container image | Pay per request; 1M requests/month are in the always-free tier. Images up to 10 GB, so numpy and pypdf fit without layer tricks. |
| Function URL instead of API Gateway | Free HTTPS endpoint. API Gateway adds cost and nothing needed here. |
| Index in S3, NumPy in memory | No database to pay for. Loaded once per cold start (a few hundred KB). |
| SSM Parameter Store | Standard parameters are free; Secrets Manager costs 0.40 USD per secret/month. |
| `API_TOKEN` in the app | The URL is public; without a token anyone could spend the LLM quota. |
| Budget alarm at 1 USD | To know immediately if something starts costing money. |
| eu-west-3 (Paris) | Close to users in Spain/France. |

Bedrock is not used because it has no free tier. The LLM stays on Gemini/Groq and the
code would only need a new entry in `PROVIDERS` to switch.

## Steps

Prerequisites: an AWS account, `aws configure` done with an IAM user (not root),
Terraform >= 1.6 and Docker.

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
# fill llm_api_key, api_token (e.g. `openssl rand -hex 24`) and alert_email
./scripts/deploy_aws.sh
```

The script:

1. creates the ECR repository and the S3 bucket (`terraform apply -target`),
2. builds the image for `linux/amd64` and pushes it,
3. builds the index locally with the same embedding model and uploads it to S3,
4. applies the rest (Lambda, IAM role, Function URL, SSM, budget).

Open `<url>?key=<api_token>` for the UI, or call the API with the `X-API-Key` header.

To update the code, run the script again (new image tag). To update the documents,
`python -m app.rag.ingest --upload` and force a cold start (any configuration change, or
just wait). To remove everything: `terraform -chdir=infra/terraform destroy`.

## Next steps

- Re-index automatically on upload: S3 event on `docs/` triggering a second Lambda.
- Odoo in live mode from Lambda: the Odoo URL must be reachable from the internet, or
  run the Lambda inside a VPC with a NAT (not free).
- Conversation history in DynamoDB (always-free 25 GB) for multi-turn chats.
- GitHub Actions deploy with OIDC (no long-lived AWS keys in GitHub).
