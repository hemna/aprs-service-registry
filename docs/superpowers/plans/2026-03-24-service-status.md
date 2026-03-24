# Service Status Feature Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add status field to services (active/down/deleted) with soft delete and API filtering.

**Architecture:** Add `status` field to `registryRequest` model with Pydantic validation. Modify GET endpoint to filter by status using query params. Change DELETE to soft delete. Update website template to show down services with indicator.

**Tech Stack:** FastAPI, Pydantic, Jinja2

**Spec:** `docs/superpowers/specs/2026-03-24-service-status-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `aprs_service_registry/main.py` | Modify | Add status to model, update endpoints, update website route |
| `aprs_service_registry/web/templates/index.html` | Modify | Show down services with badge, hide deleted |
| `tests/test_api.py` | Modify | Add tests for status filtering |

---

## Chunk 1: Model and Status Validation

### Task 1: Add status field to model with validation

**Files:**
- Modify: `aprs_service_registry/main.py:29-36`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test for status field validation**

Add to `tests/test_api.py`:

```python
class TestServiceStatus:
    """Tests for service status field."""

    def setup_method(self):
        """Clear services before each test."""
        services = APRSServices()
        services.data = {}

    def test_register_service_default_status(self):
        """New services default to active status."""
        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "TEST",
                "description": "Test Service",
                "service_website": "https://test.com",
                "software": "test 1.0",
            },
        )
        assert response.status_code == 200

        # Fetch and verify status
        get_response = client.get("/api/v1/registry/TEST")
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "active"

    def test_register_service_with_status(self):
        """Can register a service with explicit status."""
        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "TEST",
                "description": "Test Service",
                "service_website": "https://test.com",
                "software": "test 1.0",
                "status": "down",
            },
        )
        assert response.status_code == 200

        get_response = client.get("/api/v1/registry/TEST")
        assert get_response.json()["status"] == "down"

    def test_register_service_invalid_status(self):
        """Invalid status returns 422 validation error."""
        response = client.post(
            "/api/v1/registry",
            json={
                "callsign": "TEST",
                "description": "Test Service",
                "service_website": "https://test.com",
                "software": "test 1.0",
                "status": "invalid",
            },
        )
        assert response.status_code == 422

    def test_get_single_service_returns_regardless_of_status(self):
        """GET /api/v1/registry/{callsign} returns service even if deleted."""
        # Register and delete a service
        client.post(
            "/api/v1/registry",
            json={
                "callsign": "DELETED",
                "description": "Deleted Service",
                "service_website": "https://deleted.com",
                "software": "test 1.0",
                "status": "deleted",
            },
        )

        # Should still be fetchable by callsign
        get_response = client.get("/api/v1/registry/DELETED")
        assert get_response.status_code == 200
        assert get_response.json()["callsign"] == "DELETED"
        assert get_response.json()["status"] == "deleted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py::TestServiceStatus -v`

