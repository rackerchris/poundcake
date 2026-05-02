"""Reserved PoundCake internal service plugin identities."""

INTERNAL_SERVICE_TYPES: frozenset[str] = frozenset(
    {
        "credential-manager",
        "prep-chef",
        "expediter-runner",
        "timer",
        "dishwasher",
    }
)

INTERNAL_WORKER_SERVICE_TYPES: frozenset[str] = frozenset(
    {
        "prep-chef",
        "expediter-runner",
        "timer",
        "dishwasher",
    }
)

INTERNAL_SERVICE_IDENTITY_VIEW_BY_SERVICE: dict[str, str] = {
    "prep-chef": "service_identity_credentials_prep_chef",
    "expediter-runner": "service_identity_credentials_expediter_runner",
    "timer": "service_identity_credentials_timer",
    "dishwasher": "service_identity_credentials_dishwasher",
}
