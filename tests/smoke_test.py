"""
tests/smoke_test.py
────────────────────
One-shot smoke test to verify all infrastructure components are reachable.
Run AFTER docker compose up -d and db.init_db.

Usage:
    poetry run python tests/smoke_test.py

What it checks:
  - PostgreSQL: can connect and query users table
  - Redis: can ping
  - MinIO: bucket exists
  - Qdrant: collection exists
  - API: /health endpoint reachable
  - Auth: file.admin login returns a token
"""

import asyncio
import os
import sys
import httpx
import structlog

log = structlog.get_logger()


def ok(msg): print(f"  \033[92m[OK] {msg}\033[0m")
def fail(msg): print(f"  \033[91m[FAIL] {msg}\033[0m"); return False
def section(msg): print(f"\n\033[94m{'-'*50}\n  {msg}\n{'-'*50}\033[0m")


async def check_postgres():
    section("PostgreSQL")
    try:
        import asyncpg
        from config.settings import get_settings
        s = get_settings()
        conn = await asyncpg.connect(
            host=s.postgres_host, port=s.postgres_port,
            user=s.postgres_user, password=s.postgres_password,
            database=s.postgres_db
        )
        rows = await conn.fetch("SELECT COUNT(*) FROM users")
        user_count = rows[0][0]
        await conn.close()
        ok(f"Connected. Users in DB: {user_count}")
        if user_count == 0:
            fail("No users found. Did you run: python -m db.init_db ?")
            return False
        return True
    except Exception as e:
        return fail(f"PostgreSQL failed: {e}")


async def check_redis():
    section("Redis")
    try:
        import redis.asyncio as aioredis
        from config.settings import get_settings
        s = get_settings()
        r = aioredis.from_url(s.redis_url, decode_responses=True)
        result = await r.ping()
        await r.aclose()
        if result:
            ok("Ping successful")
            return True
        return fail("Ping returned False")
    except Exception as e:
        return fail(f"Redis failed: {e}")


def check_minio():
    section("MinIO")
    try:
        from services.minio_service import ensure_bucket_exists, _get_client
        from config.settings import get_settings
        s = get_settings()
        client = _get_client()
        ensure_bucket_exists(s.minio_bucket_documents)
        buckets = [b.name for b in client.list_buckets()]
        if s.minio_bucket_documents in buckets:
            ok(f"Bucket '{s.minio_bucket_documents}' exists")
            return True
        return fail(f"Bucket '{s.minio_bucket_documents}' not found")
    except Exception as e:
        return fail(f"MinIO failed: {e}")


def check_qdrant():
    section("Qdrant")
    try:
        from services.qdrant_service import ensure_collection_exists, _get_client
        from config.settings import get_settings
        s = get_settings()
        ensure_collection_exists()
        client = _get_client()
        info = client.get_collection(s.qdrant_collection_name)
        vectors_count = getattr(info, "vectors_count", None) or getattr(info, "indexed_vectors_count", 0)
        ok(f"Collection '{s.qdrant_collection_name}' ready. Vectors: {vectors_count}")
        return True
    except Exception as e:
        return fail(f"Qdrant failed: {e}")


def check_api():
    section("FastAPI Gateway")
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get("http://localhost:8000/health")
            if r.status_code == 200:
                ok(f"Health check passed: {r.json()}")
                return True
            return fail(f"Health check returned {r.status_code}: {r.text}")
    except Exception as e:
        return fail(f"API not reachable: {e}. Is the server running? (uvicorn gateway.main:app)")


def check_auth():
    section("Authentication")
    password = os.getenv("SPARKLINE_ADMIN_PASSWORD")
    if not password:
        return fail(
            "SPARKLINE_ADMIN_PASSWORD is not set. Export the file.admin password "
            "to run this check; it is deliberately not stored in the repository."
        )
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(
                "http://localhost:8000/auth/login",
                json={"username": "file.admin", "password": password}
            )
            if r.status_code == 200:
                token = r.json().get("access_token")
                ok(f"Login successful. Token (first 30 chars): {token[:30]}...")
                return True
            return fail(f"Login failed {r.status_code}: {r.text}")
    except Exception as e:
        return fail(f"Auth check failed: {e}")


def check_pilot_user_login():
    """
    Check that the chat front end can obtain a session for a pilot user.

    Uses the service token, which is how the Open WebUI pipeline authenticates.
    It previously logged in as a named pilot user with a shared password — that
    breaks whenever the roster changes or someone changes their own password,
    and it put a credential in this file.
    """
    section("Pilot User Login")

    from config.settings import get_settings

    user = os.getenv("SPARKLINE_CHECK_USER", "sidharth.vijayan")
    service_token = get_settings().service_token
    if not service_token:
        return fail("SERVICE_TOKEN is not set — the chat pipeline cannot authenticate anyone")

    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(
                "http://localhost:8000/auth/service-token",
                headers={"X-Service-Token": service_token},
                json={"username": user},
            )
            if r.status_code == 200:
                ok(f"front end can open a session for '{user}'")
                return True
            return fail(f"Service-token auth failed {r.status_code}: {r.text}")
    except Exception as e:
        return fail(f"Pilot user login check failed: {e}")


async def main():
    print("\n\033[1m[TEST] Sparkline System Smoke Test\033[0m")
    results = []

    # Infrastructure (direct connections — no API needed)
    results.append(await check_postgres())
    results.append(await check_redis())
    results.append(check_minio())
    results.append(check_qdrant())

    # API checks (need uvicorn running)
    results.append(check_api())
    results.append(check_auth())
    results.append(check_pilot_user_login())

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n\033[1m{'-'*50}\nResult: {passed}/{total} checks passed\033[0m")

    if passed == total:
        print("\033[92m[SUCCESS] All systems go! Ready for document ingestion.\033[0m\n")
        sys.exit(0)
    else:
        print(f"\033[91m[WARNING] {total - passed} check(s) failed. Fix them before proceeding.\033[0m\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
