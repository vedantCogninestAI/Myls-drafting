import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.ingestion import FormFields, IngestionFile


class IngestionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_case(self, process_type: str | None = None) -> Case:
        case = Case(process_type=process_type)
        self.session.add(case)
        await self.session.commit()
        await self.session.refresh(case)
        return case

    async def save_files(self, case_id: int, files: list[dict]) -> list[IngestionFile]:
        records = [IngestionFile(case_id=case_id, **data) for data in files]
        self.session.add_all(records)
        await self.session.commit()
        return records

    async def get_filed_docs(self, case_id: int) -> list[IngestionFile]:
        result = await self.session.execute(
            select(IngestionFile).where(
                IngestionFile.case_id == case_id,
                IngestionFile.doc_type == "filed_doc",
                IngestionFile.fields_extracted.is_(False),
            )
        )
        return list(result.scalars().all())

    async def save_form_fields(
        self, case_id: int, ingestion_file_id: uuid.UUID, fields: dict
    ) -> FormFields:
        record = FormFields(case_id=case_id, ingestion_file_id=ingestion_file_id, fields=fields)
        self.session.add(record)
        await self.session.execute(
            update(IngestionFile)
            .where(IngestionFile.id == ingestion_file_id)
            .values(fields_extracted=True)
        )
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get_case_files(self, case_id: int) -> list[IngestionFile]:
        result = await self.session.execute(
            select(IngestionFile).where(IngestionFile.case_id == case_id)
        )
        return list(result.scalars().all())

    async def get_case_form_fields(self, case_id: int) -> list[FormFields]:
        result = await self.session.execute(
            select(FormFields).where(FormFields.case_id == case_id)
        )
        return list(result.scalars().all())

    async def get_case(self, case_id: int) -> Case | None:
        return await self.session.get(Case, case_id)

    async def get_file_by_filename(self, case_id: int, filename: str) -> IngestionFile | None:
        result = await self.session.execute(
            select(IngestionFile).where(
                IngestionFile.case_id == case_id,
                IngestionFile.filename == filename,
            )
        )
        return result.scalars().first()
