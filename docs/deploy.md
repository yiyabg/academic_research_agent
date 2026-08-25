# Deployment

This project was generated with the following deployment-related flags:

- ✅ Docker / `docker-compose.yml`
- ❌ No Kubernetes manifests
- CI: `github`
- Reverse proxy: Nginx


---


## Docker Compose (single host)

For staging:

```bash
# 1. Configure
cp backend/.env.example backend/.env
# Edit backend/.env with production values (see ENV_VARS.md)

# 2. Build + start
docker compose up -d --build

# 3. Apply migrations
docker compose exec app uv run alembic upgrade head


# 4. Verify
curl http://localhost:8000/api/v1/health
# Frontend: http://localhost:3000
```

For production, use the standalone research topology rather than layering the
development files:

```bash
cp backend/.env.example backend/.env
# Replace every change-me value, set the real TLS DOMAIN, and configure any
# licensed data/LLM credentials available to this deployment.
make prod
```

`make prod` first runs a fail-closed topology/secrets verifier, then starts the
API with multiple Uvicorn workers, three Celery research workers serving four
resource queues (`research-io`, `research-cpu`, `research-llm`, `paper-analysis`),
Celery Beat, PostgreSQL, authenticated Redis, Qdrant, MinIO, GROBID, ClamAV,
Flower and Next.js. Migrations, bucket creation and model preloading are
one-shot startup gates. PostgreSQL, Redis, Qdrant, MinIO, GROBID and ClamAV do
not publish host ports. The backend network still permits outbound HTTPS for
scholarly sources and the configured LLM provider.

Production also requires `PROMETHEUS_AUTH_TOKEN`. The API master clears and
recreates a container-local `PROMETHEUS_MULTIPROC_DIR` before spawning Uvicorn
workers, so a scrape aggregates every worker rather than whichever process
accepted the connection. Configure the same Bearer token in the internal
Prometheus scrape job; unauthenticated `/metrics` requests return 401.

The generative provider is selected per project in `backend/.env`; it does not
need Codex's global `~/.codex/config.toml` or `~/.codex/auth.json`:

```dotenv
# Official OpenAI (requires an OpenAI Platform API key)
LLM_PROVIDER=openai
AI_MODEL=gpt-5.5
OPENAI_API_KEY=...
LLM_BASE_URL=

# DeepSeek (requires a separate DeepSeek API key)
LLM_PROVIDER=deepseek
AI_MODEL=deepseek-v4-pro
DEEPSEEK_API_KEY=...
LLM_BASE_URL=

# A project-only Responses-compatible gateway
LLM_PROVIDER=openai_compatible
AI_MODEL=gpt-5.5
OPENAI_API_KEY=...
LLM_BASE_URL=https://gateway.example/v1
```

Only configure one provider at a time. A ChatGPT/Codex subscription and OpenAI
Platform API billing are separate, and a gateway key is not an official OpenAI
or DeepSeek key. Either provider credential may be left empty when it is not
selected; a search-only deployment may leave the selected credential empty,
in which case readiness reports `full_research=false` rather than fabricating
an analysis result.

Research retrieval embeddings are selected independently. Keep them local when
using a Responses-only gateway:

```dotenv
RESEARCH_EMBEDDING_PROVIDER=local
RESEARCH_LOCAL_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

Do not infer embedding access from a successful Responses request. To set
`RESEARCH_EMBEDDING_PROVIDER=openai`, the configured key must be an official
OpenAI Platform key with access to `EMBEDDING_MODEL`; a third-party gateway key
works only if that gateway explicitly exposes a compatible `/embeddings` API.

When Docker must use a host HTTPS proxy, configure `RUNTIME_HTTPS_PROXY` with an
address reachable from bridge containers (for example `host.docker.internal`).
On Linux, a proxy bound only to `127.0.0.1` is not reachable through the Docker
gateway; either bind it to a restricted gateway/LAN address or use a dedicated
authenticated container-reachable proxy. Keep internal service names in
`RUNTIME_NO_PROXY`. Verify credential, network and model access without exposing
the key:

```bash
docker compose exec research-worker-llm \
  python /app/scripts/verify_llm_connectivity.py
