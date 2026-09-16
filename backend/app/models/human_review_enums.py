from enum import StrEnum


class B2BCapability(StrEnum):
    RESEARCH_MANAGER = "RESEARCH_MANAGER"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"


class B2BAction(StrEnum):
    VIEW_FOUNDATIONS = "view_foundations"
    VIEW_AUDIT = "view_audit"
    APPLY_SCIENTIFIC = "apply_scientific"
    PROPOSE_SCIENTIFIC = "propose_scientific"
    REVERT_SCIENTIFIC = "revert_scientific"
    MANAGE_TECHNICAL_ACCESS = "manage_technical_access"


class ReviewCaseType(StrEnum):
    PERSON_IDENTITY = "person_identity"
    AUTHOR_IDENTITY = "author_identity"
    PRODUCT = "product"
    PROJECT_DIRECTOR_RELATION = "project_director_relation"
    EXTERNAL_IDENTITY = "external_identity"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    INVALID_TEXT = "invalid_text"
    NEW_EVIDENCE_CONFLICT = "new_evidence_conflict"


class ReviewCaseStatus(StrEnum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    AWAITING_GESTOR_APPROVAL = "awaiting_gestor_approval"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"


class ScientificStatus(StrEnum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    DISCARDED = "discarded"


class ReviewDecisionType(StrEnum):
    VALIDATED = "validated"
    CORRECTED = "corrected"
    LINKED = "linked"
    MERGED = "merged"
    MAINTAINED_SEPARATE = "maintained_separate"
    SEPARATED = "separated"
    REJECTED = "rejected"
    DISCARDED = "discarded"
    MAINTAINED = "maintained"
    REVERTED = "reverted"


class DecisionLifecycle(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    DECLINED = "declined"
    SUPERSEDED = "superseded"


class DecisionScope(StrEnum):
    GLOBAL_IDENTITY = "global_identity"
    RECORD = "record"
    DOCUMENT = "document"
    RELATIONSHIP = "relationship"
    PERIOD = "period"


class ReviewActorType(StrEnum):
    HUMAN = "human"
    LEGACY = "legacy"


class ReviewTargetTable(StrEnum):
    PERSON_ROLES = "person_roles"
    SCIENTIFIC_PRODUCTION_AUTHORS = "scientific_production_authors"
    SCIENTIFIC_PRODUCTIONS = "scientific_productions"
    RESEARCH_ENTITIES = "research_entities"
    EXTERNAL_RESEARCHERS = "external_researchers"


class CanonicalIdentityType(StrEnum):
    INTERNAL_PERSON = "internal_person"
    EXTERNAL_PERSON = "external_person"
    UNCLASSIFIED_PERSON = "unclassified_person"


class CanonicalIdentityStatus(StrEnum):
    ACTIVE = "active"
    MERGED = "merged"
    SUPERSEDED = "superseded"


class CanonicalIdentityOrigin(StrEnum):
    B1_LOCKED = "b1_locked"
    HUMAN = "human"


class PersonAliasClass(StrEnum):
    PERSON_NAME = "person_name"


class PersonAliasScope(StrEnum):
    GLOBAL_IDENTITY = "global_identity"


class PersonAliasStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class OverrideField(StrEnum):
    CANONICAL_IDENTITY_KEY = "canonical_identity_key"
    CANONICAL_NAME = "canonical_name"
    PRODUCT_TITLE = "product_title"
    AUTHOR_IDENTITY_KEY = "author_identity_key"
    PROJECT_DIRECTOR_IDENTITY_KEY = "project_director_identity_key"
    PROJECT_DIRECTOR_RELATIONSHIP_STATUS = "project_director_relationship_status"
    EXTERNAL_IDENTITY_KEY = "external_identity_key"
    EXTERNAL_INSTITUTION = "external_institution"
    SCIENTIFIC_STATUS = "scientific_status"


class OverrideScope(StrEnum):
    GLOBAL_IDENTITY = "global_identity"
    RECORD = "record"
    DOCUMENT = "document"
    RELATIONSHIP = "relationship"
    PERIOD = "period"


class AuditEventType(StrEnum):
    CASE_BACKFILLED = "case_backfilled"
    LOCKED_DECISION_IMPORTED = "locked_decision_imported"
    IDENTITY_CREATED = "identity_created"
    ALIAS_CREATED = "alias_created"
    OVERRIDE_CREATED = "override_created"
    CAPABILITY_ASSIGNED = "capability_assigned"
    CAPABILITY_REVOKED = "capability_revoked"
    AUDIT_CORRECTED = "audit_corrected"
    FUNCTIONAL_REVERSION = "functional_reversion"
    SCIENTIFIC_DECISION_APPLIED = "scientific_decision_applied"


class BackfillSourceMembership(StrEnum):
    CANONICAL_PENDING = "canonical_pending"
    PRODUCT_PENDING = "product_pending"
    DIRECTOR_RELATION_PENDING = "director_relation_pending"
    EXTERNAL_PENDING = "external_pending"
    POSSIBLE_MATCH = "possible_match"
    IDENTITY_LOCKED = "identity_locked"
