# Runtime Config

## bootstrap/ingredients

Phase one keeps this directory empty except for README placeholders. Plugin ingredient templates
are registered only through Dishwasher's internal service boundary at
`/api/v1/internal/service-registry/ingredients/bulk`.

`poundcake-bootstrap` is limited to startup-only plugin metadata, credential/bootstrap state, and
bootstrap hooks. It does not register ingredient templates.

## bootstrap/recipes

Phase one keeps this directory empty except for README placeholders. Recipe catalogs will be
reintroduced after the plugin contract is fully wired.

Recipe catalogs remain Dishwasher-owned manifest sync state rather than startup bootstrap input.
