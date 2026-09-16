import os
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.entities import (
    AcademicPeriod,
    ImportJob,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
)
from app.models.enums import ProductionType
from app.services.validated_read_service import ValidatedReadService
from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url


class CanonicalOperationalReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._schema_context = isolated_postgres_schema(
            require_b2b1_test_database_url(),
            "canonical_readers",
        )
        cls.engine = cls._schema_context.__enter__()
        cls.addClassCleanup(cls._schema_context.__exit__, None, None, None)
        Base.metadata.create_all(cls.engine)

    def setUp(self):
        self.db = Session(self.engine)
        self.period = AcademicPeriod(year_label="2090-2091", cycle=1)
        self.job = ImportJob(
            source_type="PROGRESS_PDF",
            filename="canonical.pdf",
            status="SUCCESS",
            is_current=True,
        )
        self.db.add_all([self.period, self.job])
        self.db.flush()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _role(
        self,
        *,
        raw_name: str,
        canonical_key: str,
        canonical_name: str,
        status: str = "pending_review",
        role_type: str = "integrante_interno",
        person_type: str = "unresolved",
        entity: ResearchEntity | None = None,
        reason: str = "Pendiente de validacion.",
        job: ImportJob | None = None,
    ) -> PersonRole:
        source_job = job or self.job
        row = PersonRole(
            period_id=self.period.id,
            import_job_id=source_job.id,
            role_type=role_type,
            person_type=person_type,
            person_key=f"legacy:{raw_name}",
            canonical_identity_key=canonical_key,
            canonical_name=canonical_name,
            identity_source="pending" if canonical_key.startswith("pending:") else "institutional",
            identity_confidence=0.75 if canonical_key.startswith("pending:") else 0.99,
            identity_reason=reason,
            raw_name=raw_name,
            normalized_name=raw_name,
            source_file=source_job.filename,
            source_page=2,
            source_section="integrantes_internos",
            validation_status=status,
            research_entity_id=entity.id if entity else None,
        )
        self.db.add(row)
        return row

    def _production(
        self,
        title: str,
        *,
        status: str = "validated",
        author_key: str | None = None,
        author_name: str | None = None,
        author_status: str = "validated",
    ) -> ScientificProduction:
        product = ScientificProduction(
            period_id=self.period.id,
            import_job_id=self.job.id,
            production_type=ProductionType.ARTICLE,
            title=title,
            raw_title=f"RAW {title}",
            source_file=self.job.filename,
            source_page=4,
            source_section="produccion_cientifica",
            confidence_score=0.9,
            validation_status=status,
            review_reason="Revisar evidencia" if status != "validated" else None,
        )
        self.db.add(product)
        self.db.flush()
        if author_key:
            self.db.add(
                ScientificProductionAuthor(
                    production_id=product.id,
                    import_job_id=self.job.id,
                    author_order=1,
                    raw_author_name=author_name,
                    normalized_author_name=author_name,
                    canonical_identity_key=author_key,
                    canonical_name=author_name,
                    identity_source="institutional",
                    identity_confidence=0.99,
                    identity_reason="Coincidencia institucional unica.",
                    author_type="internal",
                    validation_status=author_status,
                    source_file=self.job.filename,
                    source_page=4,
                    source_section="produccion_cientifica",
                )
            )
        return product

    def test_groups_roles_and_authorships_by_persisted_canonical_key(self):
        key = "institutional:zambrano"
        self._role(
            raw_name="Fernando Zambrano Farias",
            canonical_key=key,
            canonical_name="Fernando Jose Zambrano Farias",
            status="validated",
        )
        self._role(
            raw_name="Fernando Jose Zambrano Farias",
            canonical_key=key,
            canonical_name="Fernando Jose Zambrano Farias",
        )
        self._production(
            "Producto Zambrano",
            author_key=key,
            author_name="Fernando Jose Zambrano Farias",
        )
        self.db.flush()

        participants = ValidatedReadService(self.db).canonical_participants(period_id=self.period.id)

        self.assertEqual(len(participants), 1)
        participant = participants[0]
        self.assertEqual(participant["canonical_identity_key"], key)
        self.assertEqual(
            {variant["raw_name"] for variant in participant["variants"]},
            {"Fernando Zambrano Farias", "Fernando Jose Zambrano Farias"},
        )
        self.assertEqual([item["title"] for item in participant["authorships"]], ["Producto Zambrano"])
        self.assertEqual(participant["participation_count"], 1)
        self.assertEqual(participant["role_count"], 2)
        self.assertEqual(participant["authorship_count"], 1)

    def test_keeps_pending_keys_separate_and_never_merges_by_name(self):
        self._role(
            raw_name="Delgado Litard",
            canonical_key="pending:delgado-1",
            canonical_name="Delgado Litard",
        )
        self._role(
            raw_name="Delgado Litardo",
            canonical_key="pending:delgado-2",
            canonical_name="Delgado Litardo",
        )
        self._role(
            raw_name="Manosalvas Le",
            canonical_key="pending:manosalvas",
            canonical_name="Manosalvas Le",
        )
        self.db.flush()

        participants = ValidatedReadService(self.db).canonical_participants(period_id=self.period.id)

        self.assertEqual(
            {item["canonical_identity_key"] for item in participants},
            {"pending:delgado-1", "pending:delgado-2", "pending:manosalvas"},
        )
        self.assertTrue(all(item["overall_status"] == "pending_review" for item in participants))
        self.assertTrue(all(not item["kpi_eligible"] for item in participants))

    def test_person_products_come_only_from_explicit_authorships(self):
        maria_key = "teacher:568"
        jose_key = "external:174"
        self._role(
            raw_name="Maria Estefania Sanchez Pacheco",
            canonical_key=maria_key,
            canonical_name="Maria Estefania Sanchez Pacheco",
            status="validated",
        )
        self._role(
            raw_name="Jose Manuel Santos Jaen",
            canonical_key=jose_key,
            canonical_name="Jose Manuel Santos Jaen",
        )
        self._production("Producto uno", author_key=maria_key, author_name="Maria Estefania Sanchez Pacheco")
        self._production("Producto dos", author_key=maria_key, author_name="Maria Estefania Sanchez Pacheco")
        self._production("Producto del mismo informe sin Jose", author_key="teacher:999", author_name="Otra Persona")
        self.db.flush()

        participants = {
            item["canonical_identity_key"]: item
            for item in ValidatedReadService(self.db).canonical_participants(period_id=self.period.id)
        }

        self.assertEqual(len(participants[maria_key]["authorships"]), 2)
        self.assertEqual(participants[jose_key]["authorships"], [])

    def test_production_visibility_preserves_kpi_rule(self):
        self._production("Elegible", author_key="teacher:1", author_name="Autora Valida")
        self._production(
            "Producto pendiente",
            status="pending_review",
            author_key="pending:1",
            author_name="Autora Pendiente",
            author_status="pending_author_resolution",
        )
        self._production(
            "Autor pendiente",
            author_key="pending:2",
            author_name="Autor Pendiente",
            author_status="pending_author_resolution",
        )
        self._production(
            "Descartado",
            status="discarded_invalid",
            author_key="pending:3",
            author_name="Texto Invalido",
            author_status="discarded_invalid",
        )
        self.db.flush()
        service = ValidatedReadService(self.db)

        self.assertEqual([item["title"] for item in service.production_views(self.period.id, visibility="eligible")], ["Elegible"])
        self.assertEqual(
            {item["title"] for item in service.production_views(self.period.id, visibility="pending")},
            {"Producto pendiente", "Autor pendiente"},
        )
        self.assertEqual([item["title"] for item in service.production_views(self.period.id, visibility="discarded")], ["Descartado"])
        self.assertEqual(len(service.production_views(self.period.id, visibility="all")), 4)
        self.assertTrue(service.production_views(self.period.id, visibility="eligible")[0]["kpi_eligible"])

    def test_project_and_director_status_are_independent(self):
        entity = ResearchEntity(
            period_id=self.period.id,
            import_job_id=self.job.id,
            type="proyecto_fci",
            name="Proyecto validado",
            director_name="Roberth Fabian Ramirez Grand",
            validation_status="validated",
        )
        self.db.add(entity)
        self.db.flush()
        self._role(
            raw_name="Roberth Fabian Ramirez Grand",
            canonical_key="pending:ramirez",
            canonical_name="Roberth Fabian Ramirez Grand",
            role_type="director",
            entity=entity,
            reason="Nombre truncado sin corroboracion suficiente.",
        )
        self.db.flush()

        state = ValidatedReadService(self.db).project_director_state(entity)

        self.assertEqual(state["project_validation_status"], "validated")
        self.assertEqual(state["director_validation_status"], "pending_review")
        self.assertEqual(state["director_relationship_validation_status"], "pending_review")
        self.assertEqual(state["director_identity_validation_status"], "pending_review")
        self.assertEqual(state["director_canonical_identity_key"], "pending:ramirez")
        self.assertEqual(
            state["display_status"],
            "Proyecto validado / identidad pendiente / relación como director pendiente",
        )

    def test_validated_identity_does_not_promote_pending_director_relationship(self):
        entity = ResearchEntity(
            period_id=self.period.id,
            import_job_id=self.job.id,
            type="proyecto_fci",
            name="Proyecto con relacion pendiente",
            director_name="Fernando Zambrano Farias",
            validation_status="validated",
        )
        self.db.add(entity)
        self.db.flush()
        key = "institutional:zambrano"
        self._role(
            raw_name="Fernando Zambrano Farias",
            canonical_key=key,
            canonical_name="Fernando Jose Zambrano Farias",
            role_type="director",
            entity=entity,
            status="pending_review",
            reason="La relacion director-proyecto requiere revision.",
        )
        self._role(
            raw_name="Fernando Jose Zambrano Farias",
            canonical_key=key,
            canonical_name="Fernando Jose Zambrano Farias",
            role_type="integrante_interno",
            status="validated",
            reason="Identidad demostrada por evidencia institucional.",
        )
        self.db.flush()

        state = ValidatedReadService(self.db).project_director_state(entity)

        self.assertEqual(state["director_identity_validation_status"], "validated")
        self.assertEqual(state["director_relationship_validation_status"], "pending_review")
        self.assertEqual(
            state["director_display_status"],
            "Identidad validada / relación como director pendiente",
        )
        self.assertEqual(
            state["display_status"],
            "Proyecto validado / identidad validada / relación como director pendiente",
        )

    def test_exact_pending_names_are_suggested_without_merging_or_writing(self):
        second_job = ImportJob(
            source_type="PROGRESS_PDF",
            filename="second.pdf",
            status="SUCCESS",
            is_current=True,
        )
        self.db.add(second_job)
        self.db.flush()
        first = self._role(
            raw_name="Anibal Quintanilla Bonilla",
            canonical_key="pending:anibal-one",
            canonical_name="Anibal Quintanilla Bonilla",
        )
        second = self._role(
            raw_name="Anibal Quintanilla Bonilla",
            canonical_key="pending:anibal-two",
            canonical_name="Anibal Quintanilla Bonilla",
            job=second_job,
        )
        self.db.flush()
        before = {
            first.id: (first.canonical_identity_key, first.validation_status),
            second.id: (second.canonical_identity_key, second.validation_status),
        }

        participants = ValidatedReadService(self.db).canonical_participants(period_id=self.period.id)

        matches = [item for item in participants if item["canonical_name"] == "Anibal Quintanilla Bonilla"]
        self.assertEqual(len(matches), 2)
        self.assertEqual({item["canonical_identity_key"] for item in matches}, {"pending:anibal-one", "pending:anibal-two"})
        self.assertTrue(all(item["possible_match_notice"] == "Posible coincidencia con otro registro" for item in matches))
        self.assertTrue(all(len(item["possible_matches"]) == 1 for item in matches))
        self.assertEqual(
            {item["possible_matches"][0]["document"] for item in matches},
            {"canonical.pdf", "second.pdf"},
        )
        self.assertEqual(
            before,
            {
                first.id: (first.canonical_identity_key, first.validation_status),
                second.id: (second.canonical_identity_key, second.validation_status),
            },
        )

    def test_partial_or_single_surname_overlap_is_not_suggested(self):
        self._role(
            raw_name="Ana Paredes Molina",
            canonical_key="pending:ana-paredes",
            canonical_name="Ana Paredes Molina",
        )
        self._role(
            raw_name="Luis Paredes Vera",
            canonical_key="pending:luis-paredes",
            canonical_name="Luis Paredes Vera",
        )
        self.db.flush()

        participants = ValidatedReadService(self.db).canonical_participants(period_id=self.period.id)

        paredes = [item for item in participants if "Paredes" in item["canonical_name"]]
        self.assertEqual(len(paredes), 2)
        self.assertTrue(all(item["possible_matches"] == [] for item in paredes))
        self.assertTrue(all(item["possible_match_notice"] is None for item in paredes))