Expected: FAIL (status field doesn't exist)

- [ ] **Step 3: Add status field to model with Literal validation**

In `aprs_service_registry/main.py`, update imports and model:

```python
# Add to imports (around line 5)
from typing import Literal

# Update registryRequest class (around line 29)
class registryRequest(BaseModel):
    """Request to register a service with the registry."""

    callsign: str
    description: str
    service_website: str
    software: str
    callsign_owner: str | None = None
    status: Literal["active", "down", "deleted"] = "active"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api.py::TestServiceStatus -v`

Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add aprs_service_registry/main.py tests/test_api.py
git commit -m "feat: add status field to service model with validation"
```

---

## Chunk 2: GET Endpoint Filtering

### Task 2: Add status filtering to GET /api/v1/registry

**Files:**
- Modify: `aprs_service_registry/main.py:104-122`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing tests for status filtering**

Add to `tests/test_api.py`:

```python
class TestStatusFiltering:
    """Tests for GET /api/v1/registry status filtering."""

    def setup_method(self):
        """Set up test services with different statuses."""
        services = APRSServices()
        services.data = {}
        
        # Add services with different statuses
        services.add("ACTIVE1", registryRequest(
            callsign="ACTIVE1",
            description="Active service 1",
            service_website="https://active1.com",
            software="test",
            status="active",
        ))
        services.add("ACTIVE2", registryRequest(
            callsign="ACTIVE2",
            description="Active service 2",
            service_website="https://active2.com",
            software="test",
            status="active",
        ))
        services.add("DOWN1", registryRequest(
            callsign="DOWN1",
            description="Down service",
            service_website="https://down.com",
            software="test",
            status="down",
        ))
        services.add("DELETED1", registryRequest(
            callsign="DELETED1",
            description="Deleted service",
            service_website="https://deleted.com",
            software="test",
            status="deleted",
        ))

    def test_default_returns_active_only(self):
        """Default GET returns only active services."""
        response = client.get("/api/v1/registry")
        assert response.status_code == 200
        data = response.json()
        
        assert data["count"] == 2
        callsigns = [s["callsign"] for s in data["services"]]
        assert "ACTIVE1" in callsigns
        assert "ACTIVE2" in callsigns
        assert "DOWN1" not in callsigns
        assert "DELETED1" not in callsigns

    def test_include_down(self):
        """include_down=true returns active + down services."""
        response = client.get("/api/v1/registry?include_down=true")
        assert response.status_code == 200
        data = response.json()
        
        assert data["count"] == 3
        callsigns = [s["callsign"] for s in data["services"]]
        assert "ACTIVE1" in callsigns
        assert "DOWN1" in callsigns
        assert "DELETED1" not in callsigns

    def test_include_deleted(self):
        """include_deleted=true returns active + deleted services."""
        response = client.get("/api/v1/registry?include_deleted=true")
        assert response.status_code == 200
        data = response.json()
        
        assert data["count"] == 3
        callsigns = [s["callsign"] for s in data["services"]]
        assert "ACTIVE1" in callsigns
        assert "DELETED1" in callsigns
        assert "DOWN1" not in callsigns

    def test_include_all(self):
        """include_all=true returns all services."""
        response = client.get("/api/v1/registry?include_all=true")
        assert response.status_code == 200
        data = response.json()
        
        assert data["count"] == 4
        callsigns = [s["callsign"] for s in data["services"]]
        assert "ACTIVE1" in callsigns
        assert "ACTIVE2" in callsigns
        assert "DOWN1" in callsigns
        assert "DELETED1" in callsigns

    def test_combined_flags(self):
        """Multiple flags are additive."""
        response = client.get("/api/v1/registry?include_down=true&include_deleted=true")
        assert response.status_code == 200
        data = response.json()
        
        assert data["count"] == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py::TestStatusFiltering -v`

Expected: FAIL (filtering not implemented)

- [ ] **Step 3: Update GET endpoint with filtering**

In `aprs_service_registry/main.py`, update the `get_all_services` function:

```python
@app.get("/api/v1/registry", response_class=JSONResponse)
async def get_all_services(
    include_down: bool = False,
    include_deleted: bool = False,
    include_all: bool = False,
):
    """Get all registered services, filtered by status."""
    services = APRSServices()
    all_services = services.get_all()

    # Determine which statuses to include
    allowed_statuses = {"active"}
    if include_down or include_all:
        allowed_statuses.add("down")
    if include_deleted or include_all:
        allowed_statuses.add("deleted")

    # Convert Pydantic models to dicts and filter by status
    services_list = []
    for callsign, service in all_services.items():
        # Handle legacy services without status field
        try:
            service_dict = service.model_dump()
        except AttributeError:
            service_dict = service.dict()
        
        # Default status for legacy services
        if "status" not in service_dict or service_dict["status"] is None:
            service_dict["status"] = "active"
        
        if service_dict["status"] in allowed_statuses:
            services_list.append(service_dict)

    return {
        "count": len(services_list),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "services": services_list,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api.py::TestStatusFiltering -v`

Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add aprs_service_registry/main.py tests/test_api.py
git commit -m "feat: add status filtering to GET /api/v1/registry"
```

---

## Chunk 3: Soft Delete

### Task 3: Change DELETE to soft delete

**Files:**
- Modify: `aprs_service_registry/main.py:143-149`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test for soft delete**

Add to `tests/test_api.py`:

```python
class TestSoftDelete:
    """Tests for soft delete behavior."""

    def setup_method(self):
        """Clear services and add test data."""
        services = APRSServices()
        services.data = {}
        services.add("TODELETE", registryRequest(
            callsign="TODELETE",
            description="Service to delete",
            service_website="https://delete.com",
            software="test",
            status="active",
        ))

    def test_delete_sets_status_deleted(self):
        """DELETE sets status to deleted instead of removing."""
        # Verify service exists and is active
        get_response = client.get("/api/v1/registry/TODELETE")
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "active"

        # Delete the service
        delete_response = client.delete("/api/v1/registry/TODELETE")
        assert delete_response.status_code == 200
        data = delete_response.json()
        assert data["status"] == "ok"
        assert "deleted" in data["message"].lower()

        # Service should still exist but with deleted status
        get_response = client.get("/api/v1/registry/TODELETE")
        assert get_response.status_code == 200
        assert get_response.json()["status"] == "deleted"

    def test_deleted_service_excluded_from_list(self):
        """Deleted services are excluded from default list."""
        # Delete the service
        client.delete("/api/v1/registry/TODELETE")

        # Should not appear in default list
        list_response = client.get("/api/v1/registry")
        callsigns = [s["callsign"] for s in list_response.json()["services"]]
        assert "TODELETE" not in callsigns

        # Should appear with include_deleted
        list_response = client.get("/api/v1/registry?include_deleted=true")
        callsigns = [s["callsign"] for s in list_response.json()["services"]]
        assert "TODELETE" in callsigns
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py::TestSoftDelete -v`

Expected: FAIL (delete still removes service)

- [ ] **Step 3: Update DELETE endpoint to soft delete**

In `aprs_service_registry/main.py`, update the `registry_delete` function:

```python
@app.delete("/api/v1/registry/{callsign}", response_class=JSONResponse)
async def registry_delete(callsign: str):
    """Soft delete a service (set status to deleted)."""
    services = APRSServices()
    callsign_upper = callsign.upper()
    
    try:
        service = services[callsign_upper]
        # Update status to deleted
        try:
            service_dict = service.model_dump()
        except AttributeError:
            service_dict = service.dict()
        
        service_dict["status"] = "deleted"
        updated_service = registryRequest(**service_dict)
        services.add(callsign_upper, updated_service)
        
        LOG.info(f"Soft deleted {callsign_upper} from the registry.")
        return {"status": "ok", "message": f"Service '{callsign_upper}' marked as deleted"}
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Service '{callsign_upper}' not found",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api.py::TestSoftDelete -v`

Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add aprs_service_registry/main.py tests/test_api.py
git commit -m "feat: change DELETE to soft delete (set status=deleted)"
```

---

## Chunk 4: Website Updates

### Task 4: Update website route and template to show down services

**Files:**
- Modify: `aprs_service_registry/main.py:72-79` (website route)
- Modify: `aprs_service_registry/web/templates/index.html`

- [ ] **Step 1: Update website route to filter services**

The website should show `active` + `down` services (not `deleted`). Update the `/` route in `main.py`:

```python
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def get(request: Request):
    services = APRSServices()
    all_services = services.get_all()
    
    # Filter for website: show active and down, hide deleted
    filtered_services = {}
    for callsign, service in all_services.items():
        try:
            status = service.status if hasattr(service, 'status') else "active"
        except AttributeError:
            status = "active"
        
        if status in ("active", "down"):
            filtered_services[callsign] = service
    
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "services": filtered_services},
    )
