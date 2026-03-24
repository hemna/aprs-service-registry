# Service Status Feature Design

**Date:** 2026-03-24
**Status:** Approved
**Author:** Assistant

## Summary

Add a status field to services allowing them to be marked as `active`, `down`, or `deleted`. This enables soft deletion and the ability to mark services as temporarily unavailable while keeping them visible on the website.

## Background

Currently, services can only be registered or permanently deleted. There's no way to:
- Mark a service as temporarily down/unavailable
- Soft-delete a service while keeping it in the database for history
- Show down services on the website while hiding them from API consumers

## Design

### Status Values

Services have three possible statuses:

| Status | Description | API Default | Website |
|--------|-------------|-------------|---------|
| `active` | Service is operational | Included | Shown normally |
| `down` | Service is temporarily unavailable | Excluded | Shown with indicator |
| `deleted` | Service is soft-deleted | Excluded | Hidden |

### Model Changes

Add `status` field to `registryRequest`:

```python
class registryRequest(BaseModel):
    callsign: str
    description: str
    service_website: str
    software: str
    callsign_owner: str | None = None
    status: str = "active"  # "active", "down", or "deleted"
```

### API Changes

#### GET /api/v1/registry — List Services

Default behavior returns only `active` services.

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `include_down` | bool | Include `down` services (active + down) |
| `include_deleted` | bool | Include `deleted` services (active + deleted) |
| `include_all` | bool | Include all services regardless of status |

Flags are additive: `?include_down=true&include_deleted=true` returns active + down + deleted services.

**Response:**

```json
{
  "count": 2,
  "timestamp": "2026-03-24T12:00:00Z",
  "services": [
    {
      "callsign": "WXBOT",
      "description": "Weather bot service",
      "service_website": "https://example.com",
      "software": "Unknown",
      "callsign_owner": null,
      "status": "active"
    }
  ]
}
```

#### GET /api/v1/registry/{callsign} — Get Single Service

Returns service regardless of status. This allows checking the status of down or deleted services.

**Response (200 OK):**

```json
{
  "callsign": "WXBOT",
  "description": "Weather bot service",
  "service_website": "https://example.com",
  "software": "Unknown",
  "callsign_owner": null,
  "status": "down"
}
```

#### POST /api/v1/registry — Register/Update Service

Accepts optional `status` field (defaults to `active`). Can be used to change a service's status.

**Validation:** Status must be one of `active`, `down`, or `deleted`. Invalid values return HTTP 422 with validation error.

**Request:**

```json
{
  "callsign": "WXBOT",
  "description": "Weather bot service",
  "service_website": "https://example.com",
  "software": "Unknown",
  "status": "down"
}
```

#### DELETE /api/v1/registry/{callsign} — Soft Delete

Changed from hard delete to soft delete. Sets `status=deleted` instead of removing.

**Response:**

```json
{
  "status": "ok",
  "message": "Service marked as deleted"
}
```

### Website Behavior

The web UI shows services based on status:

- **Active services**: Displayed normally
- **Down services**: Displayed with visual indicator (grayed out or "DOWN" badge)
- **Deleted services**: Hidden from website

The website effectively shows `active` + `down` services, giving users visibility into temporarily unavailable services.

### Data Migration

Existing services in the pickle file don't have a `status` field. Migration is automatic:

- When loading services without a `status` field, default to `active`
- No manual migration script needed
- Handled in model deserialization or `ObjectStoreMixin.load()`

## Implementation

### Files to Modify

| File | Changes |
|------|---------|
| `aprs_service_registry/main.py` | Add status field to model, update endpoints |
| `aprs_service_registry/web/templates/index.html` | Show down services with indicator |
| `tests/test_api.py` | Add tests for status filtering |

### Backward Compatibility

- Existing API consumers continue to work (default behavior unchanged for active services)
- New `status` field in response is additive
- Existing services default to `active`

## Testing

- Test GET list returns only active services by default
- Test `?include_down=true` includes down services
- Test `?include_deleted=true` includes deleted services
- Test `?include_all=true` includes all services
- Test GET single service returns regardless of status
- Test POST can set status
- Test DELETE sets status to deleted (not hard delete)
- Test services without status field default to active
