"""
Cognito Pre Token Generation trigger (V1_0).

Runs synchronously before Cognito issues ID tokens. Adds an unprefixed
'tenant_id' alias so tenant_middleware.py can read it with either
jwt_claims.get('custom:tenant_id') or jwt_claims.get('tenant_id').

Custom attributes (custom:*) are already included in the ID token when
the User Pool Client lists them in ReadAttributes — this trigger only
adds the convenience alias.
"""

import logging

logger = logging.getLogger()


def lambda_handler(event: dict, context) -> dict:
    attrs = event.get("request", {}).get("userAttributes", {})
    tenant_id = attrs.get("custom:tenant_id", "")

    if tenant_id:
        event["response"]["claimsOverrideDetails"] = {
            "claimsToAddOrOverride": {"tenant_id": tenant_id},
        }
        logger.info("pre_token_gen | tenant=%s sub=%s", tenant_id, attrs.get("sub"))
    else:
        logger.warning(
            "pre_token_gen | custom:tenant_id missing for sub=%s — "
            "set the attribute on the Cognito user before they can access the API",
            attrs.get("sub"),
        )

    return event
