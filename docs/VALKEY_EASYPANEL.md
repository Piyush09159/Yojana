# Yojana Saathi Valkey (Redis-compatible) private-cache deployment

## What this patch does
Caches ONLY the public `/api/catalog` and `/api/schemes/{scheme_id}` results.
Provides `public_key('region', state=..., district=...)`, `get_public`, `put_public`
for a later agent integration caching verified public Beed/Maharashtra scheme lookups.
It does NOT cache user profiles, eligibility results, phone numbers, WhatsApp identifiers,
conversation transcripts, private scheme recommendations or one-time links.
It does NOT install Celery or a queue. The existing PostgreSQL notification outbox
remains authoritative.

## Deploy Valkey on EasyPanel
1. Create a separate private service `yojana-cache` using the image `valkey/valkey:8.1.11-alpine`.
   Pin a reviewed immutable image digest at production deployment. Do not attach a public domain
   or publish port 6379 to the internet.
2. Set a randomly generated password using EasyPanel's secret/environment manager.
   Configure Valkey `requirepass`, `maxmemory 192mb` and `maxmemory-policy allkeys-lru`.
   Cache-only setup can disable persistence (`--save '' --appendonly no`), since a restart
   simply clears reusable public results. If you add durable jobs, use PostgreSQL or a
   separate persistent queue instead.
3. Get the actual private hostname from EasyPanel's connection/network screen; never guess it.
4. In the `yojana-api` service set:
   REDIS_URL=redis://:<URL_ENCODED_PASSWORD>@<ACTUAL_PRIVATE_HOST>:6379/0
   SCHEME_CACHE_TTL_SECONDS=300
   SCHEME_CACHE_VERSION=v1
   Do not put the password in GitHub or frontend environment variables.
5. Redeploy `yojana-api` after code merge. Call `/api/catalog` twice and a valid
   `/api/schemes/{id}` twice. In a private container, use `valkey-cli INFO stats`
   while authenticated to check cache hits/misses.
6. On each published verified scheme update bump `SCHEME_CACHE_VERSION` and redeploy;
   prior keys won't be reused. TTL bounds old information to at most 5 minutes otherwise.

## Local commands
py -3.13 -m pip install -r requirements-deploy.txt -r requirements-dev.txt
py -3.13 -m pytest tests/test_public_cache_patch.py -q
py -3.13 -m pytest -q

## Failure safety
Redis outage is treated as a cache miss, never a source of truth failure.
PostgreSQL remains mandatory for consent and notification delivery.
Do not use IP-only shared-NAT rate limiting without careful rollout and monitoring.
