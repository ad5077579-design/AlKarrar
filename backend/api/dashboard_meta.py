"""Dashboard fields shared by REST + WebSocket (credentials, testnet flags)."""



from __future__ import annotations



from typing import Any



from backend.api.credential_resolver import (

    exchange_testnet_flag,

    get_binance_keys,

    mask_binance_api_key_preview,

)





async def apply_credentials_meta(bot_id: str, payload: dict[str, Any]) -> dict[str, Any]:

    k1, k2, env, _legacy = await get_binance_keys(bot_id)

    has = bool(k1 and k2)

    payload["credentialsConfigured"] = has

    payload["exchangeTestnet"] = exchange_testnet_flag(env) if has else False

    payload["binanceEnv"] = env if has else ""

    if has and k1:

        payload["binanceApiKeyPreview"] = mask_binance_api_key_preview(k1)

        payload["binanceTestnet"] = exchange_testnet_flag(env)

    else:

        payload.setdefault("binanceApiKeyPreview", "")

        payload.setdefault("binanceTestnet", True)

    return payload

