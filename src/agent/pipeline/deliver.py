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
from agent.pipeline.deescalation import mark_notice_sent, record_escalation_day


class DeliverStage:
    name = "deliver"

    def __init__(self, env: Mapping[str, str], logger: logging.Logger) -> None:
        self._env = env
        self._logger = logger

    def run(self, ctx) -> None:
        messages = list(getattr(ctx, "messages", None) or [])
        ctx.counters.setdefault("deliver", 0)
        ctx.delivery_states = {k: "not_attempted" for k in getattr(ctx, "compose_kept_keys", [])}
        ctx.delivered_keys = []
        ctx.delivery_page_states = ["not_attempted"] * len(messages)
        if not messages:
            self._logger.info("deliver: nothing to send")
            return
        if getattr(ctx, "dry_run", False):
            ctx.delivery_states = dict.fromkeys(ctx.delivery_states, "dry_run")
            ctx.delivery_page_states = ["dry_run"] * len(messages)
            for text in messages:
                self._logger.info("deliver: dry-run, would have sent:\n%s", text)
            return
        client = self._client()
        if client is None:
            return
        results = []
        pages = getattr(ctx, "message_event_keys", None)
        for index, text in enumerate(messages):
            result = self._send(ctx, client, text, is_lead=False)
            results.append(result)
            state = ("mocked" if result.mocked else "acknowledged" if result.ok
                     else "failed_or_unknown")
            ctx.delivery_page_states[index] = state
            keys = pages[index] if pages is not None and index < len(pages) else []
            self._record_page(ctx, keys, state)
        self._mark_if_all_real(ctx, results)

        lead_text = getattr(ctx, "lead_message", None)
        if lead_text:
            if getattr(ctx, "leads_channel_id", None):
                self._send(ctx, client, lead_text, is_lead=True)
            else:
                self._logger.info(
                    "deliver: lead events stored but not sent (no leads channel configured)"
                )

    def _record_page(self, ctx, keys, state) -> None:
        """Persist each real receipt before attempting the next page."""
        if state == "acknowledged" and keys:
            if getattr(ctx, "db", None) is not None:
                mark_delivered(ctx.db, keys, ctx.now)
            ctx.delivered_keys.extend(k for k in keys if k not in ctx.delivered_keys)
        ctx.delivery_states.update(dict.fromkeys(keys, state))

    def _mark_if_all_real(self, ctx, results) -> None:
        """Keep notice/escalation whole-batch semantics.

        Older callers without page metadata retain the conservative whole-batch
        receipt gate. Production compose supplies exact per-page metadata.
        """
        if not results:
            return
        if any(getattr(r, "mocked", False) or not getattr(r, "ok", False)
               for r in results):
            return
        if getattr(ctx, "message_event_keys", None) is None:
            self._record_page(ctx, getattr(ctx, "compose_kept_keys", []) or [], "acknowledged")
        # De-escalation state (9z), same all-real gate as the markers above:
        # record today as an escalation-delivered day and stamp the notice's
        # cooldown clock only after every real send succeeded.
        if getattr(ctx, "escalation_delivered", False) and getattr(ctx, "db", None) is not None:
            record_escalation_day(ctx.db, ctx.now, ctx.config.deescalation)
        if getattr(ctx, "deescalation_notice", None) and getattr(ctx, "db", None) is not None:
            mark_notice_sent(ctx.db, ctx.now)

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
