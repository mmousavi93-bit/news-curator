# PHASE 1 — SKELETON

**Deliverable:** folders, config loader, logging, CI, dry-run harness.
**Gate:** tests green on an empty pipeline.

**Gate command (exact):**
```
env -i PATH=$PATH HOME=$HOME python -m pytest -q
env -i PATH=$PATH HOME=$HOME python -m agent.run --dry-run
```
**Pass condition:** `pytest` exits 0 with every test collected and none skipped for missing
keys. `agent.run --dry-run` exits 0 and emits exactly one summary log line reporting 0
items collected, 0 clusters, 0 messages sent. `env -i` is not decoration — it proves the
suite runs with no API keys present. A test that skips when a key is absent is a failed
gate.

Target Python 3.11 (GitHub Actions default, matches the runner).

---

## FILES

```
pyproject.toml                        deps + tool config; every dep gets a justification comment
.github/workflows/ci.yml              pytest on push and PR; no secrets referenced
.gitignore                            must exclude .env, *.db, *.age, __pycache__
src/agent/__init__.py                 version string only
src/agent/config.py                   YAML discovery, parse, merge, freeze, fail-fast validation
src/agent/settings.py                 typed frozen dataclasses mirroring settings.yaml
src/agent/run.py                      entrypoint; builds RunContext, sequences stages, summarises
src/agent/util/__init__.py
src/agent/util/logging.py             logger factory + value-based redaction filter
src/agent/pipeline/__init__.py        Stage protocol + the ordered stage list
src/agent/pipeline/_noop.py           no-op stage used until real stages land
tests/conftest.py                     network guard, tmp config fixture, env scrubber
tests/unit/test_config.py
tests/unit/test_logging_redaction.py
tests/unit/test_settings_schema.py
tests/integration/test_dry_run.py
tests/fixtures/settings_minimal.yaml
```

Create empty package dirs with `__init__.py` for `collectors/`, `memory/`, `risk/`,
`llm/`, `delivery/` so later phases have somewhere to land. Do not put code in them.

---

## SIGNATURES

**`src/agent/config.py`**
```python
class ConfigError(Exception): ...

def config_dir() -> Path:
    """Resolve config/ from AGENT_CONFIG_DIR env var, else repo root. No side effects."""

def load_yaml(name: str, *, base: Path | None = None) -> dict:
    """Parse one config file. Raises ConfigError on missing file or malformed YAML.
    Never returns None for an empty document — returns {}."""

def load_all(*, base: Path | None = None) -> "Config":
    """Load settings + credibility (+ risk_weights, sources when they exist),
    validate, freeze, return. Fail-fast: raise ConfigError listing EVERY problem
    found, not just the first."""
```

**`src/agent/settings.py`**
```python
@dataclass(frozen=True, slots=True)
class Settings:
    version: int
    schedule: ScheduleSettings
    collection: CollectionSettings
    pipeline: PipelineSettings
    retention: RetentionSettings
    scoring: ScoringSettings
    alerting: AlertingSettings
    markets: MarketsSettings
    llm: LlmSettings
    delivery: DeliverySettings
    ops: OpsSettings

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        """Strict. An unknown key is an error, not a warning — a typo'd threshold
        that silently defaults is the worst failure mode this project has."""

@dataclass(frozen=True, slots=True)
class Config:
    settings: Settings
    credibility: Mapping[str, "SourceCredibility"]
```

Mirror the ten top-level keys already present in `config/settings.yaml`
(`version, schedule, collection, pipeline, retention, scoring, alerting, markets, llm,
delivery, ops`). Do not invent keys and do not drop keys you do not yet use — Phase 1
validates the whole file even though it consumes almost none of it.

**`src/agent/util/logging.py`**
```python
class RedactionFilter(logging.Filter):
    """Redacts by VALUE, not by key name."""
    def register(self, secret: str) -> None: ...
    def filter(self, record: logging.LogRecord) -> bool: ...

def register_env_secrets(f: RedactionFilter, env: Mapping[str, str]) -> int:
    """Register the VALUE of every env var whose NAME matches
    (KEY|TOKEN|SECRET|PASSPHRASE|PASSWORD|CHAT_ID). Skip values under 8 chars —
    registering a 3-char value would redact half the log. Returns count registered."""

def get_logger(name: str) -> logging.Logger:
    """Returns a logger with the process-wide RedactionFilter attached.
    This is the ONLY sanctioned way to obtain a logger anywhere in the codebase."""
```

