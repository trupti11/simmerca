"""Simmerca backend stack: one per stage (staging, prod)."""

from __future__ import annotations

import json
import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    SecretValue,
    Stack,
)
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_authorizers as authorizers
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_sqs as sqs
from constructs import Construct

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


class SimmercaStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, stage: str, cfg: dict, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        removal = RemovalPolicy.RETAIN if cfg["retain_data"] else RemovalPolicy.DESTROY

        # ---------------- data ----------------
        table = ddb.Table(
            self, "Table",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            time_to_live_attribute="expires_at",
            removal_policy=removal,
        )

        bucket = s3.Bucket(
            self, "Images",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=cfg["retain_data"],
            removal_policy=removal,
            auto_delete_objects=not cfg["retain_data"],
            cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.PUT, s3.HttpMethods.GET],
                              allowed_origins=cfg["allowed_origins"], allowed_headers=["*"], max_age=3000)],
        )

        # One JSON secret holds every credential. Fill it after deploy (see SETUP.md).
        secret = sm.Secret(self, "Credentials", description=f"Simmerca {stage} channel + Twilio credentials",
                           secret_string_value=SecretValue.unsafe_plain_text("{}"))
        secret.apply_removal_policy(RemovalPolicy.RETAIN)

        def queue(name: str, visibility: int) -> tuple[sqs.Queue, sqs.Queue]:
            dlq = sqs.Queue(self, f"{name}Dlq", retention_period=Duration.days(14),
                            encryption=sqs.QueueEncryption.SQS_MANAGED)
            q = sqs.Queue(self, name, visibility_timeout=Duration.seconds(visibility),
                          encryption=sqs.QueueEncryption.SQS_MANAGED,
                          dead_letter_queue=sqs.DeadLetterQueue(queue=dlq, max_receive_count=5))
            return q, dlq

        sync_q, sync_dlq = queue("SyncQueue", 1800)  # >= 6x the worker timeout
        intel_q, intel_dlq = queue("IntelQueue", 720)

        # ---------------- auth ----------------
        pool = cognito.UserPool(
            self, "Users",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(min_length=10, require_symbols=False),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=removal,
        )
        for group in ("admin", "buyer"):
            cognito.CfnUserPoolGroup(self, f"Group{group.title()}", user_pool_id=pool.user_pool_id, group_name=group)
        client = pool.add_client("Web", auth_flows=cognito.AuthFlow(user_srp=True, user_password=True),
                                 generate_secret=False, prevent_user_existence_errors=True)

        # ---------------- compute ----------------
        env = {
            "STAGE": stage,
            "TENANT": cfg["tenant"],
            "TABLE_NAME": table.table_name,
            "BUCKET_NAME": bucket.bucket_name,
            "SECRET_ARN": secret.secret_arn,
            "SYNC_QUEUE_URL": sync_q.queue_url,
            "INTEL_QUEUE_URL": intel_q.queue_url,
            "BEDROCK_VISION_MODEL_ID": cfg["bedrock_vision_model_id"],
            "BEDROCK_TEXT_MODEL_ID": cfg["bedrock_text_model_id"],
            "PRICING_SETTINGS": json.dumps(cfg["pricing_settings"]),
            "PUBLIC_BASE_URL": cfg.get("public_base_url", ""),
            "GITHUB_REPO": cfg["github_repo"],
        }
        code = lambda_.Code.from_asset(SRC, exclude=["**/__pycache__", "*.pyc"])

        def fn(name: str, handler: str, timeout: int, memory: int = 512) -> lambda_.Function:
            f = lambda_.Function(
                self, name, runtime=lambda_.Runtime.PYTHON_3_12, architecture=lambda_.Architecture.ARM_64,
                handler=f"simmerca.lambdas.{handler}", code=code, timeout=Duration.seconds(timeout),
                memory_size=memory, environment=env,
                log_group=logs.LogGroup(self, f"{name}Logs", retention=logs.RetentionDays.ONE_MONTH,
                                        removal_policy=RemovalPolicy.DESTROY),
            )
            table.grant_read_write_data(f)
            secret.grant_read(f)
            sync_q.grant_send_messages(f)
            intel_q.grant_send_messages(f)
            bucket.grant_read_write(f)
            return f

        api_fn = fn("Api", "api_handler", 20)
        sync_fn = fn("SyncWorker", "sync_worker", 300)
        intel_fn = fn("IntelWorker", "intelligence_worker", 120, 1024)
        poll_fn = fn("Poller", "poller", 120)
        alarm_fn = fn("AlarmToIssue", "alarm_to_issue", 20, 256)
        for f in (api_fn, sync_fn, poll_fn):
            secret.grant_write(f)  # Etsy rotates refresh tokens; any function holding the adapter may refresh
        bedrock = iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[
            "arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:*:{self.account}:inference-profile/*"])
        api_fn.add_to_role_policy(bedrock)
        intel_fn.add_to_role_policy(bedrock)

        sync_fn.add_event_source(sources.SqsEventSource(sync_q, batch_size=2, report_batch_item_failures=True))
        intel_fn.add_event_source(sources.SqsEventSource(intel_q, batch_size=2, report_batch_item_failures=True))
        events.Rule(self, "PollEvery10Min", schedule=events.Schedule.rate(Duration.minutes(10)),
                    targets=[targets.LambdaFunction(poll_fn)])

        post_confirm = lambda_.Function(
            self, "PostConfirmation", runtime=lambda_.Runtime.PYTHON_3_12, architecture=lambda_.Architecture.ARM_64,
            handler="simmerca.lambdas.cognito_post_confirmation", code=code, timeout=Duration.seconds(10),
            environment={"STAGE": stage})
        pool.add_trigger(cognito.UserPoolOperation.POST_CONFIRMATION, post_confirm)
        # Avoid a circular dependency (pool -> trigger -> pool ARN) by using a wildcard on this account.
        post_confirm.add_to_role_policy(iam.PolicyStatement(
            actions=["cognito-idp:AdminAddUserToGroup"],
            resources=[f"arn:aws:cognito-idp:{self.region}:{self.account}:userpool/*"]))

        # ---------------- API ----------------
        http_api = apigw.HttpApi(
            self, "HttpApi",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=cfg["allowed_origins"],
                allow_methods=[apigw.CorsHttpMethod.ANY],
                allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
                max_age=Duration.hours(1)),
        )
        integ = integrations.HttpLambdaIntegration("ApiInteg", api_fn)
        auth = authorizers.HttpUserPoolAuthorizer("Cognito", pool, user_pool_clients=[client])
        verbs = [apigw.HttpMethod.GET, apigw.HttpMethod.POST, apigw.HttpMethod.PUT, apigw.HttpMethod.PATCH,
                 apigw.HttpMethod.DELETE]
        http_api.add_routes(path="/public/{proxy+}", methods=[apigw.HttpMethod.GET], integration=integ)
        http_api.add_routes(path="/webhooks/{proxy+}", methods=[apigw.HttpMethod.POST], integration=integ)
        http_api.add_routes(path="/admin/{proxy+}", methods=verbs, integration=integ, authorizer=auth)
        http_api.add_routes(path="/b2b/{proxy+}", methods=verbs, integration=integ, authorizer=auth)
        http_api.default_stage.node.default_child.add_property_override("DefaultRouteSettings", {
            "ThrottlingBurstLimit": 50, "ThrottlingRateLimit": 25})

        # ---------------- alarms -> email + GitHub issue (ops agent) ----------------
        topic = sns.Topic(self, "Alarms")
        topic.add_subscription(subs.EmailSubscription(cfg["alert_email"]))
        topic.add_subscription(subs.LambdaSubscription(alarm_fn))
        action = cw_actions.SnsAction(topic)

        def alarm(name: str, metric: cw.Metric, threshold: float) -> None:
            a = cw.Alarm(self, name, metric=metric, threshold=threshold, evaluation_periods=1,
                         comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                         treat_missing_data=cw.TreatMissingData.NOT_BREACHING)
            a.add_alarm_action(action)

        alarm("SyncDlqNotEmpty", sync_dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(5)), 1)
        alarm("IntelDlqNotEmpty", intel_dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(5)), 1)
        for f in (api_fn, sync_fn, intel_fn, poll_fn):
            alarm(f"{f.node.id}Errors", f.metric_errors(period=Duration.minutes(5)), 3)
        alarm("Api5xx", cw.Metric(namespace="AWS/ApiGateway", metric_name="5xx",
                                  dimensions_map={"ApiId": http_api.api_id, "Stage": "$default"},
                                  period=Duration.minutes(5), statistic="Sum"), 5)

        budgets.CfnBudget(self, "MonthlyBudget", budget=budgets.CfnBudget.BudgetDataProperty(
            budget_type="COST", time_unit="MONTHLY",
            budget_limit=budgets.CfnBudget.SpendProperty(amount=cfg["monthly_budget_usd"], unit="USD")),
            notifications_with_subscribers=[budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    comparison_operator="GREATER_THAN", notification_type="ACTUAL", threshold=80),
                subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL",
                                                                  address=cfg["alert_email"])])])

        # ---------------- outputs (paste into Lovable env) ----------------
        CfnOutput(self, "ApiUrl", value=http_api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=client.user_pool_client_id)
        CfnOutput(self, "Region", value=self.region)
        CfnOutput(self, "SecretArn", value=secret.secret_arn)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "BucketName", value=bucket.bucket_name)


class GithubOidcStack(Stack):
    """Deploy ONCE per account with admin credentials. Lets GitHub Actions deploy without stored keys."""

    def __init__(self, scope: Construct, cid: str, *, repo: str, stage: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        provider = iam.OpenIdConnectProvider(self, "GitHub", url="https://token.actions.githubusercontent.com",
                                             client_ids=["sts.amazonaws.com"])
        # prod: only the `production` GitHub environment (which requires owner approval) can assume the role.
        subject = f"repo:{repo}:environment:production" if stage == "prod" else f"repo:{repo}:*"
        role = iam.Role(
            self, "DeployRole", role_name=f"simmerca-github-deploy-{stage}",
            assumed_by=iam.WebIdentityPrincipal(provider.open_id_connect_provider_arn, conditions={
                "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
                "StringLike": {"token.actions.githubusercontent.com:sub": subject},
            }),
            max_session_duration=Duration.hours(1),
        )
        # CDK deploys by assuming the bootstrap roles; this role only needs to assume them.
        role.add_to_policy(iam.PolicyStatement(actions=["sts:AssumeRole"],
                                               resources=[f"arn:aws:iam::{self.account}:role/cdk-*"]))
        CfnOutput(self, "DeployRoleArn", value=role.role_arn)
