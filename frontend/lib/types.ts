export type UserRole = "FACULTY_ADMIN" | "CAREER_MANAGER";

export type TokenResponse = {
  access_token: string;
  role: UserRole;
  career_id: number | null;
  full_name: string;
};

export type Career = {
  id: number;
  name: string;
  code: string;
};

export type Period = {
  id: number;
  year_label: string;
  cycle: number;
};

export type CareerKpi = {
  career_id: number;
  career_name: string;
  total_teachers: number;
  teachers_in_research: number;
  teachers_in_research_percent: number;
  projects: number;
  scientific_output_total: number;
  production: {
    articles: number;
    books: number;
    book_chapters: number;
    presentations: number;
    unclassified: number;
  };
  planned_output: number;
  output_compliance_percent: number;
  output_variation_percent: number;
};

export type NamedCount = {
  id?: number | null;
  name: string;
  count: number;
};

export type DashboardKpi = {
  year_label: string;
  cycle: number;
  reports_received: number;
  reports_requires_review: number;
  total_teachers: number;
  internal_participants_count?: number;
  internal_fca_count?: number;
  internal_other_faculty_count?: number;
  external_participants_count?: number;
  canonical_identities_count?: number;
  participant_appearances_count?: number;
  participant_roles_count?: number;
  authorships_count?: number;
  external_researchers_detected?: number;
  external_researchers_kpi_eligible?: number;
  external_researchers_pending_review?: number;
  pending_participants_count?: number;
  discarded_participants_count?: number;
  teachers_in_research_percent: number;
  scientific_output_total: number;
  scientific_output_published: number;
  scientific_output_in_review: number;
  scientific_output_detected?: number;
  scientific_output_kpi_eligible?: number;
  scientific_output_pending_review?: number;
  scientific_output_discarded?: number;
  projects_total: number;
  projects_current: number;
  projects_approved: number;
  projects_average_progress_percent: number;
  research_entities_detected?: number;
  research_entities_kpi_eligible?: number;
  research_entities_pending_review?: number;
  research_entities_excluded?: number;
  internal_teachers_by_career: NamedCount[];
  production_by_career: NamedCount[];
  external_researchers_by_university: NamedCount[];
  careers: CareerKpi[];
  alerts: string[];
};

export type GoalSummaryItem = {
  metric:
    | "TEACHERS_IN_RESEARCH"
    | "PROJECTS"
    | "SCIENTIFIC_OUTPUT"
    | "ARTICLES"
    | "BOOKS"
    | "BOOK_CHAPTERS"
    | "PRESENTATIONS";
  label: string;
  planned_value: number;
  reported_value: number;
  compliance_percent: number;
};

export type ImportStatus = {
  active: boolean;
  batch_id: number | null;
  total: number;
  queued: number;
  processing: number;
  processed: number;
  failed: number;
  ignored: number;
  requires_review: number;
};

export type ImportJob = {
  id: number;
  batch_id: number | null;
  source_type: string;
  filename: string;
  status: string;
  imported_by: string | null;
  summary: string | null;
  source_identifier: string | null;
  source_rev: string | null;
  error_reason: string | null;
  error_type: string | null;
  error_message: string | null;
  error_traceback: string | null;
  retry_count: number;
  max_retries: number;
  current_step: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  queue_ms: number | null;
  download_ms: number | null;
  text_extraction_ms: number | null;
  ocr_ms: number | null;
  parser_ms: number | null;
  persistence_ms: number | null;
  page_count: number | null;
  used_ocr: boolean;
  extraction_method: string | null;
  created_at: string;
  processed_at: string | null;
};

export type ImportBatch = {
  id: number;
  source_type: string;
  status: string;
  total_files: number;
  created_by: string | null;
  summary: string | null;
  created_at: string;
  completed_at: string | null;
};

export type Teacher = {
  id: number;
  career_id: number;
  full_name: string;
  institutional_email: string | null;
  research_hours: number;
  is_active: boolean;
  career_name: string;
  faculty_name: string;
  projects: {
    id: number;
    role: string;
    project_name: string;
    project_type: string;
  }[];
  productions: {
    id: number;
    production_type: string;
    title: string;
    year_label: string;
    cycle: number;
  }[];
};