```

- [ ] **Step 2: Update template to show status badge for down services**

In `aprs_service_registry/web/templates/index.html`, update the table header (add Status column) and rows:

Update the `<thead>` section (around line 46-53):

```html
<thead>
  <tr>
    <th>Callsign</th>
    <th>Status</th>
    <th>Owner</th>
    <th>Description</th>
    <th>URL</th>
    <th>Software</th>
  </tr>
</thead>
```

Update the `<tbody>` section (around line 55-68):

```html
<tbody>
  {% for service in services %}
  <tr{% if services[service].status == 'down' %} class="service-down"{% endif %}>
    <td><span class="callsign">{{ service }}</span></td>
    <td>
      {% if services[service].status == 'down' %}
        <span class="badge bg-warning text-dark">DOWN</span>
      {% else %}
        <span class="badge bg-success">Active</span>
      {% endif %}
    </td>
    <td>{{ services[service].callsign_owner or '-' }}</td>
    <td>{{ services[service].description }}</td>
    <td>
      <a href="{{ services[service].service_website }}" target="_blank" rel="noopener noreferrer" class="link-external">
        {{ services[service].service_website }}
      </a>
    </td>
    <td><code class="software">{{ services[service].software }}</code></td>
  </tr>
  {% endfor %}
</tbody>
```

- [ ] **Step 3: Add CSS for down services (optional styling)**

In `aprs_service_registry/web/static/main.css`, add:

```css
/* Service status styling */
.service-down {
    opacity: 0.7;
}

