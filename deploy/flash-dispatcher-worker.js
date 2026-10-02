// Flash dispatcher — Cloudflare Worker that fires the flash-alert workflow
// every 15 minutes via workflow_dispatch, because GitHub throttles the
// `schedule:` trigger on public repos down to ~1 run/3h (measured 263 runs
// over 32 days vs the 96/day the */15 cron intends).
//
// Deploy (dashboard, no wrangler):
//   1. Workers & Pages -> Create Worker (or Edit an existing one) -> paste
//      this whole file -> Deploy.
//   2. Settings -> Variables and Secrets -> add secret:
//        name:  GITHUB_TOKEN
//        value: a fine-grained PAT with "Actions" = Read and write on
//               mmousavi93-bit/news-curator (never a user password).
//   3. Settings -> Triggers -> Add Cron Trigger:  */15 * * * *  -> Deploy
//      again (the trigger is its OWN deploy — see skill note).
//   4. KV binding (optional, for the /health heartbeat): Settings ->
//      Variables -> KV namespace bindings -> bind name DISPATCH_KV.
//
// Endpoints (all no-auth — they only POST a workflow dispatch, which is
// idempotent and already gated by the workflow's own concurrency group):
//   GET /health  -> last dispatch timestamp + HTTP status (null if never)
//   GET /run     -> dispatch now (manual re-fire / post-deploy test)

export default {
  async scheduled(_event, env) {
    await dispatch(env);
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      const last = env.DISPATCH_KV ? await env.DISPATCH_KV.get("last") : null;
      return new Response(last || "never", { status: last ? 200 : 404 });
    }
    if (url.pathname === "/run") {
      const r = await dispatch(env);
      return new Response(r, { status: r.startsWith("204") ? 200 : 502 });
    }
    return new Response("flash-dispatcher: /run to dispatch, /health for state");
  },
};

const REPO = "mmousavi93-bit/news-curator";
const WORKFLOW = "flash-alert.yml";

async function dispatch(env) {
  if (!env.GITHUB_TOKEN) {
    return "no GITHUB_TOKEN secret set";
  }
  const res = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "flash-dispatcher-worker",
      },
      body: JSON.stringify({ ref: "main" }),
    },
  );
  const now = new Date().toISOString();
  const line = `${now} -> HTTP ${res.status}`;
  if (env.DISPATCH_KV) {
    await env.DISPATCH_KV.put("last", line);
  }
  return line;
}