export type Participant = {
  id: number;
  canonical_identity_key: string;
  canonical_name: string;
  identity_source: string;
  identity_confidence: number | null;
  identity_reason: string | null;
  identity_locked: boolean;
  person_key: string | null;
  person: string;
  person_type: string;
  type_label: string;
  roles: string[];
  role_labels: string[];
  affiliation: string;
  email: string | null;
  status: "validado" | "pendiente_validacion" | string;
  validation_status: string;
  overall_status: "validated" | "pending_review" | string;
  review_bucket: string;
  show_in_participants: boolean;
  kpi_eligible: boolean;
  pending_reasons: string[];
  possible_match_notice: string | null;
  possible_matches: {
    canonical_identity_key: string;
    canonical_name: string;
    variants: string[];
    document: string | null;
    documents: { import_job_id: number | null; filename: string | null }[];
    roles: string[];
    evidence_type: "Rol" | "Autoria" | "Rol y autoria" | string;
    reason_not_merged: string;
    pending_reasons: string[];
  }[];
  variants: {
    source_table: "person_roles" | "scientific_production_authors";
    source_id: number;
    role_type: string;
    person_type: string;
    raw_name: string | null;
    normalized_name: string | null;
    person_key: string | null;
    import_job_id: number | null;
    batch_id: number | null;
    document: string | null;
    source_page: number | null;
    source_section: string | null;
    validation_status: string;
    confidence: number | null;
    reason: string | null;
    production_id?: number | null;
    project_id: number | null;
    details: Record<string, unknown>;
  }[];
  documents: { import_job_id: number | null; filename: string | null }[];
  research_entities: {
    id: number;
    name: string | null;
    type: string;
    status: string | null;
    source_section: string | null;
    validation_status: string;
  }[];
  authorships: {
    production_id: number;
    title: string;
    raw_title: string;
    validation_status: string;
    status: string;
    kpi_eligible: boolean;
    source_file: string | null;
    source_page: number | null;
    source_section: string | null;
  }[];
  evidence: Record<string, unknown>[];
  validation_statuses: string[];
  participation_count: number;
  role_count: number;
  role_type_count: number;
  authorship_count: number;
  evidence_count: number;
  appearances: {
    id: number;
    role_type: string;
    role_label: string;
    person_type: string;
    source_file: string | null;
    source_page: number | null;
    source_section: string | null;
    raw_name: string | null;
    normalized_name: string | null;
    raw_value: string | null;
    normalized_value: string | null;
    confidence_score: number | null;
    reason: string | null;
    import_job_id: number | null;
    batch_id: number | null;
    product_id: number | null;
    project_id: number | null;
    details: Record<string, unknown>;
  }[];
  products: {
    id: number;
    title: string;
    status: string;
    source_section: string | null;
  }[];
  projects: {
    id: number;
    name: string;
    status: string;
    source_section: string | null;
  }[];
};

export type Production = {
  id: number;
  teacher_id: number | null;
  period_id: number;
  research_entity_id?: number | null;
  production_type: "ARTICLE" | "BOOK" | "BOOK_CHAPTER" | "PRESENTATION";
  title: string;
  canonical_title?: string | null;
  journal?: string | null;
  quartile?: "Q1" | "Q2" | "Q3" | "Q4" | null;
  link?: string | null;
  evidence_url?: string | null;
  status: string;
  validation_status?: string;
  review_reason?: string | null;
  raw_title?: string | null;
  normalized_title?: string | null;
  raw_authors?: string | null;
  normalized_authors?: string | null;
  source_file?: string | null;
  source_section?: string | null;
  source_page?: number | null;
  normalization_reason?: string | null;
  document?: string | null;
  confidence?: number | null;
  reason?: string | null;
  evidence_status?: "available" | "missing" | string;
  kpi_eligible?: boolean;
  visibility?: "eligible" | "pending" | "discarded";
  teacher_name: string | null;
  career_name: string | null;
  faculty_name: string | null;
  year_label: string;
  cycle: number;
  authors?: {
    id: number;
    production_id: number;
    canonical_identity_key: string;
    canonical_name: string;
    variants: string[];
    raw_author_name: string | null;
    normalized_author_name: string | null;
    person_type: string;
    production_role: "autor_producto";
    validation_status: string;
    identity_source: string | null;
    identity_confidence: number | null;
    identity_reason: string | null;
    confidence: number | null;
    reason: string | null;
  }[];
};

