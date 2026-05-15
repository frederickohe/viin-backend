from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.socialmedia.model.DigitalMarketingPostAsset import DigitalMarketingPostAsset


class DigitalMarketingAssetService:
    def __init__(self, db: Session):
        self.db = db

    def create_from_postiz(
        self,
        *,
        user_internal_id: str,
        agent_name: str,
        marketing_text: str,
        content_links: List[str],
        postiz_response: Optional[Dict[str, Any]],
    ) -> DigitalMarketingPostAsset:
        row = DigitalMarketingPostAsset(
            id=f"dma_{uuid.uuid4().hex[:22]}",
            user_id=user_internal_id,
            agent_name=agent_name,
            marketing_text=marketing_text or None,
            content_links=list(content_links or []),
            postiz_response=postiz_response,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def count_for_user(self, user_internal_id: str) -> int:
        return (
            self.db.query(DigitalMarketingPostAsset)
            .filter(DigitalMarketingPostAsset.user_id == user_internal_id)
            .count()
        )

    def list_for_user(
        self, user_internal_id: str, *, limit: int = 20, offset: int = 0
    ) -> List[DigitalMarketingPostAsset]:
        lim = max(1, min(100, limit))
        off = max(0, offset)
        return (
            self.db.query(DigitalMarketingPostAsset)
            .filter(DigitalMarketingPostAsset.user_id == user_internal_id)
            .order_by(DigitalMarketingPostAsset.created_at.desc())
            .offset(off)
            .limit(lim)
            .all()
        )

    def get_for_user(
        self, user_internal_id: str, asset_id: str
    ) -> Optional[DigitalMarketingPostAsset]:
        return (
            self.db.query(DigitalMarketingPostAsset)
            .filter(
                DigitalMarketingPostAsset.user_id == user_internal_id,
                DigitalMarketingPostAsset.id == asset_id,
            )
            .first()
        )
