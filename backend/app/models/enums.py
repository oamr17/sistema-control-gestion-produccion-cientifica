from enum import StrEnum


class UserRole(StrEnum):
    FACULTY_ADMIN = "FACULTY_ADMIN"
    CAREER_MANAGER = "CAREER_MANAGER"


class ProductionType(StrEnum):
    ARTICLE = "ARTICLE"
    BOOK = "BOOK"
    BOOK_CHAPTER = "BOOK_CHAPTER"
    PRESENTATION = "PRESENTATION"


class Quartile(StrEnum):
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"


class ProjectType(StrEnum):
    FCI = "FCI"
    SEEDBED = "SEEDBED"


class ProjectTeacherRole(StrEnum):
    RESEARCHER = "RESEARCHER"
    DIRECTOR = "DIRECTOR"


class GoalMetric(StrEnum):
    TEACHERS_IN_RESEARCH = "TEACHERS_IN_RESEARCH"
    PROJECTS = "PROJECTS"
    SCIENTIFIC_OUTPUT = "SCIENTIFIC_OUTPUT"
    ARTICLES = "ARTICLES"
    BOOKS = "BOOKS"
    BOOK_CHAPTERS = "BOOK_CHAPTERS"
    PRESENTATIONS = "PRESENTATIONS"
