# SESSION_10C_BRIEF — add 9 owner channels + 2 independence groups

## TASK
Add 9 Telegram channels (3 tier-3 signal sources, 6 lead crowd/early sources) and
2 new independence groups. Apply the EXACT entries below — do not rename, re-tier,
reorder, or invent anything not specified here.

## GATE (Hermes runs, not Codex)
1. `validate_join` passes — every `sources.yaml` id has a `credibility.yaml` key.
2. `coverage.check_coverage` returns ZERO warnings.
3. Full pytest green on Py3.12: `"C:/Users/CafeBazaar/AppData/Local/Programs/Python/Python312/python.exe" -m pytest -q`

## SCOPE (ONLY these two files)
- config/sources.yaml
- config/credibility.yaml

## CONSTRAINTS (project hard rules — Codex has not read CLAUDE.md)
- JOIN RULE: the `id` in sources.yaml MUST exactly match a key under
  `sources:` in credibility.yaml. A mismatch silently falls through to
  `defaults:` (tier 3, group "unlisted").
- tier-3 entries MUST carry a non-empty `signals_covered` list, else the
  coverage check warns "covers none". Lead entries carry `signals_covered: []`
  (exempt by decision 7).
- `group: null` = independent (own id). A NAMED group = shared origin = those
  sources cannot corroborate each other.
- Match existing formatting exactly (2-space indent, `enabled: true`, blank
  lines between entries).
- No other files. No code changes. Do NOT git commit or push (Hermes does).

## EXACT ENTRIES — config/sources.yaml (append at end of the `sources:` list)

  - id: tg_avatoday
    name: AVATODAY (javanmardi77)
    url: https://t.me/s/javanmardi77
    type: telegram
    lang: fa
    topic: military
    enabled: true
    signals_covered: [A3, A4, A5, A6, B3, B6, E1, E2, E3, E4, E5]

  - id: tg_iran_didehban
    name: Iran Didehban (ایران دیده‌بان)
    url: https://t.me/s/IRAN_DIDEHBAN
    type: telegram
    lang: fa
    topic: military
    enabled: true
    signals_covered: [A3, A4, A5, A6, B3, B6, E1, E2, E3, E4, E5]

  - id: tg_kaavoush
    name: Kaavoush (کاوش)
    url: https://t.me/s/kaavoush
    type: telegram
    lang: fa
    topic: military
    enabled: true
    signals_covered: [A3, A4, A5, A6, B3, B6, E1, E2, E3, E4, E5]

  - id: tg_mamlekate
    name: Mamlekate (مملکته)
    url: https://t.me/s/mamlekate
    type: telegram
    lang: fa
    topic: iran_domestic
    enabled: true
    signals_covered: []

  - id: tg_kh_b2
    name: KH_B2 (اخبارفوری جنگ امریکا و ایران)
    url: https://t.me/s/KH_B2
    type: telegram
    lang: fa
    topic: iran_domestic
    enabled: true
    signals_covered: []

  - id: tg_m0_hm
    name: M0_HM (اخبارفوری جنگ امریکا فوری)
    url: https://t.me/s/M0_HM
    type: telegram
    lang: fa
    topic: iran_domestic
    enabled: true
    signals_covered: []

  - id: tg_p_raetorio
    name: Praetorio (P_raetorio)
    url: https://t.me/s/P_raetorio
    type: telegram
    lang: fa
    topic: military
    enabled: true
    signals_covered: []

  - id: tg_mohem_mat
    name: Mohem Mat (مهمات)
    url: https://t.me/s/mohem_mat
    type: telegram
    lang: fa
    topic: iran_domestic
    enabled: true
    signals_covered: []

  - id: tg_vahid_oonline
    name: VahidOOnline (وحید اون‌لاین)
    url: https://t.me/s/VahidOOnLine
    type: telegram
    lang: fa
    topic: iran_domestic
    enabled: true
    signals_covered: []

## EXACT ENTRIES — config/credibility.yaml

Tier-3 block (append after the `tg_parvaz_capital_tel` entry):

  tg_avatoday:
    tier: 3
    group: null
    note: "Sourced regional news aggregator (Reuters-cited). Nominates, never corroborates."
  tg_iran_didehban:
    tier: 3
    group: null
    note: "Domestic military-activity watcher (fighter sorties, explosions, official denials)."
  tg_kaavoush:
    tier: 3
    group: null
    note: "Regional intel curator, ~35% forwarded. Original curation, partial relay."

Lead block (append after the `tg_irane_azad_news` entry):

  tg_mamlekate:
    tier: lead
    group: null
    note: "Crowd/eyewitness reports + official quotes. Surface-early only."
  tg_kh_b2:
    tier: lead
    group: iran_war_rumor
    note: "Iran war-rumor aggregator. Same genre as tg_m0_hm; grouped so they cannot corroborate each other."
  tg_m0_hm:
    tier: lead
    group: iran_war_rumor
    note: "Iran war-rumor aggregator. Same genre as tg_kh_b2; grouped."
  tg_p_raetorio:
    tier: lead
    group: null
    note: "Regional + regime-change commentary."
  tg_mohem_mat:
    tier: lead
    group: null
    note: "Mixed domestic/regional, partial Hebrew."
  tg_vahid_oonline:
    tier: lead
    group: vahid
    note: "Same operator as tg_vahid_online; grouped so they never corroborate each other."

MODIFY existing `tg_vahid_online` entry — change its `group: null` to `group: vahid`
(same operator as the new tg_vahid_oonline). Keep tier: lead and any note.

## REPORT (Codex must print)
1. Files changed + `git diff --stat`.
2. Output of a YAML sanity load of BOTH files (must parse clean).
3. Output of `validate_join` (must report no missing keys).
