# Effective Data Global Revision Design

## Goal

Propagate every committed Human Review decision to all active authenticated
browser sessions without a manual refresh, while keeping the effective-data
readers as the sole source for operational modules and KPI calculations.

## Decision

The backend exposes an authenticated, lightweight global revision derived from
approved review decisions. The revision is the count of committed
`review_decisions` rows. Commands only create a decision in their existing
transaction, so a new revision is visible to clients only after that transaction
commits; a rollback leaves it unchanged. The endpoint returns no review content,
only the numeric revision.

The browser stores the last observed revision in the shared data-cache provider.
One polling lifecycle per mounted provider checks it every three seconds while a
document is visible and immediately on `visibilitychange`/focus. A different
revision publishes the existing effective-data refresh event. Existing mounted
readers then invalidate and refetch Dashboard, canonical participants,
production, projects, entities, cards and charts. Equal revisions trigger no
data refetch.

## Failure Handling

The revision request is authenticated. `401` and `403` stop the current polling
lifecycle until the provider remounts with an authenticated session. Temporary
network failures retain the last revision and retry at the normal interval.
The lifecycle owns one interval and cleans up all listeners on unmount.

## Acceptance Criteria

- User A confirms a decision; PostgreSQL commits it before the revision changes.
- User B observes the new revision within approximately three seconds and
  refreshes mounted effective-data consumers without a manual action.
- A rollback does not change the revision.
- The current Human Review success summary remains mounted after local
  invalidation.
- Dashboard/KPI values are freshly fetched. A descriptive correction may retain
  the same numeric total when its formula is unaffected, but cards and charts
  must be based on the fresh response.