```

If host DNS intentionally maps only the selected gateway to a sink address,
set `LLM_GATEWAY_HOST` and a verified `LLM_GATEWAY_HOST_IP`. Compose writes that
mapping only into this project's app/research containers; it does not modify
host DNS or Codex configuration. Revalidate the IP when the gateway operator
changes infrastructure.


### Serving from a host that isn't localhost

The defaults assume the browser runs on the Docker host. Opening the app from
another machine (a LAN IP, a staging box, a tunnel) needs two things set, and
both fail in a way that looks like something else:

```bash
# In the .env next to docker-compose.prod.yml
PUBLIC_HOST=10.0.0.5     # an address the BROWSER can reach
COOKIE_SECURE=false      # ONLY if you serve over plain http:// (see below)
```

```bash
# NEXT_PUBLIC_* is inlined into the bundle, so this needs a real rebuild
docker compose -f docker-compose.prod.yml build --no-cache frontend
docker compose -f docker-compose.prod.yml up -d frontend
```

1. **`NEXT_PUBLIC_*` is baked in at build time.** Next.js inlines those values
   into the JavaScript the browser downloads, so a runtime env var cannot change
   them — `docker-compose.prod.yml` passes them as `build:` args instead. Miss
   this and the chat socket dials `ws://localhost:8000` from
   the visitor's own machine and the input stays disabled ("Offline").
2. **The auth cookies are `Secure` in production, and a browser silently drops a
   `Secure` cookie that arrives over `http://`.** Login looks like it worked (the
   user comes back in the response body) and then every request carrying no
   cookie answers 401, `/api/auth/me` included, so the token never refreshes.
   TLS is the real fix; `COOKIE_SECURE=false` is the escape hatch for a trusted
   network.

Reaching the backend matters too: the chat socket is opened by the browser
directly against `NEXT_PUBLIC_WS_URL`, so either publish the backend port or
route `/api/v1/ws` through your proxy with the `Upgrade` headers set.

### Reverse proxy
Nginx config in `nginx/nginx.conf` proxies `/` → frontend, `/` on `api.DOMAIN` → backend, and `/api/v1/ws` → backend WebSocket. Update `server_name` and the TLS cert paths there.




## Platform-specific quickstarts

### Fly.io

```bash
fly launch --name academic_research_agent-backend --region waw
fly postgres create --name academic_research_agent-db
fly postgres attach academic_research_agent-db
# Redis: use Upstash (`fly redis create`) or Fly's Tigris
fly secrets set $(cat backend/.env | grep -v '^#' | xargs)
fly deploy
```

### Railway

1. Connect repo, pick Dockerfile-based deploy.
2. Add env vars from `backend/.env` to Railway service.
3. Provision PostgreSQL plugin → `DATABASE_URL` auto-injected.
4. Provision Redis plugin → `REDIS_URL` auto-injected.
5. Deploy.

### Render

1. Create Web Service → docker, point at `backend/Dockerfile`.
2. Create Static Site for frontend (build cmd: `bun install && bun run build`, output dir: `.next`).
3. Create PostgreSQL → copy DATABASE_URL.
4. Add env vars; deploy.

### Vercel (frontend only)

The frontend is a Next.js app — works on Vercel out of the box.

```bash
cd frontend
vercel
```

Set `BACKEND_URL`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL` (`wss://…`) and
`NEXT_PUBLIC_SITE_URL` in the Vercel dashboard, pointing at your backend host,
then redeploy — the `NEXT_PUBLIC_*` ones are only picked up by a fresh build.


---

## Environment validation in production

Before promoting to prod, run:

```bash
python3 backend/scripts/verify_research_production_topology.py
docker compose --env-file backend/.env -f docker-compose.prod.yml run --rm config-check
```

Catches missing required env vars early. See `ENV_VARS.md` for the full list.

## Post-deploy checks

- [ ] `/api/v1/health` returns `{"status": "healthy", ...}`
- [ ] `/api/v1/health/ready` reports every infrastructure/worker check healthy
- [ ] `capabilities.full_research=true`, or the release is explicitly approved as search-only
- [ ] `alembic current` matches expected revision
- [ ] Frontend renders, login flow works end-to-end
- [ ] Logs flowing to your aggregator + Sentry capturing errors + Logfire receiving traces
- [ ] Reverse proxy enforces HTTPS

## Rollback

- **Schema:** `alembic downgrade -1` rolls back one migration. Test on staging first.
- **Code:** redeploy previous image tag. Pin tags (`v1.2.3`), never deploy `latest` to prod.
- **Data:** restore from your most recent backup; verify `alembic current` matches the data version.