export type ImportedProgressReport = {
  id: number;
  import_job_id: number;
  career_name: string | null;
  year_label: string;
  cycle: number;
  teacher_identifier: string | null;
  teacher_name: string;
  articles: number;
  books: number;
  book_chapters: number;
  presentations: number;
  unclassified_products: number;
  projects: number;
  notes: string | null;
  research_topic: string | null;
  source_filename: string | null;
  source_path: string | null;
  has_source_file: boolean;
  group_projects: {
    code: string;
    name: string;
    director: string;
    progress: string;
    status: string;
  }[];
  research_entities: {
    type: string;
    code: string | null;
    name: string;
    director?: string | null;
    progress_percentage?: number | null;
    status?: string | null;
    validation_status: string;
    source_file?: string | null;
    source_page?: number | null;
    source_section?: string | null;
    confidence_score?: number | null;
    kpi_eligible: boolean;
    dashboard_counted: boolean;
    reconciliation_status: string;
    reason: string;
  }[];
  group_members: {
    name: string;
    faculty: string | null;
    career: string | null;
  }[];
  external_researchers: {
    name: string;
    institution: string | null;
    is_external: string;
  }[];
  scientific_products: {
    type: "ARTICLE" | "BOOK" | "BOOK_CHAPTER" | "PRESENTATION" | "UNCLASSIFIED";
    title: string;
    authors: string[];
    authorships?: {
      person_key: string;
      author_name: string;
      role: "autor_producto";
      product_title: string;
      product_type: string;
      source_section: string;
    }[];
    status: string | null;
    impact: string | null;
    link: string | null;
    source_section: string;
  }[];
  project_directors: string[];
  associated_teachers: string[];
  normalized_participants: {
    person_key: string;
    canonical_name: string;
    source_person_key?: string;
    source_canonical_name?: string;
    identity_resolution?: Record<string, unknown> | null;
    aliases: string[];
    person_type:
      | "docente_interno"
      | "investigador_externo"
      | "estudiante"
      | "graduado"
      | "participante_especial"
      | "pendiente_clasificacion";
    institutional_roles: string[];
    production_roles: string[];
    participations: Record<string, unknown>[];
    authorships: Record<string, unknown>[];
    evidences: Record<string, unknown>[];
    validation_status: string;
    status_reason: string;
    review_reason?: string;
    participant_scope?: "internal_fca" | "internal_other_faculty" | "external" | "pending" | "discarded";
    institution_scope?: string;
    faculty_scope?: string;
    detected_faculty?: string | null;
    participant_scope_reason?: string;
    review_bucket?:
      | "valid_person"
      | "pending_person"
      | "pending_author_classification"
      | "pending_merge"
      | "invalid_text_fragment"
      | "duplicate_evidence"
      | "pending_ocr"
      | "pending_product"
      | "pending_entity";
    show_in_participants?: boolean;
    kpi_eligible?: boolean;
    kpi_reason?: string;
    name_truncated?: boolean;
    products_authored_count?: number;
    products_entity_count?: number;
    matched_existing_person?: boolean;
    match_confidence?: number;
    match_reason?: string | null;
  }[];
  participants_summary: Record<string, number>;
  person_aliases: Record<string, unknown>[];
  possible_merge_review: Record<string, unknown>[];
};

export type Project = {
  id: number;
  period_id: number;
  name: string;
  project_type: "FCI" | "SEEDBED";
  description?: string | null;
  status: string;
  progress_percentage: number;
  year_label: string;
  cycle: number;
  teachers: {
    id: number;
    role: string;
    teacher_name: string;
    teacher_email: string;
    career_name: string;
    faculty_name: string;
  }[];
};