.service-down td {
    color: #6c757d;
}
```

- [ ] **Step 4: Test manually**

Start server: `make server` or `aprs-service-registry server`

1. Register a service with status "down":
   ```bash
   curl -X POST http://localhost:8001/api/v1/registry \
     -H "Content-Type: application/json" \
     -d '{"callsign": "TESTDOWN", "description": "Test down service", "service_website": "https://test.com", "software": "test", "status": "down"}'
   ```

2. Visit http://localhost:8001/
3. Verify:
   - TESTDOWN appears with yellow "DOWN" badge
   - Row is slightly grayed out
   - Active services show green "Active" badge

4. Register a deleted service and verify it does NOT appear:
   ```bash
   curl -X POST http://localhost:8001/api/v1/registry \
     -H "Content-Type: application/json" \
     -d '{"callsign": "TESTDELETED", "description": "Deleted service", "service_website": "https://test.com", "software": "test", "status": "deleted"}'
   ```
   
5. Verify TESTDELETED does not appear on the website

- [ ] **Step 5: Commit**

```bash
git add aprs_service_registry/main.py aprs_service_registry/web/templates/index.html aprs_service_registry/web/static/main.css
git commit -m "feat: show down services with badge on website, hide deleted"
```

---

## Chunk 5: Final Verification and Documentation

### Task 5: Run full test suite and update README

- [ ] **Step 1: Run complete test suite**

Run: `python -m pytest tests/ -v`

Expected: All tests PASS

- [ ] **Step 2: Run pre-commit hooks**

Run: `pre-commit run --all-files`

Expected: All hooks PASS

- [ ] **Step 3: Update README with status documentation**

Add to `README.rst` in the API Reference section, after the existing endpoint documentation:

```rst
Service Status
--------------

Services have a status field that can be one of:

- **active** (default) — Service is operational
- **down** — Service is temporarily unavailable
- **deleted** — Service is soft-deleted

**Filtering by status:**

By default, ``GET /api/v1/registry`` returns only active services. Use query parameters to include other statuses:

.. code-block:: bash

   # Include down services (active + down)
   curl https://aprs.hemna.com/api/v1/registry?include_down=true

   # Include deleted services (active + deleted)
   curl https://aprs.hemna.com/api/v1/registry?include_deleted=true

   # Include all services
   curl https://aprs.hemna.com/api/v1/registry?include_all=true

**Setting service status:**

Include the ``status`` field when registering or updating a service:

.. code-block:: bash

   curl -X POST https://aprs.hemna.com/api/v1/registry \
     -H "Content-Type: application/json" \
     -d '{"callsign": "MYSERVICE", "description": "...", "service_website": "...", "software": "...", "status": "down"}'

**Soft delete:**

``DELETE /api/v1/registry/{callsign}`` sets the service status to ``deleted`` rather than removing it permanently. The service can still be fetched by callsign and will appear with ``?include_deleted=true``.
```

- [ ] **Step 4: Final commit**

```bash
git add README.rst
git commit -m "docs: add service status documentation to README"
```

- [ ] **Step 5: Push changes**

```bash
git push origin master
```
