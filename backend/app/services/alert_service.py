import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.kpi_service import KpiService


class AlertService:
    def __init__(self, db: Session):
        self.db = db

    async def send_period_alerts(self, year_label: str, cycle: int) -> dict[str, int | str]:
        dashboard = KpiService(self.db).dashboard(year_label, cycle)
        if not settings.n8n_webhook_url:
            return {"status": "skipped", "alerts": len(dashboard.alerts)}

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                settings.n8n_webhook_url,
                json={
                    "year_label": year_label,
                    "cycle": cycle,
                    "alerts": dashboard.alerts,
                    "careers": [item.model_dump() for item in dashboard.careers],
                },
            )
        return {"status": "sent", "alerts": len(dashboard.alerts)}
