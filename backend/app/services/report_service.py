from io import BytesIO

from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.services.kpi_service import KpiService


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    def excel(self, year_label: str, cycle: int) -> BytesIO:
        dashboard = KpiService(self.db).dashboard(year_label, cycle)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "KPI"
        sheet.append(
            [
                "Career",
                "Teachers",
                "Research teachers %",
                "Projects",
                "Scientific output",
                "Planned output",
                "POA compliance %",
                "Cycle variation %",
            ]
        )
        for career in dashboard.careers:
            sheet.append(
                [
                    career.career_name,
                    career.total_teachers,
                    career.teachers_in_research_percent,
                    career.projects,
                    career.scientific_output_total,
                    career.planned_output,
                    career.output_compliance_percent,
                    career.output_variation_percent,
                ]
            )
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)
        return buffer

    def pdf(self, year_label: str, cycle: int) -> BytesIO:
        dashboard = KpiService(self.db).dashboard(year_label, cycle)
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        y = height - 50
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(40, y, f"Scientific Production Report {year_label} Cycle {cycle}")
        y -= 35
        pdf.setFont("Helvetica", 10)
        pdf.drawString(40, y, f"Total teachers: {dashboard.total_teachers}")
        y -= 18
        pdf.drawString(40, y, f"Research teachers: {dashboard.teachers_in_research_percent}%")
        y -= 18
        pdf.drawString(40, y, f"Scientific output: {dashboard.scientific_output_total}")
        y -= 18
        pdf.drawString(40, y, f"Projects: {dashboard.projects_total}")
        y -= 30

        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(40, y, "Career")
        pdf.drawString(230, y, "Output")
        pdf.drawString(300, y, "POA %")
        pdf.drawString(370, y, "Variation %")
        y -= 18
        pdf.setFont("Helvetica", 9)
        for career in dashboard.careers:
            if y < 60:
                pdf.showPage()
                y = height - 50
            pdf.drawString(40, y, career.career_name[:32])
            pdf.drawString(230, y, str(career.scientific_output_total))
            pdf.drawString(300, y, str(career.output_compliance_percent))
            pdf.drawString(370, y, str(career.output_variation_percent))
            y -= 16

        pdf.save()
        buffer.seek(0)
        return buffer
