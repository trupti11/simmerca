"""Per-stage configuration. OWNER-CONTROLLED (agents may not edit).

Fill in the TODO values before the first deploy. Account ids come from the environment so they are
never committed: STAGING_ACCOUNT_ID, PROD_ACCOUNT_ID.
"""

import os

COMMON = {
    "region": "us-west-2",
    "tenant": "aalora",
    "github_repo": "TODO-owner/simmerca-backend",       # e.g. "truptirussell/simmerca-backend"
    "alert_email": "TODO@example.com",
    # Pick the current models enabled in your Bedrock console (Model access). Inference-profile ids
    # (e.g. "us.anthropic....") are needed for cross-region models.
    "bedrock_vision_model_id": "TODO-bedrock-vision-model-id",
    "bedrock_text_model_id": "TODO-bedrock-text-model-id",
    "pricing_settings": {"min_margin_pct": 25, "channel_fee_pct": 10, "tiers": [[1, 0], [5, 10], [10, 18]]},
}

STAGES = {
    "staging": {
        **COMMON,
        "account": os.environ.get("STAGING_ACCOUNT_ID"),
        "allowed_origins": ["http://localhost:5173", "https://*.lovable.app", "https://*.lovableproject.com"],
        "monthly_budget_usd": 50,
        "retain_data": False,
        "public_base_url": "",  # set to the API URL after first deploy (Twilio signature uses it)
    },
    "prod": {
        **COMMON,
        "account": os.environ.get("PROD_ACCOUNT_ID"),
        "allowed_origins": ["https://aaloradrapes.com", "https://www.aaloradrapes.com"],
        "monthly_budget_usd": 150,
        "retain_data": True,
        "public_base_url": "",
    },
}
