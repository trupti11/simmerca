# Setup — from zero to a running loop

Order matters. Roughly one focused day for steps 1–8; channel approvals (Etsy, Meta, WhatsApp sender) can take days, so start those first.

## 0. Start the slow approvals today
- [ ] **WhatsApp sender** on Twilio (needs Meta Business verification): Twilio Console → Messaging → Senders → WhatsApp.
- [ ] **Etsy app**: etsy.com/developers → Create app → request scopes `listings_r listings_w transactions_r`. Add callback `http://localhost:3003/callback`.
- [ ] **Meta catalog**: Commerce Manager → your catalog; Business Settings → System users → token with `catalog_management`.
- [ ] **Amazon SP-API** (phase 2): register as a developer now; approval takes weeks.
- [ ] **Bedrock model access**: AWS console → Bedrock → Model access → enable one vision-capable model and one text model in `us-west-2`. Copy their model or inference-profile ids.

## 1. Tools
```bash
# macOS
brew install python@3.12 node awscli gh
npm install -g aws-cdk@2 @anthropic-ai/claude-code
aws configure sso          # or aws configure, for an admin profile
```

## 2. Repo
```bash
unzip simmerca-backend.zip && cd simmerca-backend
git init -b main && git add . && git commit -m "Simmerca backend v0.1 with loop engineering"
gh repo create simmerca-backend --private --source . --push
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -r infra/requirements.txt
pytest && python evals/run.py      # should be green before anything else
```

## 3. Fill `infra/stages.py`
Set `github_repo`, `alert_email`, the two Bedrock model ids, and (later) `public_base_url`.
Two AWS accounts (staging, prod) is best. One account works to start: use the same id for both.

## 4. Bootstrap CDK + GitHub OIDC (once per account, with admin credentials)
```bash
export STAGING_ACCOUNT_ID=111111111111 PROD_ACCOUNT_ID=222222222222
cdk bootstrap aws://$STAGING_ACCOUNT_ID/us-west-2
cdk bootstrap aws://$PROD_ACCOUNT_ID/us-west-2
cd infra
cdk deploy -c stage=staging -c oidc=1     # prints DeployRoleArn
cdk deploy -c stage=prod -c oidc=1        # prints DeployRoleArn
cd ..
```

## 5. GitHub settings
```bash
gh variable set STAGING_ACCOUNT_ID --body $STAGING_ACCOUNT_ID
gh variable set PROD_ACCOUNT_ID --body $PROD_ACCOUNT_ID
gh variable set AWS_STAGING_ROLE_ARN --body arn:aws:iam::$STAGING_ACCOUNT_ID:role/simmerca-github-deploy-staging
gh variable set AWS_PROD_ROLE_ARN --body arn:aws:iam::$PROD_ACCOUNT_ID:role/simmerca-github-deploy-prod
gh secret set ANTHROPIC_API_KEY          # paste your key
gh label create agent:build --color 5319e7 ; gh label create agent:triage --color d93f0b
```
In the GitHub UI:
- Settings → Environments → create `staging` and `production`; on `production` add yourself as **Required reviewer**.
- Settings → Branches → protect `main`: require PR, require status checks `test` and `synth`, require 1 approval.
- Install the Claude GitHub app: run `claude` in the repo, then `/install-github-app`.

## 6. First deploy
```bash
cd infra && cdk deploy -c stage=staging --outputs-file outputs.json && cd ..
cat infra/outputs.json     # ApiUrl, UserPoolId, UserPoolClientId, SecretArn, BucketName
```
After this, every merge to `main` deploys staging automatically and waits for your approval to deploy prod.

## 7. Credentials secret
```bash
cp docs/secret.template.json /tmp/secret.json   # fill it in; never commit it
aws secretsmanager put-secret-value --secret-id <SecretArn> --secret-string file:///tmp/secret.json
rm /tmp/secret.json
```
- **Shopify**: Admin → Settings → Apps → Develop apps → create app; Admin API scopes `read_products write_products read_inventory write_inventory read_orders read_locations`; install; copy the `shpat_` token. Location id: run `{ locations(first:5){ nodes{ id name } } }` in the GraphiQL app. Webhook: create `orders/create` → `<ApiUrl>/webhooks/shopify`, JSON; put the signing secret shown for that webhook in `shopify_webhook_secret`.
- **Etsy**: `python scripts/etsy_oauth.py <KEYSTRING>` → refresh token. Shop id: `GET /v3/application/users/<user_id>/shops`.
- **Meta**: catalog id + system-user token. Create catalog items with **content id / retailer id = Simmerca sku**.
- The Lambdas cache secrets per container; after changing the secret, redeploy or wait for new containers.

## 8. Admin user and WhatsApp webhook
```bash
aws cognito-idp admin-create-user --user-pool-id <UserPoolId> --username you@aaloradrapes.com \
  --user-attributes Name=email,Value=you@aaloradrapes.com Name=email_verified,Value=true
aws cognito-idp admin-add-user-to-group --user-pool-id <UserPoolId> --username you@aaloradrapes.com --group-name admin
```
- Twilio → your WhatsApp sender → "When a message comes in": `POST <ApiUrl>/webhooks/whatsapp/<whatsapp_path_token>`.
- Set `public_base_url` in `infra/stages.py` to `<ApiUrl>` (Twilio signs the exact URL) and redeploy.
- Register each weaver in Admin → Suppliers with their WhatsApp number in `+91...` format and language.

## 9. Lovable
Follow `frontend/LOVABLE_PROMPT.md` (env vars from step 6).

## 10. Smoke test (staging)
- [ ] Admin login works; dashboard loads.
- [ ] Create product (fabric Kanchi Silk) → sku `KS-0001`; upload a photo; "Analyse with AI" → appears in Review queue; approve.
- [ ] Set price, activate, link `aalora` → visible on storefront.
- [ ] From a registered phone: `ADD KS-0001 2` → confirm prompt → `YES` → "now has 2"; ledger shows it.
- [ ] Link `shopify` (sku must exist there) → Shopify inventory shows 2. Place a test order → stock 1, all channels updated.
- [ ] `aws sqs get-queue-attributes` on the DLQs → 0 messages. Alerts screen empty.

## Things this repo cannot verify for you (do them in the first loops)
1. **Shopify contract test** against a development store (API version + `inventorySetQuantities` input).
2. **Etsy contract test** on one test listing (x-api-key format, inventory PUT for your variation setup).
3. **Meta contract test** on a test catalog (`items_batch` field formats, Graph version).
4. **Native-speaker review** of WhatsApp keywords and reply translations (mr, bn, gu, ta, te, or, as, kn).
5. **Replace the attribute eval seed set** with 100 real labeled photos and run `python evals/run.py --live`.