@unittest.skipUnless(
    os.getenv("CANONICAL_OPERATIONAL_TEST_DATABASE_URL"),
    "requiere CANONICAL_OPERATIONAL_TEST_DATABASE_URL",
)
class CanonicalOperationalPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg_engine = create_engine(os.environ["CANONICAL_OPERATIONAL_TEST_DATABASE_URL"], pool_pre_ping=True)

    @classmethod
    def tearDownClass(cls):
        cls.pg_engine.dispose()

    def setUp(self):
        self.pg = Session(self.pg_engine)

    def tearDown(self):
        self.pg.rollback()
        self.pg.close()

    def test_b2a_named_identity_and_authorship_contract(self):
        service = ValidatedReadService(self.pg)
        participants = {item["canonical_name"]: item for item in service.canonical_participants()}

        self.assertEqual(participants["Carlos Parrales Choez"]["authorship_count"], 1)
        self.assertEqual(participants["Maria Del Carmen Valls Martinez"]["authorship_count"], 3)
        self.assertEqual(participants["Maria Estefania Sanchez Pacheco"]["authorship_count"], 2)
        self.assertEqual(participants["Jose Manuel Santos Jaen"]["authorship_count"], 0)
        self.assertEqual(participants["Fernando Jose Zambrano Farias"]["authorship_count"], 7)
        self.assertEqual(
            sum(1 for item in participants.values() if "Zambrano" in item["canonical_name"]),
            1,
        )
        self.assertEqual(
            sum(1 for item in participants.values() if "Sanchez Pacheco" in item["canonical_name"]),
            1,
        )
        self.assertEqual(
            sum(1 for item in participants.values() if "Merchan" in item["canonical_name"]),
            1,
        )
        self.assertEqual(
            {item["canonical_name"] for item in participants.values() if "Delgado Litard" in item["canonical_name"]},
            {"Delgado Litard", "Delgado Litardo"},
        )
        self.assertTrue(
            all(
                item["overall_status"] == "pending_review"
                for item in participants.values()
                if "Ramirez" in item["canonical_name"] or item["canonical_name"] == "Manosalvas Le"
            )
        )

    def test_b2a_operational_counts_and_kpi_contract(self):
        service = ValidatedReadService(self.pg)
        participants = service.canonical_participants()
        metrics = service.canonical_participant_metrics()
        products = service.production_views(visibility="all")

        self.assertEqual(len(participants), 49)
        self.assertEqual(metrics["participations"], 78)
        self.assertEqual(metrics["roles"], 120)
        self.assertEqual(metrics["authorships"], 38)
        self.assertEqual(metrics["external_detected"], 5)
        self.assertEqual(metrics["external_kpi_eligible"], 1)
        self.assertEqual(metrics["external_pending"], 4)
        self.assertEqual(sum(1 for item in products if item["visibility"] == "eligible"), 2)
        self.assertEqual(sum(1 for item in products if item["visibility"] == "pending"), 17)
        self.assertFalse(
            any(
                item["canonical_identity_key"].startswith("pending:") and item["kpi_eligible"]
                for item in participants
            )
        )
        population = self.pg.execute(
            text(
                """
                WITH current_product_evidence AS (
                    SELECT production.id AS production_id
                    FROM scientific_productions production
                    LEFT JOIN import_jobs direct_job ON direct_job.id = production.import_job_id
                    LEFT JOIN import_normalization_audits audit
                      ON audit.normalized_record_id = production.id
                     AND audit.entity_type = 'scientific_production'
                     AND audit.source_section = 'produccion_cientifica'
                    LEFT JOIN import_jobs audit_job
                      ON audit_job.id = audit.import_job_id
                     AND audit_job.status = 'SUCCESS'
                     AND audit_job.is_current IS TRUE
                    GROUP BY production.id
                    HAVING production.import_job_id IS NULL
                        OR max(CASE WHEN direct_job.is_current IS TRUE AND direct_job.status = 'SUCCESS' THEN 1 ELSE 0 END) = 1
                        OR count(audit_job.id) > 0
                ), operational_rows AS (
                    SELECT role.canonical_identity_key
                    FROM person_roles role
                    JOIN import_jobs job ON job.id = role.import_job_id
                    WHERE job.status = 'SUCCESS' AND job.is_current IS TRUE
                    UNION ALL
                    SELECT author.canonical_identity_key
                    FROM scientific_production_authors author
                    JOIN current_product_evidence evidence ON evidence.production_id = author.production_id
                )
                SELECT count(*) AS total_rows,
                       count(DISTINCT canonical_identity_key) AS identities,
                       count(*) FILTER (WHERE canonical_identity_key LIKE 'pending:%') AS pending_rows
                FROM operational_rows
                """
            )
        ).mappings().one()
        self.assertEqual(population["total_rows"], 163)
        self.assertEqual(population["identities"], 54)
        self.assertEqual(population["pending_rows"], 53)

    def test_b2a1_real_projects_and_exact_pending_name_suggestions(self):
        service = ValidatedReadService(self.pg)
        projects = service.entity_views()
        participants = service.canonical_participants()

        self.assertEqual(len(projects), 7)
        self.assertTrue(
            all(project["director_relationship_validation_status"] == "pending_review" for project in projects)
        )
        validated_identity_projects = [
            project
            for project in projects
            if project["director_identity_validation_status"] == "validated"
        ]
        self.assertEqual({project["id"] for project in validated_identity_projects}, {128, 129, 130})
        self.assertTrue(
            all(
                project["director_display_status"]
                == "Identidad validada / relación como director pendiente"
                for project in validated_identity_projects
            )
        )

        for canonical_name in (
            "Anibal Quintanilla Bonilla",
            "Jeniffer Marcillo Chasy",
            "Martha Rodriguez Carlos Apolinario",
        ):
            matches = [item for item in participants if item["canonical_name"] == canonical_name]
            self.assertEqual(len(matches), 2)
            self.assertEqual(len({item["canonical_identity_key"] for item in matches}), 2)
            self.assertTrue(all(item["possible_match_notice"] for item in matches))
            self.assertTrue(all(len(item["possible_matches"]) == 1 for item in matches))


if __name__ == "__main__":
    unittest.main()
