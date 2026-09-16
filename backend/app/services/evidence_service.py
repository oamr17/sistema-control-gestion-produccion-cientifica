from io import BytesIO
from pathlib import PurePosixPath
import re
import unicodedata
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException, UploadFile, status
from minio import Minio

from app.core.config import settings

_EVIDENCE_OBJECT_NAMESPACE = UUID("be6b1d5f-6d1b-4ca4-929c-4d70af83e7b2")


def sanitize_pdf_filename(value: str | None) -> str:
    basename = PurePosixPath((value or "").replace("\\", "/")).name
    normalized = unicodedata.normalize("NFKD", basename).encode(
        "ascii", "ignore"
    ).decode("ascii")
    sanitized = re.sub(r"[^A-Za-z0-9._ -]+", "_", normalized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" ._-")
    if not sanitized or sanitized in {".", ".."}:
        return "evidence.pdf"
    stem = sanitized[:-4] if sanitized.casefold().endswith(".pdf") else sanitized
    stem = stem[:251].rstrip(" ._-")
    return f"{stem}.pdf" if stem else "evidence.pdf"


class EvidenceService:
    def __init__(self):
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )

    @staticmethod
    def object_name_for_public_reference(public_reference: str) -> str:
        prefix = "evidence:"
        if not public_reference.startswith(prefix):
            raise ValueError("Referencia de evidencia inválida.")
        public_id = UUID(public_reference.removeprefix(prefix))
        return f"{uuid5(_EVIDENCE_OBJECT_NAMESPACE, str(public_id))}.pdf"

    def upload_pdf(self, file: UploadFile) -> str:
        raw_filename = file.filename or ""
        extension = PurePosixPath(raw_filename.replace("\\", "/")).suffix.casefold()
        allowed_extensions = {item.casefold() for item in settings.evidence_allowed_extensions}
        if extension not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Solo se permiten archivos PDF.",
            )

        content_type = (file.content_type or "").split(";", 1)[0].strip().casefold()
        allowed_mime_types = {item.casefold() for item in settings.evidence_allowed_mime_types}
        if content_type not in allowed_mime_types:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="El tipo de archivo no corresponde a un PDF permitido.",
            )

        content = file.file.read(settings.evidence_max_bytes + 1)
        if len(content) > settings.evidence_max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="El archivo supera el tamaño máximo permitido.",
            )

        if not self.client.bucket_exists(settings.minio_bucket):
            self.client.make_bucket(settings.minio_bucket)

        public_reference = f"evidence:{uuid4()}"
        object_name = self.object_name_for_public_reference(public_reference)
        self.client.put_object(
            settings.minio_bucket,
            object_name,
            BytesIO(content),
            length=len(content),
            part_size=10 * 1024 * 1024,
            content_type="application/pdf",
            metadata={"public-filename": sanitize_pdf_filename(raw_filename)},
        )
        return public_reference
