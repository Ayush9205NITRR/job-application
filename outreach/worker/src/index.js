// Tracking Worker: open pixel, tracked resume link, events API. Cloudflare Workers + D1 (free tier is enough).
// Routes:
//   GET /p/<track_id>.gif   -> logs an "open", returns a 1x1 transparent gif
//   GET /r/<track_id>       -> logs a "click", 302 to RESUME_URL
//   GET /api/events?since=<unix>  (Authorization: Bearer TRACK_TOKEN) -> {"events":[{fid,type,ts,ua,country}]}
const GIF = Uint8Array.from(
  atob("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"),
  (c) => c.charCodeAt(0)
);

async function log(env, fid, type, req) {
  const ua = (req.headers.get("user-agent") || "").slice(0, 200);
  const country = (req.cf && req.cf.country) || "";
  await env.DB.prepare("INSERT INTO events (fid, type, ts, ua, country) VALUES (?, ?, ?, ?, ?)")
    .bind(fid, type, Math.floor(Date.now() / 1000), ua, country)
    .run();
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);

    const p = url.pathname.match(/^\/p\/([a-z0-9]{6,32})\.gif$/i);
    if (p) {
      ctx.waitUntil(log(env, p[1], "open", req));
      return new Response(GIF, {
        headers: {
          "content-type": "image/gif",
          "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
        },
      });
    }

    const r = url.pathname.match(/^\/r\/([a-z0-9]{6,32})$/i);
    if (r) {
      ctx.waitUntil(log(env, r[1], "click", req));
      return Response.redirect(env.RESUME_URL, 302);
    }

    if (url.pathname === "/api/events") {
      if (req.headers.get("authorization") !== "Bearer " + env.TRACK_TOKEN) {
        return new Response("unauthorized", { status: 401 });
      }
      const since = parseInt(url.searchParams.get("since") || "0", 10) || 0;
      const { results } = await env.DB.prepare(
        "SELECT fid, type, ts, ua, country FROM events WHERE ts >= ? ORDER BY ts ASC LIMIT 5000"
      )
        .bind(since)
        .all();
      return Response.json({ events: results });
    }

    return new Response("not found", { status: 404 });
  },
};
