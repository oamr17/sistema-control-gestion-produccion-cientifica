import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.pdf_parser import classify_document, parse_progress_report
from app.services.pdf_parser.pdf_text_extractor import extract_pdf_text
from app.services.pdf_parser.participants import build_participant_summary
from app.services.import_reconciliation import build_product_reconciliation_rows, build_research_entities
from tests.support.investigator_seed import (
    injected_participant_seed,
    synthetic_seed_record,
)


class PdfParserTest(unittest.TestCase):
    @unittest.skipUnless(
        (Path(__file__).parent / "fixtures" / "zambrano_fci021_2025.pdf").is_file(),
        "external historical PDF fixture is not part of the public prototype distribution",
    )
    def test_zambrano_real_pdf_matches_verified_scientific_production_fixture(self):
        pdf_path = Path(__file__).parent / "fixtures" / "zambrano_fci021_2025.pdf"
        extraction = extract_pdf_text(pdf_path.read_bytes())

        parsed = parse_progress_report(extraction.text)

        self.assertEqual(extraction.provider, "PyMuPDF")
        self.assertEqual(
            [
                {
                    "title": item["title"],
                    "authors": item["authors"],
                    "link": item["link"],
                }
                for item in parsed.produccion_cientifica
            ],
            [
                {
                    "title": "Modelo estructural de Innovación, Gestión del Conocimiento y Cadena de Suministros en la Nube en Pymes Portuarias Ecuatorianas",
                    "authors": ["Fernando Zambrano Farías"],
                    "link": "https://epsir.net/index.php/epsir/about",
                },
                {
                    "title": "Incidencia del capital de trabajo en la rentabilidad de las pymes en Ecuador",
                    "authors": [
                        "María Estefanía Sánchez Pacheco",
                        "María del Carmen Valls Martínez",
                        "Fernando Zambrano Farías",
                    ],
                    "link": "https://retos.ups.edu.ec/index.php/retos",
                },
                {
                    "title": "Modelo bancario ético frente a modelo bancario convencional: un estudio económico-financiero comparativo",
                    "authors": ["Fernando Zambrano Farías", "María del Carmen Valls Martínez"],
                    "link": "https://revistas.ucm.es/index.php/REVE",
                },
                {
                    "title": "Análisis económico financiero de las empresas de turismo en Ecuador: un análisis clustering",
                    "authors": [
                        "Jeniffer Marcillo Chasy",
                        "Fernando Zambrano Farías",
                        "María Estefanía Sánchez",
                    ],
                    "link": "https://revistas.uexternado.edu.co/index.php/tursoc",
                },
                {
                    "title": "Eficiencia Técnica de las Cooperativas de Crédito de Ecuador: antes y durante la pandemia",
                    "authors": [
                        "Carlos Parrales Choez",
                        "María del Carmen Valls Martínez",
                        "Fernando Zambrano Farías",
                    ],
                    "link": "https://revistas.unal.edu.co/index.php/innovar",
                },
            ],
        )
        self.assertEqual(
            parsed.research_entities[0]["title"],
            "VARIABLES EXPLICATIVAS DE LA RENTABILIDAD DE LAS MIPYMES EN ECUADOR",
        )
        participant_summary = build_participant_summary(parsed.to_public_dict())
        participants = {
            item["canonical_name"]: item
            for item in participant_summary["normalized_participants"]
        }
        self.assertIn("Jeniffer Marcillo Chasy", participants)
        self.assertEqual(
            participants["Jeniffer Marcillo Chasy"]["review_bucket"],
            "pending_author_classification",
        )
        self.assertFalse(participants["Jeniffer Marcillo Chasy"]["kpi_eligible"])
        self.assertNotIn("en las", {name.casefold() for name in participants})

    def test_internal_members_from_project_alias_and_multiline_name(self):
        text = """
        14. INVESTIGADORES QUE PARTICIPAN EN EL PROYECTO*
        NOMBRE COMPLETO
        FACULTAD
        CARRERA
        María Estefanía Sánchez
        Pacheco
        CIENCIAS
        ADMINISTRATIVAS
        Licenciatura en
        Contabilidad y Auditoría
        Septiembre 2023
        15. INVESTIGADORES QUE SE DESVINCULAN DEL PROYECTO
        Jeniffer Marcillo Chasy
        CIENCIAS ADMINISTRATIVAS
        Licenciatura en Finanzas
        """
        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.integrantes_internos), 1)
        self.assertEqual(parsed.integrantes_internos[0]["name"], "Maria Estefania Sanchez Pacheco")
        self.assertEqual(parsed.integrantes_internos[0]["career"], "Licenciatura en Contabilidad y Auditoria")

    def test_external_members_name_then_institution(self):
        text = """
        16. INVESTIGADORES EXTERNOS QUE PARTICIPAN DEL PROYECTO
        NOMBRE COMPLETO
        INSTITUCIÓN EXTERNA
        María del Carmen Valls Martínez
        Universidad de Almería
        José Manuel Santos Jaén
        Universidad de Murcia
        17. ESTUDIANTES QUE PARTICIPAN DEL PROYECTO
        """
        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.integrantes_externos), 2)
        self.assertEqual(parsed.integrantes_externos[0]["institution"], "Universidad de Almería")

    def test_scientific_products_are_unclassified_without_explicit_type(self):
        text = """
        21. PRODUCCIÓN CIENTÍFICA IMPACTO MUNDIAL Y REGIONAL
        TÍTULO
        AUTOR 1
        ESTADO
        IMPACTO
        LINK
        Determinants of profitability of credit unions in Ecuador
        Carlos Parrales Choez
        PUBLICADO
        IMPACTO MUNDIAL
        https://doi.org/10.123/example
        """
        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.produccion_cientifica), 1)
        self.assertEqual(parsed.produccion_cientifica[0]["type"], "UNCLASSIFIED")

    def test_scientific_product_keeps_source_evidence_impact_and_link(self):
        text = """
        3 DE 8
        21. PRODUCCION CIENTIFICA IMPACTO MUNDIAL Y REGIONAL
        TITULO
        AUTOR 1
        ESTADO
        IMPACTO
        LINK
        A robust framework for public sector innovation in Ecuador
        Fernando Zambrano Farias
        PUBLICADO
        IMPACTO MUNDIAL
        https://doi.org/10.123/example
        22. INTERCAMBIO DE CONOCIMIENTO
        """
        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.produccion_cientifica), 1)
        product = parsed.produccion_cientifica[0]
        self.assertEqual(product["impact"], "IMPACTO MUNDIAL")
        self.assertEqual(product["link"], "https://doi.org/10.123/example")
        self.assertEqual(product["source_section"], "produccion_cientifica")
        self.assertEqual(product["source_page"], 3)

    def test_scientific_author_is_production_role_not_person_type(self):
        text = """
        14. INVESTIGADORES QUE PARTICIPAN EN EL PROYECTO*
        NOMBRE COMPLETO
        FACULTAD
        CARRERA
        Fernando Zambrano Farias
        CIENCIAS ADMINISTRATIVAS
        Licenciatura en Administracion de Empresas
        Maria Parrales Choez
        CIENCIAS ADMINISTRATIVAS
        Licenciatura en Mercadotecnia
        21. PRODUCCION CIENTIFICA IMPACTO MUNDIAL Y REGIONAL
        TITULO
        AUTOR 1
        AUTOR 2
        ESTADO
        IMPACTO
        LINK
        Determinants of profitability of credit unions in Ecuador during 2025
        Fernando Zambrano Farias
        Alberto Jacint
        PUBLICADO
        IMPACTO MUNDIAL
        https://doi.org/10.123/example
        """
        with injected_participant_seed(
            synthetic_seed_record(
                "ZAMBRANO FARIAS FERNANDO JOSE",
                identity_number="SYN-FERNANDO",
            )
        ):
            parsed = parse_progress_report(text)
        participants = {item["canonical_name"]: item for item in parsed.normalized_participants}

        fernando = participants["Fernando Jose Zambrano Farias"]
        self.assertEqual(fernando["person_type"], "docente_interno")
        self.assertEqual(fernando["source_canonical_name"], "Fernando Zambrano Farias")
        self.assertIn("integrante_interno", fernando["institutional_roles"])
        self.assertIn("autor_producto", fernando["production_roles"])
        self.assertEqual(participants["Alberto Jacint"]["person_type"], "pendiente_clasificacion")
        self.assertIn("autor_producto", participants["Alberto Jacint"]["production_roles"])
        self.assertEqual(participants["Alberto Jacint"]["review_bucket"], "pending_author_classification")
        self.assertTrue(participants["Alberto Jacint"]["show_in_participants"])
        self.assertFalse(participants["Alberto Jacint"]["kpi_eligible"])
        self.assertNotIn("autor_producto", participants["Maria Parrales Choez"]["production_roles"])
        self.assertTrue(parsed.produccion_cientifica[0]["authorships"])

    def test_invalid_author_fragment_is_discarded_from_participants(self):
        payload = {
            "produccion_cientifica": [
                {
                    "type": "UNCLASSIFIED",
                    "title": "Determinants of profitability of credit unions in Ecuador during 2025",
                    "authors": [
                        "Claves Para El Crecimie Nto De La",
                        "Emprend Edor Ecuatori Ano",
                        "Alberto Jacint",
                    ],
                    "status": "PUBLICADO",
                    "impact": "IMPACTO MUNDIAL",
                    "link": None,
                    "source_section": "produccion_cientifica",
                }
            ]
        }
        summary = build_participant_summary(payload)

        self.assertEqual(len(summary["normalized_participants"]), 1)
        self.assertEqual(summary["normalized_participants"][0]["canonical_name"], "Alberto Jacint")
        self.assertEqual(summary["participants_summary"]["discarded_invalid"], 2)
        self.assertEqual(summary["participants_summary"]["invalid_text_fragments_count"], 2)
        discarded = [
            item for item in summary["participants_audit"] if item["review_bucket"] == "invalid_text_fragment"
        ]
        discarded_texts = {item["texto_original"] for item in discarded}
        self.assertEqual(
            discarded_texts,
            {"Claves Para El Crecimie Nto De La", "Emprend Edor Ecuatori Ano"},
        )
        self.assertTrue(all(not item["show_in_participants"] for item in discarded))
        self.assertTrue(all(not item["kpi_eligible"] for item in discarded))

    def test_label_value_author_rejects_title_fragment_before_accumulating(self):
        text = """
        21. PRODUCCION CIENTIFICA IMPACTO MUNDIAL Y REGIONAL
        TITULO
        Modelo de competitividad para cooperativas ecuatorianas
        AUTOR 1
        Rentabilidad Empresarial Regional
        ESTADO
        PUBLICADO
        """

        parsed = parse_progress_report(text)

        self.assertTrue(
            all(
                "Rentabilidad Empresarial Regional" not in product.get("authors", [])
                for product in parsed.produccion_cientifica
            )
        )
        self.assertNotIn(
            "Rentabilidad Empresarial Regional",
            {item["canonical_name"] for item in parsed.normalized_participants},
        )

    def test_scientific_title_continues_across_pages_without_new_section(self):
        text = """
        7 DE 8
        21. PRODUCCION CIENTIFICA IMPACTO MUNDIAL Y REGIONAL
        TITULO
        Analisis de la competitividad empresarial y su incidencia
        8 DE 8
        TITULO
        en la sostenibilidad de las pymes ecuatorianas
        AUTOR 1
        Fernando Zambrano Farias
        ESTADO
        PUBLICADO
        IMPACTO
        REGIONAL
        """

        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.produccion_cientifica), 1)
        self.assertEqual(
            parsed.produccion_cientifica[0]["title"],
            "Analisis de la competitividad empresarial y su incidencia en la sostenibilidad de las pymes ecuatorianas",
        )
        self.assertEqual(parsed.produccion_cientifica[0]["authors"], ["Fernando Zambrano Farias"])

    def test_scientific_title_continues_across_form_feed_page_break(self):
        text = """
        21. PRODUCCION CIENTIFICA IMPACTO MUNDIAL Y REGIONAL
        TITULO
        Analisis de la competitividad empresarial y su incidencia
        """ + "\f" + """
        TITULO
        en la sostenibilidad de las pymes ecuatorianas
        AUTOR 1
        Fernando Zambrano Farias
        ESTADO
        PUBLICADO
        IMPACTO
        REGIONAL
        """

        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.produccion_cientifica), 1)
        self.assertEqual(
            parsed.produccion_cientifica[0]["title"],
            "Analisis de la competitividad empresarial y su incidencia en la sostenibilidad de las pymes ecuatorianas",
        )
        self.assertEqual(parsed.produccion_cientifica[0]["authors"], ["Fernando Zambrano Farias"])

    def test_scientific_title_continues_across_extractor_page_marker(self):
        text = """
        21. PRODUCCION CIENTIFICA IMPACTO MUNDIAL Y REGIONAL
        TITULO
        Analisis economico financiero de las empresas de turismo
        [[PAGE_BREAK:8]]
        TITULO
        en Ecuador mediante un analisis clustering
        AUTOR 1
        Fernando Zambrano Farias
        ESTADO
        PUBLICADO
        IMPACTO
        MUNDIAL
        """

        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.produccion_cientifica), 1)
        self.assertEqual(
            parsed.produccion_cientifica[0]["title"],
            "Analisis economico financiero de las empresas de turismo en Ecuador mediante un analisis clustering",
        )

    def test_stacked_title_tail_after_page_break_extends_previous_row(self):
        text = """
        21. PRODUCCION CIENTIFICA IMPACTO MUNDIAL Y REGIONAL
        TITULO
        AUTOR 1
        ESTADO
        IMPACTO
        LINK
        Modelo bancario etico frente a modelo bancario convencional un estudio economico
        Fernando Zambrano Farias
        ENVIADO A REVISION
        IMPACTO MUNDIAL
        https://revistas.example/reve
        [[PAGE_BREAK:8]]
        financiero
        comparativo
        Analisis economico financiero de las empresas de turismo en Ecuador
        Jeniffer Marcillo Chasy
        Fernando Zambrano Farias
        ENVIADO A REVISION
        IMPACTO MUNDIAL
        https://revistas.example/turismo
        """

        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.produccion_cientifica), 2)
        self.assertEqual(
            parsed.produccion_cientifica[0]["title"],
            "Modelo bancario etico frente a modelo bancario convencional un estudio economico financiero comparativo",
        )
        self.assertEqual(
            parsed.produccion_cientifica[1]["title"],
            "Analisis economico financiero de las empresas de turismo en Ecuador",
        )

    def test_truncated_internal_name_is_pending_person_not_pending_author(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Paredes Herrer...",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                }
            ]
        }
        summary = build_participant_summary(payload)

        participant = summary["normalized_participants"][0]
        self.assertEqual(participant["person_type"], "docente_interno")
        self.assertEqual(participant["review_bucket"], "pending_person")
        self.assertFalse(participant["kpi_eligible"])
        self.assertEqual(summary["participants_summary"]["pending_people_count"], 1)
        self.assertEqual(summary["participants_summary"]["pending_author_classification_count"], 0)

    def test_context_sentence_is_discarded_from_participants(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Investigadora Karen Balladares Participaron En La Postulacion",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                }
            ]
        }
        summary = build_participant_summary(payload)

        self.assertEqual(summary["normalized_participants"], [])
        self.assertEqual(summary["participants_summary"]["invalid_text_fragments_count"], 1)
        discarded = summary["participants_audit"][0]
        self.assertEqual(discarded["review_bucket"], "invalid_text_fragment")
        self.assertFalse(discarded["show_in_participants"])
        self.assertFalse(discarded["kpi_eligible"])

    def test_objective_section_does_not_create_people(self):
        text = """
        10. OBJETIVOS DEL PROYECTO
        Fortalecer Carlos Parrales Choez mediante actividades de investigacion aplicada.
        11. RESULTADOS
        """
        parsed = parse_progress_report(text)

        self.assertEqual(parsed.normalized_participants, [])

    def test_product_like_text_outside_production_section_is_not_product(self):
        text = """
        10. ACTIVIDADES REALIZADAS
        TITULO
        AUTOR 1
        Determinants of profitability of credit unions in Ecuador
        Carlos Parrales Choez
        PUBLICADO
        """
        parsed = parse_progress_report(text)

        self.assertEqual(parsed.produccion_cientifica, [])

    def test_truncated_scientific_product_remains_pending_review(self):
        text = """
        21. PRODUCCION CIENTIFICA IMPACTO MUNDIAL Y REGIONAL
        TITULO
        AUTOR 1
        ESTADO
        IMPACTO
        LINK
        Producto incompleto de semillero sobre cultura tributaria
        """
        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.produccion_cientifica), 1)
        product = parsed.produccion_cientifica[0]
        self.assertEqual(product["validation_status"], "pending_review")
        self.assertFalse(product["kpi_eligible"])
        self.assertEqual(product["authors"], [])
        self.assertNotIn("title_recovery_status", product)

    def test_truncated_title_is_recovered_from_contiguous_pdf_lines(self):
        text = """
        5 DE 9
        21. PRODUCCION CIENTIFICA IMPACTO MUNDIAL Y REGIONAL
        TITULO
        AUTOR 1
        ESTADO
        IMPACTO
        LINK
        Impacto de la gestion de
        calidad en pymes ecuatorianas durante 2025
        Carlos Parrales Choez
        PUBLICADO
        IMPACTO MUNDIAL
        https://doi.org/10.123/recovered
        22. INTERCAMBIO DE CONOCIMIENTO
        """
        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.produccion_cientifica), 1)
        product = parsed.produccion_cientifica[0]
        self.assertEqual(
            product["title"],
            "Impacto de la gestion de calidad en pymes ecuatorianas durante 2025",
        )
        self.assertEqual(product["original_title"], "Impacto de la gestion de")
        self.assertEqual(
            product["source_fragment"],
            [
                "Impacto de la gestion de",
                "calidad en pymes ecuatorianas durante 2025",
            ],
        )
        self.assertEqual(product["title_recovery_status"], "recovered_from_pdf_context")
        self.assertEqual(product["source_page"], 5)
        self.assertEqual(product["validation_status"], "validado")
        self.assertTrue(product["kpi_eligible"])

    def test_tutor_student_table_title_is_not_counted_as_product(self):
        rows = build_product_reconciliation_rows(
            {
                "produccion_cientifica": [
                    {
                        "type": "UNCLASSIFIED",
                        "title": "TUTOR ESTUDIANTE 1 ESTUDIANTE 2",
                        "authors": [],
                        "status": None,
                        "impact": None,
                        "source_section": "produccion_cientifica",
                    }
                ]
            }
        )

        self.assertEqual(rows[0]["reconciliation_status"], "discarded_invalid")
        self.assertFalse(rows[0]["dashboard_counted"])

    def test_seedbed_and_group_are_research_entities(self):
        seedbed = build_research_entities({}, source_filename="Semillero S.I-010 Informe parcial.pdf")
        group = build_research_entities({}, source_filename="GI001-2024 GINFAES InfSem Junio2025.pdf")

        self.assertEqual(seedbed[0]["type"], "semillero")
        self.assertTrue(seedbed[0]["kpi_eligible"])
        self.assertEqual(group[0]["type"], "grupo_investigacion")
        self.assertTrue(group[0]["kpi_eligible"])

    def test_fci_filename_alone_does_not_create_project_entity(self):
        entities = build_research_entities(
            {},
            source_filename="FCI-001-2025 informe parcial.pdf",
            review_status="VALIDADO",
            confidence_score=0.9,
        )

        self.assertFalse(any(item.get("type") == "proyecto_fci" for item in entities))

    def test_project_entity_does_not_invent_missing_name_from_filename(self):
        entities = build_research_entities(
            {
                "proyectos_fci": [
                    {
                        "code": "FCI001-2025",
                        "name": "",
                        "director": "Fernando Zambrano Farias",
                        "progress": "45%",
                        "status": "Vigente",
                        "source_section": "proyectos_fci",
                        "source_page": 7,
                    }
                ]
            },
            source_filename="fallback name.pdf",
            review_status="VALIDADO",
        )

        self.assertEqual(len(entities), 1)
        self.assertIn(entities[0]["name"], (None, ""))
        self.assertEqual(entities[0]["source_page"], 7)

    def test_duplicate_evidence_does_not_duplicate_person(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Carlos Parrales Choez",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Mercadotecnia",
                },
                {
                    "name": "Carlos Parrales Choez",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Mercadotecnia",
                },
            ]
        }
        summary = build_participant_summary(payload)

        self.assertEqual(len(summary["normalized_participants"]), 1)
        self.assertEqual(summary["participants_summary"]["duplicate_evidence_count"], 1)

    def test_internal_fca_participant_scope_uses_faculty_evidence(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Dennise Quimi Franco",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                }
            ]
        }
        summary = build_participant_summary(payload)

        participant = summary["normalized_participants"][0]
        self.assertEqual(participant["person_type"], "docente_interno")
        self.assertEqual(participant["participant_scope"], "internal_fca")
        self.assertEqual(participant["detected_faculty"], "CIENCIAS ADMINISTRATIVAS")
        self.assertTrue(participant["kpi_eligible"])
        self.assertEqual(summary["participants_summary"]["internal_fca_count"], 1)

    def test_shared_surnames_with_different_first_names_are_not_pending_merge(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Dennise Quimi Franco",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                },
                {
                    "name": "Wendy Quimi Franco",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Contabilidad y Auditoria",
                },
            ]
        }
        summary = build_participant_summary(payload)

        participants = {item["canonical_name"]: item for item in summary["normalized_participants"]}
        self.assertIn("Dennise Quimi Franco", participants)
        self.assertIn("Wendy Quimi Franco", participants)
        self.assertFalse(summary["possible_merge_review"])
        self.assertEqual(summary["participants_summary"]["pending_merge_count"], 0)
        self.assertTrue(participants["Dennise Quimi Franco"]["kpi_eligible"])
        self.assertTrue(participants["Wendy Quimi Franco"]["kpi_eligible"])

    def test_same_given_names_and_one_surname_do_not_trigger_merge(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Ana Maria Paredes Ruiz",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                },
                {
                    "name": "Ana Maria Paredes Rios",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                },
            ]
        }

        summary = build_participant_summary(payload)

        self.assertEqual(len(summary["normalized_participants"]), 2)
        self.assertFalse(summary["possible_merge_review"])
        self.assertTrue(
            all(
                participant["validation_status"] == "validado"
                for participant in summary["normalized_participants"]
            )
        )
        self.assertEqual(summary["participants_summary"]["pending_merge_count"], 0)

    def test_internal_other_faculty_scope_keeps_internal_kpi(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Anibal Quintanilla Bonilla",
                    "faculty": "FILOSOFIA Y LETRAS DE LA EDUCACION",
                    "career": "Licenciatura en Pedagogia de Matematicas",
                }
            ]
        }
        summary = build_participant_summary(payload)

        participant = summary["normalized_participants"][0]
        self.assertEqual(participant["person_type"], "docente_interno")
        self.assertEqual(participant["participant_scope"], "internal_other_faculty")
        self.assertEqual(participant["detected_faculty"], "FILOSOFIA Y LETRAS DE LA EDUCACION")
        self.assertTrue(participant["kpi_eligible"])
        self.assertIn("otra facultad", participant["participant_scope_reason"])
        self.assertEqual(summary["participants_summary"]["internal_other_faculty_count"], 1)

    def test_aliases_merge_same_scientific_author_without_replacing_type(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Dolores Ortiz Guevara",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                }
            ],
            "produccion_cientifica": [
                {
                    "type": "UNCLASSIFIED",
                    "title": "Determinants of profitability of credit unions in Ecuador during 2025",
                    "authors": ["Ortiz Guevara Dolores", "Dolores Ortiz"],
                    "status": "PUBLICADO",
                    "impact": "IMPACTO MUNDIAL",
                    "link": None,
                    "source_section": "produccion_cientifica",
                }
            ],
        }
        with injected_participant_seed(
            synthetic_seed_record(
                "ORTIZ GUEVARA DOLORES DEL ROCIO",
                identity_number="SYN-DOLORES",
            )
        ):
            summary = build_participant_summary(payload)

        self.assertEqual(len(summary["normalized_participants"]), 1)
        participant = summary["normalized_participants"][0]
        self.assertEqual(participant["canonical_name"], "Dolores Del Rocio Ortiz Guevara")
        self.assertEqual(participant["source_canonical_name"], "Dolores Ortiz Guevara")
        self.assertEqual(participant["person_type"], "docente_interno")
        self.assertIn("autor_producto", participant["production_roles"])
        self.assertGreaterEqual(len(summary["person_aliases"]), 2)

    def test_seed_identity_resolution_merges_partial_internal_names(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Fernando Zambrano Farias",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                },
                {
                    "name": "Fernando Jose",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                },
            ]
        }
        with injected_participant_seed(
            synthetic_seed_record(
                "ZAMBRANO FARIAS FERNANDO JOSE",
                identity_number="SYN-FERNANDO",
            )
        ):
            summary = build_participant_summary(payload)

        self.assertEqual(len(summary["normalized_participants"]), 1)
        participant = summary["normalized_participants"][0]
        self.assertEqual(participant["canonical_name"], "Fernando Jose Zambrano Farias")
        self.assertEqual(participant["person_key"], "FERNANDO JOSE ZAMBRANO FARIAS")
        self.assertTrue(participant["kpi_eligible"])
        self.assertIn("Fernando Zambrano Farias", participant["aliases"])
        self.assertEqual(participant["identity_resolution"]["status"], "resolved")

    def test_high_confidence_seed_suggestion_normalizes_pdf_validated_internal_name(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Karen Balladares Ponguillo",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                }
            ]
        }
        with injected_participant_seed(
            synthetic_seed_record(
                "BALLADARES PONGUILLO KAREM ANDREA",
                identity_number="SYN-KAREM",
            )
        ):
            summary = build_participant_summary(payload)

        participant = summary["normalized_participants"][0]
        self.assertEqual(participant["canonical_name"], "Karem Andrea Balladares Ponguillo")
        self.assertEqual(participant["source_canonical_name"], "Karen Balladares Ponguillo")
        self.assertTrue(participant["kpi_eligible"])

    def test_unique_single_token_seed_match_completes_internal_participant(self):
        payload = {
            "integrantes_internos": [
                {
                    "name": "Montesdeoca",
                    "faculty": "CIENCIAS ADMINISTRATIVAS",
                    "career": "Licenciatura en Administracion de Empresas",
                }
            ]
        }
        with injected_participant_seed(
            synthetic_seed_record(
                "MONTESDEOCA PERALTA MARLENE DE JESUS",
                identity_number="SYN-MARLENE",
            )
        ):
            summary = build_participant_summary(payload)

        participant = summary["normalized_participants"][0]
        self.assertEqual(participant["canonical_name"], "Marlene De Jesus Montesdeoca Peralta")
        self.assertEqual(participant["source_canonical_name"], "Montesdeoca")
        self.assertEqual(participant["review_bucket"], "valid_person")
        self.assertTrue(participant["kpi_eligible"])

    def test_resolved_author_without_institutional_evidence_stays_non_kpi(self):
        payload = {
            "produccion_cientifica": [
                {
                    "type": "UNCLASSIFIED",
                    "title": "Articulo con autor pendiente",
                    "authors": ["Rafael Apolinario"],
                    "source_section": "produccion_cientifica",
                }
            ]
        }
        with injected_participant_seed(
            synthetic_seed_record(
                "APOLINARIO QUINTANA RAFAEL EMILIANO",
                identity_number="SYN-RAFAEL",
            )
        ):
            summary = build_participant_summary(payload)

        participant = summary["normalized_participants"][0]
        self.assertEqual(participant["canonical_name"], "Rafael Emiliano Apolinario Quintana")
        self.assertEqual(participant["review_bucket"], "pending_author_classification")
        self.assertFalse(participant["kpi_eligible"])

    def test_weak_seed_suggestion_does_not_normalize_unvalidated_author(self):
        payload = {
            "produccion_cientifica": [
                {
                    "type": "UNCLASSIFIED",
                    "title": "Articulo con autora pendiente",
                    "authors": ["Maria del Pilar Ortega"],
                    "source_section": "produccion_cientifica",
                }
            ]
        }
        summary = build_participant_summary(payload)

        participant = summary["normalized_participants"][0]
        self.assertEqual(participant["canonical_name"], "Maria Del Pilar Ortega")
        self.assertEqual(participant["review_bucket"], "pending_author_classification")
        self.assertFalse(participant["kpi_eligible"])

    def test_office_letter_is_ignored_before_parsing(self):
        parsed = classify_document(
            """
            OFICIO
            Solicitud de informacion administrativa para archivo interno.
            Atentamente,
            Decanato
            """,
            "oficio.pdf",
        )

        self.assertEqual(parsed.status, "ignored")
        self.assertEqual(parsed.document_type, "administrative_document")

    def test_progress_report_is_accepted_with_core_signals(self):
        parsed = classify_document(
            """
            INFORME PARCIAL DE GRUPOS DE INVESTIGACION
            DATOS GENERALES DE GRUPOS DE INVESTIGACION
            NOMBRE DEL GRUPO DE INVESTIGACION
            CICLO ACADEMICO QUE REPORTA
            INVESTIGADORES QUE PARTICIPAN EN EL GRUPO
            ACTIVIDADES REALIZADAS POR PROYECTOS FCI
            """,
            "GI001.pdf",
        )

        self.assertEqual(parsed.status, "accepted")
        self.assertEqual(parsed.document_type, "progress_report")

    def test_progress_report_variant_structure_is_accepted(self):
        parsed = classify_document(
            """
            FORMULARIO DE INFORME PARCIAL DE PROYECTOS DE INVESTIGACION
            DATOS GENERALES DEL PROYECTO DE INVESTIGACION
            CICLO ACADEMICO
            INVESTIGADORES QUE PARTICIPAN EN EL PROYECTO
            ACTIVIDADES REALIZADAS POR PROYECTOS FCI VINCULADOS A GRUPOS DE INVESTIGACION
            """,
            "GI001-2024 GINFAES InfSem Junio2025-signed.pdf",
        )

        self.assertEqual(parsed.status, "accepted")
        self.assertEqual(parsed.document_type, "progress_report")

    def test_typos_in_section_alias_still_parse_internal_members(self):
        text = """
        12. INVESTIGADORES QUE PARTICIPAN EN EL LUPO
        NOMBRE COMPLETO
        FACULTAD
        CARRERA
        Carlos Parrales Choez
        CIENCIAS ADMINISTRATIVAS
        Licenciatura en Administracion de Empresas
        13. INVESTIGADORES QUE SE DESVINCULAN DEL PROYECTO
        Grupo De Investigacion
        CIENCIAS ADMINISTRATIVAS
        Licenciatura en Administracion de Empresas
        """
        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.integrantes_internos), 1)
        self.assertEqual(parsed.integrantes_internos[0]["name"], "Carlos Parrales Choez")

    def test_outlook_capture_is_ignored_even_if_mentions_report_attachment(self):
        parsed = classify_document(
            """
            Outlook
            Bandeja de entrada
            Desde: coordinacion@example.edu
            Para: docente@example.edu
            CC: investigacion@example.edu
            Asunto: RE: informes semestrales de proyectos
            Enviado: jueves, 26 de junio de 2026
            1 archivo adjunto
            Formulario de informe parcial de proyectos.pdf
            https://outlook.cloud.microsoft/mail/id/AAQkExample
            Mensaje original
            Se remite formulario e informes semestrales para revision.
            """,
            "captura_outlook.pdf",
        )

        self.assertEqual(parsed.status, "ignored")
        self.assertEqual(parsed.document_type, "email_capture")
        self.assertIn("Outlook", parsed.warnings[0])

    def test_external_members_institution_then_name_real_layout(self):
        text = """
        13. INSTITUCIONES E INVESTIGADORES EXTERNOS PARTICIPANTES
        INSTITUCION EXTERNA
        INVESTIGADOR
        Universidad de Almeria
        Maria del Carmen Valls Martinez
        Universidad de Murcia
        Jose Manuel Santos Jaen
        14. RESUMEN DEL GRUPO DE INVESTIGACION EN INFORME SEMESTRAL
        """
        parsed = parse_progress_report(text)

        self.assertEqual(len(parsed.integrantes_externos), 2)
        self.assertEqual(parsed.integrantes_externos[0]["name"], "Maria Del Carmen Valls Martinez")
        self.assertEqual(parsed.integrantes_externos[0]["institution"], "Universidad de Almería")


if __name__ == "__main__":
    unittest.main()
