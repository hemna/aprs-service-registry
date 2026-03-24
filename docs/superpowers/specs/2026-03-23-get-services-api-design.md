# GET Services API Design

**Date:** 2026-03-23  
**Status:** Approved  
**Author:** Assistant

## Summary

Add two GET endpoints to the APRS Service Registry API for fetching registered services as JSON:
1. List all services with metadata
2. Get a single service by callsign

## Background

The existing API supports POST (register/update) and DELETE (remove) operations but lacks read endpoints. Users need a way to programmatically fetch service data without scraping the HTML UI.

## Design

### Endpoint 1: List All Services

**`GET /api/v1/registry`**

Returns all registered services with metadata.

#### Response (200 OK)

```json
{
  "count": 3,
  "timestamp": "2026-03-23T14:30:00Z",
  "services": [
    {
      "callsign": "SMSGTE",
      "description": "SMS Gateway Service",
      "service_website": "https://smsgte.example.com",
      "software": "aprsd 3.0",
      "callsign_owner": "N0CALL"
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `count` | integer | Total number of registered services |
| `timestamp` | string | ISO 8601 UTC timestamp of the response |
| `services` | array | Array of service objects (empty if none registered) |

### Endpoint 2: Get Single Service

**`GET /api/v1/registry/{callsign}`**

Returns a single service by callsign.

#### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `callsign` | string | The callsign to look up (case-insensitive) |

#### Response (200 OK)

```json
{
  "callsign": "SMSGTE",
  "description": "SMS Gateway Service",
  "service_website": "https://smsgte.example.com",
  "software": "aprsd 3.0",
  "callsign_owner": "N0CALL"
}
```

#### Response (404 Not Found)

```json
{
  "detail": "Service 'SMSGTE' not found"
}
```

### Behavior

- Callsign lookup is case-insensitive (converted to uppercase internally)
- List endpoint returns empty `services` array when no services are registered
- Single service endpoint returns 404 with error detail when callsign not found

## Implementation

### Approach

Add endpoints directly to `main.py` alongside existing POST and DELETE routes.

### Changes Required

**File: `aprs_service_registry/main.py`**

1. Add `datetime` import for timestamp generation
2. Add `HTTPException` to FastAPI imports for 404 handling
3. Add `GET /api/v1/registry` endpoint
4. Add `GET /api/v1/registry/{callsign}` endpoint

### Code Estimate

~25 lines added to `main.py`

### Files Not Changed

- `objectstore.py` — already has `get_all()` and `get()` methods
- `registryRequest` model — reused for response serialization

## Alternatives Considered

### Separate Router Module

Extract API routes into `aprs_service_registry/api/registry.py` using FastAPI's `APIRouter`.

**Rejected:** Overkill for 2 endpoints given the current simplicity of the app.

### Response Models with OpenAPI Docs

Define Pydantic response models for better API documentation.

**Deferred:** Can be added later if the API grows. Not needed for initial implementation.

## Testing

- Test list endpoint returns correct structure with count, timestamp, and services array
- Test list endpoint returns empty array when no services registered
- Test single service endpoint returns service data for valid callsign
- Test single service endpoint returns 404 for invalid callsign
- Test callsign lookup is case-insensitive
