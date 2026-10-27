"""Conservative serial capacity overflow between equivalent Groq rungs."""


def select_overflow(router, provider, queue, tokens_est):
    """Prefer the ordinary rung on ties; probes never acquire or book."""
    if provider.name not in {"groq", "groq2"} or provider.schema_retried:
        return provider
    if provider.spend is not None and not provider.spend.has_capacity():
        return provider

    def delay(p):
        return max(router._pacer.delay(p.name, p.rpm),
                   router._token_pacer.delay(p.name, p.tpm, tokens_est))

    preferred_delay = delay(provider)
    if preferred_delay <= 0:
        return provider
    now = router._clock()
    for alternative in queue:
        if ({provider.name, alternative.name} == {"groq", "groq2"}
                and alternative.adapter.model == provider.adapter.model
                and not router._breaker.is_open(alternative.name)
                and not router._cooldowns.is_cooling(alternative.name, now)
                and (alternative.spend is None or alternative.spend.has_capacity())
                and delay(alternative) < preferred_delay):
            queue.remove(alternative)
            queue.appendleft(provider)
            return alternative
    return provider
