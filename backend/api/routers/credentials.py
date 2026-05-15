"""Store / preview Binance API keys per bot (dashboard)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.credential_resolver import get_binance_keys, mask_binance_api_key_preview
from backend.api.dependencies import get_db_session
from backend.api import futures_account_sync
from backend.database.models.exchange_credential import ExchangeCredential

router = APIRouter()
_log = logging.getLogger(__name__)


class CredentialPayload(BaseModel):
    binanceApiKey: str = Field(min_length=1)
    binanceApiSecret: str = Field(min_length=1)
    binanceTestnet: bool = True


@router.get("/{bot_id}/credentials")
async def get_credentials_preview(bot_id: str, db: AsyncSession = Depends(get_db_session)) -> dict:
    row = await db.scalar(select(ExchangeCredential).where(ExchangeCredential.bot_id == bot_id))
    db_active = bool(row and row.api_key.strip() and row.api_secret.strip())
    k1, k2, paper, _ = await get_binance_keys(bot_id)
    if not (k1 and k2):
        return {
            "hasKeys": False,
            "binanceApiKeyPreview": "",
            "binanceTestnet": True,
            "credentialSource": "none",
        }
    preview = mask_binance_api_key_preview(k1)
    if db_active:
        return {
            "hasKeys": True,
            "binanceApiKeyPreview": preview,
            "binanceTestnet": bool(row.testnet),
            "credentialSource": "database",
        }
    return {
        "hasKeys": True,
        "binanceApiKeyPreview": preview,
        "binanceTestnet": bool(paper),
        "credentialSource": "env",
    }


@router.post("/{bot_id}/credentials")
async def save_credentials(
    bot_id: str,
    body: CredentialPayload,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await db.scalar(select(ExchangeCredential).where(ExchangeCredential.bot_id == bot_id))
    now = datetime.now(timezone.utc)
    if row is None:
        row = ExchangeCredential(
            bot_id=bot_id,
            api_key=body.binanceApiKey.strip(),
            api_secret=body.binanceApiSecret.strip(),
            testnet=body.binanceTestnet,
            updated_at=now,
        )
        db.add(row)
    else:
        row.api_key = body.binanceApiKey.strip()
        row.api_secret = body.binanceApiSecret.strip()
        row.testnet = body.binanceTestnet
        row.updated_at = now
    await db.commit()
    futures_account_sync.reset_sync_dedupe()
    await futures_account_sync.sync_futures_account_to_hub_once()
    _log.info("credentials updated bot_id=%s testnet=%s", bot_id, body.binanceTestnet)
    return {
        "ok": True,
        "hasKeys": True,
        "binanceApiKeyPreview": mask_binance_api_key_preview(body.binanceApiKey),
        "binanceTestnet": body.binanceTestnet,
        "credentialSource": "database",
    }


@router.delete("/{bot_id}/credentials")
async def delete_credentials(bot_id: str, db: AsyncSession = Depends(get_db_session)) -> dict:
    await db.execute(delete(ExchangeCredential).where(ExchangeCredential.bot_id == bot_id))
    await db.commit()
    futures_account_sync.reset_sync_dedupe()
    return {"ok": True, "hasKeys": False, "credentialSource": "none"}
