from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.interventions.model.Intervention import Intervention
from core.notification.service.event_notification_service import EventNotificationService


class InterventionService:
    def __init__(self, db: Session):
        self.db = db

    def create_intervention(
        self,
        *,
        user_id: str,
        trigger: str,
        reason: Optional[str] = None,
        conversation_date: Optional[date] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Intervention:
        conv_date = conversation_date or date.today()

        # If there is already an open intervention for the day, just return it.
        existing = (
            self.db.query(Intervention)
            .filter(
                Intervention.user_id == user_id,
                Intervention.conversation_date == conv_date,
                Intervention.status == "open",
            )
            .order_by(Intervention.created_at.desc())
            .first()
        )
        if existing:
            return existing

        intervention = Intervention(
            user_id=user_id,
            conversation_date=conv_date,
            status="open",
            trigger=trigger,
            reason=reason,
            meta=metadata or {},
        )
        self.db.add(intervention)
        self.db.commit()
        self.db.refresh(intervention)

        try:
            EventNotificationService(self.db).notify_intervention_active(
                user_id=user_id,
                intervention_id=int(intervention.id),
                trigger=trigger,
                reason=reason,
                conversation_date=str(conv_date),
            )
        except Exception:
            pass

        return intervention

    def close_intervention(self, *, intervention_id: int, user_id: str) -> Intervention:
        intervention = (
            self.db.query(Intervention)
            .filter(Intervention.id == int(intervention_id), Intervention.user_id == user_id)
            .first()
        )
        if not intervention:
            raise ValueError("Intervention not found")

        intervention.status = "closed"
        intervention.closed_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(intervention)
        return intervention

    def list_interventions(
        self,
        *,
        user_id: str,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Intervention]:
        q = self.db.query(Intervention).filter(Intervention.user_id == user_id)
        if status:
            q = q.filter(Intervention.status == status)
        return q.order_by(Intervention.created_at.desc()).limit(int(limit)).all()

    def get_intervention(self, *, intervention_id: int, user_id: str) -> Optional[Intervention]:
        return (
            self.db.query(Intervention)
            .filter(Intervention.id == int(intervention_id), Intervention.user_id == user_id)
            .first()
        )

