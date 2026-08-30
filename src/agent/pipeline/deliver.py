"""The deliver stage: ctx.message goes to Telegram through the Phase 2
client, or is logged in dry-run. Delivery failure returns a SendResult --
it never crashes the run (Phase 2 contract). The `deliver` counter is 1
when a send (real or mocked) succeeded, 0 on failure or no-send, so the
summary line stays honest on every path.

Mock discipline: with no credentials, TelegramClient.from_env builds the
mock client (Phase 2 behaviour) -- this stage is fully exercisable in the
offline suite with no keys and no network.
"""

from __future__ import annotations

import logging
from typing import Mapping

from agent.delivery.credentials import TelegramConfigError
from agent.delivery.telegram import TelegramClient
from agent.memory.event_models import mark_delivered


class DeliverStage:
    name = "deliver"

    def __init__(self, env: Mapping[str, str], logger: logging.Logger) -> None:
        self._env = env
        self._logger = logger

    def run(self, ctx) -> None:
        messages = list(getattr(ctx, "messages", None) or [])
        ctx.counters.setdefault("deliver", 0)
        if not messages:
            self._logger.info("deliver: nothing to send")
            return
        if getattr(ctx, "dry_run", False):
            for text in messages:
                self._logger.info("deliver: dry-run, would have sent:\n%s", text)
            return
        client = self._client()
        if client is None:
            return
        results = []
        for text in messages:
            results.append(self._send(ctx, client, text, is_lead=False))
        self._mark_if_all_real(ctx, results)

        lead_text = getattr(ctx, "lead_message", None)
        if lead_text:
            if getattr(ctx, "leads_channel_id", None):
                self._send(ctx, client, lead_text, is_lead=True)
            else:
                self._logger.info(
                    "deliver: lead events stored but not sent (no leads channel configured)"
                )

    def _mark_if_all_real(self, ctx, results) -> None:
        """Write the received-markers (compose's kept keys) only when every
        main-feed send REALLY succeeded. Marking before send -- or on a
        mocked send -- would re-create the ghost suppression the delivered
        table exists to kill: send failure is non-fatal by contract, and
        the owner never saw these events."""
        if not results:
            return
        if any(getattr(r, "mocked", False) or not getattr(r, "ok", False)
               for r in results):
            return
        keys = getattr(ctx, "compose_kept_keys", None)
        if keys and getattr(ctx, "db", None) is not None:
            mark_delivered(ctx.db, keys, ctx.now)

    def _client(self):
        try:
            return TelegramClient.from_env(self._env)
        except TelegramConfigError as exc:
            self._logger.error("deliver: %s", exc)
            return None

    def _send(self, ctx, client, text: str, *, is_lead: bool):
        result = client.send(text)
        if result.ok:
            ctx.counters["deliver"] += 1
        if result.mocked:
            self._logger.info("deliver: mock mode (no credentials), nothing sent")
        elif result.ok:
            self._logger.info(
                "deliver: %s sent (%d chars, attempts=%d)",
                "lead message" if is_lead else "message", len(text), result.attempts,
            )
        else:
            self._logger.error(
                "deliver: send failed (status=%s, description=%s)",
                result.status_code, result.description,
            )
        return result