Redact by value and not by name. Name-based redaction only catches
`log.info("key=%s", os.environ["GEMINI_API_KEY"])`; value-based also catches the case
that actually happens — a secret embedded in a URL, a traceback, or a provider error
string echoed back verbatim. Apply the filter to the record's formatted message and to
`record.args`.

**`src/agent/pipeline/__init__.py`**
```python
class Stage(Protocol):
    name: str
    def run(self, ctx: "RunContext") -> None: ...

STAGES: tuple[str, ...] = (
    "collect", "filter", "vision", "embed", "cluster",
    "understand", "validate", "score", "compose", "deliver",
)
```

**`src/agent/run.py`**
```python
@dataclass
class RunContext:
    config: Config
    dry_run: bool
    now: datetime          # injected ONCE at startup, never re-read from the clock
    counters: dict[str, int]

def main(argv: Sequence[str] | None = None) -> int:
    """--dry-run, --config-dir, --log-level. Returns a process exit code.
    Executes STAGES in order, catching nothing — a stage that raises must fail the run
    loudly. Emits exactly one structured summary line at the end."""
```

`now` is injected once and threaded through `RunContext`. Nothing anywhere may call
`datetime.now()` directly. This is the seed of hard-constraint #3: once the risk engine
exists, a stray clock read makes scores irreproducible, and it is far cheaper to forbid it
in Phase 1 than to hunt it in Phase 8.

---

## TESTS

- `test_config.py` — missing file raises `ConfigError`; malformed YAML raises `ConfigError`;
  an empty document yields `{}` not `None`; **a config with three separate errors reports
  all three in one exception**, not just the first.
- `test_settings_schema.py` — the real `config/settings.yaml` in the repo loads clean;
  an unknown key raises; a missing required key raises; the returned object is immutable
  (assignment raises `FrozenInstanceError`).
- `test_logging_redaction.py` — a registered secret is absent from formatted output when
  passed as the message, as a `%s` arg, embedded mid-URL, and inside an exception
  traceback. A 4-char value is *not* registered. Assert on the final formatted string,
  never on the filter's internals.
- `test_dry_run.py` — `main(["--dry-run"])` returns 0 with all stages no-op, executes
  `STAGES` in declared order, and emits one summary line with zero counts. Run it twice
  with the same injected `now` and assert byte-identical log output.

**`tests/conftest.py` must install a network guard** that raises on any `socket.socket`
connect attempt for the whole suite, and scrub every `*_KEY`, `*_TOKEN`, `*_SECRET` from
`os.environ` before tests run. Mock mode is not a convention if nothing enforces it — this
fixture is what makes "runs offline with no keys" true in Phase 6 rather than aspirational.

---

## MOST LIKELY CONSTRAINT VIOLATION IN THIS PHASE

**#9, secret leakage — and it will happen by accident, not by carelessness.** The realistic
path is not someone logging a key deliberately. It is `logger.exception()` on a failed HTTP
call where the provider put the key in the query string, or a config-validation error that
helpfully prints the offending value. Both are invisible in review and permanent in a
public repo's fork network. Value-based redaction plus the traceback test above is the
defence. Also: `ConfigError` messages must name the *key path*, never echo the value.

Secondary risk: over-building. Phase 1 is scaffolding. A clever plugin registry or a
config-schema DSL here costs you the 200-line limit in Phase 5 and buys nothing.

---

## OUT OF SCOPE — do not write these in Phase 1

Collectors, HTTP client, SQLite, schema, encryption, dedup hashing, embeddings,
clustering, any LLM provider or router, Telegram client or formatter, risk scoring,
`config/sources.yaml`, `config/risk_weights.yaml`, `config/prompts/*`, the scheduled
workflows, and the state branch. Stages stay no-op. `run.py` sequences them and reports
zeros.

If a stage needs a dependency that does not exist yet, that is correct — it is a no-op.

---

## INPUTS THE IMPLEMENTER MAY READ

`CLAUDE.md`, the `## Implementation phases` table and the repo-layout tree
(§4) of `ARCHITECTURE.md`, `config/settings.yaml`, `config/credibility.yaml`, this brief.
Nothing else. Do not read the analysis documents — none of that lands until Phase 7.

---

## DEPENDENCIES ALLOWED IN THIS PHASE

`PyYAML` (config parsing) and `pytest` (tests). Nothing else. Any third dependency needs
Architect approval before it goes in `pyproject.toml`.
