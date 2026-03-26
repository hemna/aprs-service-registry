# API Rate Limiting Design Spec

**Date:** 2026-03-26
**Status:** Approved

## Overview

Add rate limiting to all `/api/v1/*` endpoints to prevent abuse and hammering. Clients are identified by IP address and limited to 60 requests per minute. Exceeding the limit returns HTTP 429 with a `Retry-After` header.

## Goals

- Prevent bad actors from overwhelming the API with requests
- Simple, uniform rate limit across all API endpoints
- Minimal code changes and maintenance burden
- Standard HTTP response (429) with proper headers

## Non-Goals

- Different limits for read vs write operations
- API key authentication
- Redis/distributed storage (single container deployment)
- Rate limiting HTML pages or static assets

## Approach

Use **SlowAPI**, the de-facto standard rate-limiting library for FastAPI. It's built on top of the `limits` library and provides:

- Decorator-based rate limiting
- Multiple storage backends (using in-memory for simplicity)
- Proper HTTP 429 responses with `Retry-After` header
- Easy integration with FastAPI middleware

### Alternatives Considered

1. **Custom middleware** - More code, reinventing the wheel, missing standard features
2. **Nginx rate limiting** - Requires proxy configuration outside the app, harder to customize

## Technical Design

### New Dependency

Add to `pyproject.toml`:

```toml
dependencies = [
    ...
    "slowapi",
]
```

### Limiter Setup

In `main.py`, add the limiter configuration:

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

### Rate Limit Application

Apply `@limiter.limit("60/minute")` decorator to each API endpoint:

| Endpoint | Method | Rate Limited |
|----------|--------|--------------|
| `/api/v1/registry` | POST | Yes |
| `/api/v1/registry` | GET | Yes |
| `/api/v1/registry/{callsign}` | GET | Yes |
| `/api/v1/registry/{callsign}` | DELETE | Yes |
| `/api/v1/health-check/{callsign}` | POST | Yes |
| `/api/v1/health-check` | POST | Yes |

### Not Rate Limited

- HTML pages (`/`, `/services`, `/about`, `/guide`, `/developers`, `/service-types`, `/faq`)
- Static files (`/static/*`)
- WebSocket endpoint (`/ws`)
- OpenAPI docs (`/docs`, `/redoc`)

### Error Response

When rate limit is exceeded:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 42
Content-Type: text/plain

Rate limit exceeded: 60 per 1 minute
```

The `Retry-After` header indicates seconds until the client can retry.

## Documentation Updates

Update `/developers` page (`developers.html`):

**Before:**
> **No rate limits** are currently enforced.

**After:**
> Rate limit: **60 requests per minute** per IP address. Exceeding this returns HTTP 429 with a `Retry-After` header.

Keep the "be reasonable" guidance about caching and polling frequency.

## Testing

### Manual Testing

```bash
# Should succeed (within limit)
for i in {1..60}; do curl -s -o /dev/null -w "%{http_code}\n" https://aprs.hemna.com/api/v1/registry; done

# 61st request should return 429
curl -i https://aprs.hemna.com/api/v1/registry
```

### Behavior Notes

- In-memory storage resets on server restart (acceptable)
- Rate limits are per-IP, so users behind NAT share a limit
- Limits reset after 1 minute window

## Implementation Checklist

1. [ ] Add `slowapi` to `pyproject.toml` dependencies
2. [ ] Import and configure limiter in `main.py`
3. [ ] Add exception handler for `RateLimitExceeded`
4. [ ] Add `@limiter.limit("60/minute")` to all 6 API endpoints
5. [ ] Update `/developers` documentation page
6. [ ] Test locally
7. [ ] Deploy to production

## Rollback Plan

If issues arise, remove the `@limiter.limit()` decorators and the limiter setup code. The dependency can remain installed without being used.
