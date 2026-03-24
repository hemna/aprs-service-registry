# GET Services API Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GET endpoints to fetch all services and individual services from the APRS Service Registry API.

**Architecture:** Two new GET endpoints in `main.py` using the existing `APRSServices` singleton. List endpoint returns services with count and timestamp metadata. Single service endpoint returns 404 for unknown callsigns.

**Tech Stack:** FastAPI, Pydantic, Python datetime

**Spec:** `docs/superpowers/specs/2026-03-23-get-services-api-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `aprs_service_registry/main.py` | Modify | Add GET endpoints and imports |
| `tests/test_api.py` | Create | API endpoint tests |

---

## Chunk 1: GET All Services Endpoint

### Task 1: Create test file and write failing test for list endpoint

**Files:**
- Create: `tests/test_api.py`

- [ ] **Step 1: Create test file with list endpoint test**

```python
"""Tests for APRS Service Registry API endpoints."""
from fastapi.testclient import TestClient

from aprs_service_registry.main import app, APRSServices, registryRequest


client = TestClient(app)


class TestGetAllServices:
    """Tests for GET /api/v1/registry endpoint."""

    def setup_method(self):
        """Clear services before each test."""
        services = APRSServices()
        services.data = {}

    def test_get_all_services_empty(self):
        """Returns empty list with count 0 when no services registered."""
        response = client.get("/api/v1/registry")
        
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["services"] == []
        assert "timestamp" in data

    def test_get_all_services_with_data(self):
        """Returns all registered services with correct count."""
        # Register a test service
        services = APRSServices()
        services.add("TEST1", registryRequest(
            callsign="TEST1",
            description="Test Service 1",
            service_website="https://test1.example.com",
            software="test-soft 1.0"
        ))
        services.add("TEST2", registryRequest(
            callsign="TEST2",
            description="Test Service 2",
            service_website="https://test2.example.com",
            software="test-soft 2.0"
        ))

        response = client.get("/api/v1/registry")
        
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["services"]) == 2
        assert "timestamp" in data
        
        # Verify service data
        callsigns = [s["callsign"] for s in data["services"]]
        assert "TEST1" in callsigns
        assert "TEST2" in callsigns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api.py -v`

Expected: FAIL (endpoint doesn't exist yet, returns 405 Method Not Allowed)

- [ ] **Step 3: Commit test file**

```bash
git add tests/test_api.py
git commit -m "test: add failing tests for GET all services endpoint"
```

### Task 2: Implement GET all services endpoint

**Files:**
- Modify: `aprs_service_registry/main.py:1-11` (imports)
- Modify: `aprs_service_registry/main.py:100` (after registry POST endpoint)

- [ ] **Step 1: Add datetime import**

In `aprs_service_registry/main.py`, add to imports section (after line 8):

```python
from datetime import datetime, timezone
```

- [ ] **Step 2: Add GET all services endpoint**

In `aprs_service_registry/main.py`, add after the POST `/api/v1/registry` endpoint (after line 99):

```python
@app.get("/api/v1/registry", response_class=JSONResponse)
async def get_all_services():
    """Get all registered services."""
    services = APRSServices()
    all_services = services.get_all()
    
    # Convert Pydantic models to dicts
    services_list = []
    for callsign, service in all_services.items():
        try:
            services_list.append(service.model_dump())
        except AttributeError:
            services_list.append(service.dict())
    
    return {
        "count": len(services_list),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "services": services_list
    }
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `python -m pytest tests/test_api.py::TestGetAllServices -v`

Expected: PASS (both tests)

- [ ] **Step 4: Commit implementation**

```bash
git add aprs_service_registry/main.py
git commit -m "feat: add GET endpoint to list all services"
```

---

## Chunk 2: GET Single Service Endpoint

### Task 3: Write failing test for single service endpoint

**Files:**
- Modify: `tests/test_api.py`

- [ ] **Step 1: Add tests for single service endpoint**

Append to `tests/test_api.py`:

```python
class TestGetSingleService:
    """Tests for GET /api/v1/registry/{callsign} endpoint."""

    def setup_method(self):
        """Clear services and add test data before each test."""
        services = APRSServices()
        services.data = {}
        services.add("TESTCALL", registryRequest(
            callsign="TESTCALL",
            description="Test Service",
            service_website="https://test.example.com",
            software="test-soft 1.0",
            callsign_owner="N0CALL"
        ))

    def test_get_service_found(self):
        """Returns service data when callsign exists."""
        response = client.get("/api/v1/registry/TESTCALL")
        
        assert response.status_code == 200
        data = response.json()
        assert data["callsign"] == "TESTCALL"
        assert data["description"] == "Test Service"
        assert data["service_website"] == "https://test.example.com"
        assert data["software"] == "test-soft 1.0"
        assert data["callsign_owner"] == "N0CALL"

    def test_get_service_case_insensitive(self):
        """Callsign lookup is case-insensitive."""
        response = client.get("/api/v1/registry/testcall")
        
        assert response.status_code == 200
        data = response.json()
        assert data["callsign"] == "TESTCALL"

    def test_get_service_not_found(self):
        """Returns 404 when callsign doesn't exist."""
        response = client.get("/api/v1/registry/NOTEXIST")
        
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "NOTEXIST" in data["detail"]
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python -m pytest tests/test_api.py::TestGetSingleService -v`

Expected: FAIL (endpoint doesn't exist, DELETE endpoint matches the path pattern)

- [ ] **Step 3: Commit test**

```bash
git add tests/test_api.py
git commit -m "test: add failing tests for GET single service endpoint"
```

### Task 4: Implement GET single service endpoint

**Files:**
- Modify: `aprs_service_registry/main.py:3` (imports)
- Modify: `aprs_service_registry/main.py` (after GET all services endpoint)

- [ ] **Step 1: Add HTTPException import**

In `aprs_service_registry/main.py`, modify the FastAPI import line:

```python
from fastapi import FastAPI, WebSocket, Request, Response, HTTPException
```

- [ ] **Step 2: Add GET single service endpoint**

In `aprs_service_registry/main.py`, add after the GET all services endpoint:

```python
@app.get("/api/v1/registry/{callsign}", response_class=JSONResponse)
async def get_service(callsign: str):
    """Get a single service by callsign."""
    services = APRSServices()
    callsign_upper = callsign.upper()
    
    try:
        service = services[callsign_upper]
        try:
            return service.model_dump()
        except AttributeError:
            return service.dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Service '{callsign_upper}' not found")
```

- [ ] **Step 3: Run all tests to verify they pass**

Run: `python -m pytest tests/test_api.py -v`

Expected: PASS (all 5 tests)

- [ ] **Step 4: Commit implementation**

```bash
git add aprs_service_registry/main.py
git commit -m "feat: add GET endpoint to fetch single service by callsign"
```

---

## Chunk 3: Final Verification

### Task 5: Run full test suite and manual verification

- [ ] **Step 1: Run complete test suite**

Run: `python -m pytest tests/ -v`

Expected: All tests PASS

- [ ] **Step 2: Start server and test manually**

Run: `make server` (or `aprs-service-registry server`)

Test list endpoint:
```bash
curl http://localhost:8001/api/v1/registry | jq
```

Expected: JSON with count, timestamp, and services array

- [ ] **Step 3: Test single service endpoint (after registering one)**

```bash
# Register a test service
curl -X POST http://localhost:8001/api/v1/registry \
  -H "Content-Type: application/json" \
  -d '{"callsign": "TEST", "description": "Test", "service_website": "https://test.com", "software": "test"}'

# Fetch it
curl http://localhost:8001/api/v1/registry/TEST | jq

# Test 404
curl http://localhost:8001/api/v1/registry/NOTEXIST
```

- [ ] **Step 4: Verify OpenAPI docs**

Open http://localhost:8001/docs in browser. Verify both GET endpoints appear with correct schemas.

- [ ] **Step 5: Final commit (if any cleanup needed)**

```bash
git status
# If clean, skip. Otherwise:
git add -A
git commit -m "chore: final cleanup for GET services API"
```
