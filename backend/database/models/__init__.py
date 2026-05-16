"""Import side-effect: registers all ORM models on ``Base.metadata``."""

from backend.database.models.bot_settings import BotSettings
from backend.database.models.bot_audit_log import BotAuditLog
from backend.database.models.exchange_credential import ExchangeCredential
from backend.database.models.order import Order
from backend.database.models.position import Position
from backend.database.models.trade_fill import TradeFill

__all__ = [
    "Order",
    "Position",
    "BotSettings",
    "BotAuditLog",
    "ExchangeCredential",
    "TradeFill",
]
