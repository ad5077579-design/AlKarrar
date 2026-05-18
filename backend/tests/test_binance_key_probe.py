"""Binance env auto-detection and resume credential guards."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.financial

from backend.api.grid_snapshot_store import (
    disable_stale_resume_snapshots,
    snapshot_matches_credentials,
    write_snapshot_payload,
)
from backend.core.binance_key_probe import (
    credentials_fingerprint,
    probe_binance_env,
    reset_binance_env_probe_cache,
    resolve_binance_env_for_keys,
)


def test_credentials_fingerprint_stable() -> None:
    a = credentials_fingerprint("key123", "secret456")
    b = credentials_fingerprint("key123", "secret456")
    c = credentials_fingerprint("other", "secret456")
    assert a == b
    assert a != c
    assert len(a) == 32


def test_snapshot_matches_credentials() -> None:
    fp = credentials_fingerprint("k", "s")
    ok = {
        "autoResume": True,
        "binanceEnv": "mainnet",
        "credentialsFingerprint": fp,
    }
    assert snapshot_matches_credentials(ok, binance_env="mainnet", credentials_fingerprint=fp)
    assert not snapshot_matches_credentials(ok, binance_env="demo", credentials_fingerprint=fp)
    assert not snapshot_matches_credentials(
        {"autoResume": True}, binance_env="mainnet", credentials_fingerprint=fp
    )


def test_probe_picks_demo_first() -> None:
    async def _run() -> None:
        reset_binance_env_probe_cache()
        mock_client = AsyncMock()
        mock_client.fetch_account = AsyncMock(return_value={"balances": []})
        mock_client.aclose = AsyncMock()

        with patch(
            "backend.core.binance_key_probe.BinanceSpotClient.create_for_env",
            new_callable=AsyncMock,
            return_value=mock_client,
        ) as create_mock:
            env = await probe_binance_env("k", "s", hint=None)
            assert env == "demo"
            create_mock.assert_called_once()
            assert create_mock.call_args.kwargs["env"] == "demo"

    asyncio.run(_run())


def test_probe_falls_through_to_mainnet() -> None:
    async def _run() -> None:
        reset_binance_env_probe_cache()

        class WrongEnvError(Exception):
            code = -2015

        async def _create(*_a, env: str, **_k):
            c = AsyncMock()
            if env == "demo":

                async def _fail() -> dict:
                    raise WrongEnvError("Invalid API-key")

                c.fetch_account = _fail
            else:
                c.fetch_account = AsyncMock(return_value={"balances": []})
            c.aclose = AsyncMock()
            return c

        with patch(
            "backend.core.binance_key_probe.BinanceSpotClient.create_for_env",
            side_effect=_create,
        ):
            env = await probe_binance_env("k", "s", hint="demo")
            assert env == "mainnet"

    asyncio.run(_run())


def test_resolve_respects_disable_auto_detect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALKARRAR_AUTO_DETECT_BINANCE_ENV", "0")

    async def _run() -> None:
        reset_binance_env_probe_cache()
        with patch(
            "backend.core.binance_key_probe.probe_binance_env",
            new_callable=AsyncMock,
        ) as probe:
            env = await resolve_binance_env_for_keys("k", "s", hint="mainnet")
            assert env == "mainnet"
            probe.assert_not_called()

    asyncio.run(_run())


def test_disable_stale_resume_snapshots(tmp_path) -> None:
    async def _run() -> None:
        db = tmp_path / "trader.db"
        fp_demo = credentials_fingerprint("demo_k", "demo_s")
        fp_main = credentials_fingerprint("main_k", "main_s")
        await write_snapshot_payload(
            "default",
            "DOGEUSDT",
            {
                "autoResume": True,
                "binanceEnv": "demo",
                "credentialsFingerprint": fp_demo,
                "generatorUpper": 1.0,
                "generatorLower": 0.9,
                "generatorCount": 5,
            },
            path=db,
        )
        await write_snapshot_payload(
            "default",
            "BTCUSDT",
            {
                "autoResume": True,
                "binanceEnv": "mainnet",
                "credentialsFingerprint": fp_main,
                "generatorUpper": 1.0,
                "generatorLower": 0.9,
                "generatorCount": 5,
            },
            path=db,
        )
        disabled = await disable_stale_resume_snapshots(
            "default",
            binance_env="mainnet",
            credentials_fingerprint=fp_main,
            path=db,
        )
        assert disabled == ["DOGEUSDT"]

    asyncio.run(_run())
