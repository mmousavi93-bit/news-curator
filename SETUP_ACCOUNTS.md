# What you need to do — account setup

Written for someone who does not code. Nothing here requires a credit card. Total time is
about 30 minutes. Do these before Phase 1 starts, or in parallel with it.

Keep every value you collect in one temporary note. At the end you will paste them all into
GitHub in one sitting, then delete the note.

---

## 1. Google AI Studio — the main brain (5 min)

This provides text understanding, image reading (replacing OCR), and it is the reason the
project needs no OCR engine.

Go to **https://aistudio.google.com**, sign in with any Google account, click **Get API
key**, then **Create API key in new project**. Copy the key. It starts with `AIza`.

Save as: `GEMINI_API_KEY`

Free tier gives you 10 requests per minute and 1,500 per day. The system is designed to use
about 290 per day, so you have room. Note that Google may use free-tier prompts to improve
their models — we only ever send public news content, never anything personal.

## 2. Groq — the backup brain (3 min)

Used only when Gemini is rate-limited or down. Without it, a Gemini outage means a missed
briefing.

Go to **https://console.groq.com**, sign up with Google or GitHub, open **API Keys**,
**Create API Key**. Copy it immediately — Groq shows it exactly once. It starts with `gsk_`.

Save as: `GROQ_API_KEY`

## 3. OpenRouter — the emergency parachute (3 min, recommended)

Only fires if both Gemini and Groq are down at once. Its free tier allows just 50 requests a
day, so it cannot carry the system — it exists to prevent total silence. Recommended since
2026-08-30: both primaries died the same day, three runs in a row (Gemini 503s, Groq's
free-tier ceiling).

Go to **https://openrouter.ai**, sign up (email or Google), **Keys** → **Create Key**.
Do not add a payment method — free models need zero balance. The key starts with `sk-or-`.

Save as: `OPENROUTER_API_KEY`

The pipeline calls the free model named in `config/settings.yaml` (`providers.openrouter.model`,
currently `qwen/qwen3.6-plus:free`). OpenRouter's free roster rotates weekly and IDs rot — the
original placeholder 404'd on its first live use (2026-08-30). If a run logs a model error
from openrouter: **404** means the model was delisted — open **https://openrouter.ai/models?q=free**
and put any model marked FREE into that YAML line (`openrouter/free` is a self-healing alias that
routes to whatever is free). **402** means OpenRouter's balance policy is blocking zero-balance
keys — that one is not fixable in config, tell me.

If signup is blocked from your network, use the same access path you used for the Gemini and
Groq accounts.

## 3b. b.ai reseller gateway — the third rung (5 min)

Your own gateway (docs.b.ai). All listed models are free; the pipeline treats them as a
gift, not capacity to depend on (your words). Create the API key on the b.ai console.

Save as: `BAI_API_KEY`

The model line lives in `config/settings.yaml` (`providers.bai.model`, currently
`qwen3.8-flash`); the comment next to it lists the swap order (glm-5.3-flash, mimo-v2.5,
hy3, deepseek-v4-flash). gpt-5.2 is paid on this gateway and stays unused until spend caps
are wired — do not enable it without telling me.

## 4. Telegram bot (5 min)

Open Telegram and search for **@BotFather** — the one with the blue verified check.

Send `/newbot`. It asks for a display name (anything, e.g. `Intel Desk`) and then a username
which must end in `bot` (e.g. `my_intel_desk_bot`). BotFather replies with a token that looks
like `8123456789:AAH...`. That is your bot token — treat it like a password, anyone holding
it controls the bot.

Save as: `TELEGRAM_BOT_TOKEN`

While still in BotFather, send `/setprivacy`, choose your bot, choose **Disable**. This
matters later if you ever put the bot in a group.

**Now get your chat ID.** Find your new bot by its username, open it, and press **Start** —
this step is mandatory, a bot cannot message you until you message it first. Then send it any
message, like `hello`.

Next, open this URL in a browser, replacing the placeholder with your actual token:

```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

You will see a wall of text. Find `"chat":{"id":123456789`. That number is yours. If it is
negative, that is normal for groups.

Save as: `TELEGRAM_CHAT_ID`

If `getUpdates` returns an empty result, you did not press Start or did not send a message.
Do both and reload.

## 5. GitHub repository (5 min)

Go to **https://github.com/new**. Name it `news-curator`. Set it to **Public** — this is
deliberate: public repositories get unlimited free Actions minutes, while private ones get
2,000 a month which this system would exhaust in about three weeks. Your accumulated
intelligence stays private because the database is encrypted before it is ever stored.

Do not add a README, .gitignore or licence — the project supplies them.

Tell me the repository URL when it exists.

## 6. Encryption key (2 min)

This is what keeps your data private in a public repository. I will generate the keypair for
you during Phase 4 and give you two strings: a public key and a private key.

**The private key is the single most important thing in this project.** If you lose it, your
30-day memory is permanently unrecoverable — there is no reset, no support line, no backup
that isn't also encrypted with it. Store it in a password manager, and put a second copy
somewhere physically separate.

Save as: `AGE_PRIVATE_KEY` and `AGE_PUBLIC_KEY`

## 7. Load the secrets into GitHub (5 min)

In your repository: **Settings** → **Secrets and variables** → **Actions** → **New
repository secret**. Add each one, name exactly as written below, capitals and underscores
matter.

| Secret name | From step |
|---|---|
| `GEMINI_API_KEY` | 1 |
| `GROQ_API_KEY` | 2 |
| `OPENROUTER_API_KEY` | 3, recommended |
| `TELEGRAM_BOT_TOKEN` | 4 |
| `TELEGRAM_CHAT_ID` | 4 |
| `AGE_PRIVATE_KEY` | 6, later |
| `AGE_PUBLIC_KEY` | 6, later |

Once saved, GitHub will never show you a secret's value again. That is intentional. It also
means your temporary note is the only copy — move the important ones into a password manager
before deleting it.

Never paste a key into a chat window, a code file, an issue, or a commit. Only into the
Secrets page. If one ever leaks, revoke it at the provider and generate a new one; all of
these are free to regenerate.

---

## Checklist

- [ ] Gemini API key
- [ ] Groq API key
- [ ] OpenRouter API key (optional)
- [ ] Telegram bot created, privacy disabled
- [ ] Pressed Start and sent the bot a message
- [ ] Chat ID retrieved
- [ ] Public GitHub repo created, URL sent to me
- [ ] Secrets 1–5 loaded into GitHub
- [ ] Encryption keys generated and stored — Phase 4

## Things that commonly go wrong

`getUpdates` returns nothing → you did not press Start, or you already ran the pipeline once
and consumed the update queue. Send a fresh message and reload.

Groq key lost → it is shown only once. Delete it and create another; costs nothing.

Bot is silent after setup → almost always the chat ID, not the token. Confirm the number,
including any minus sign.

Workflow does not run on schedule → GitHub disables scheduled workflows after 60 days of
repository inactivity. The pipeline writes to the repo on every run so this should not
trigger, but if messages stop entirely, check the Actions tab for a "workflow disabled"
banner and click Enable.
