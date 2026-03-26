# Card-Based UI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace table-based main page with card-based UI showing 24-hour health heatmaps and uptime percentages.

**Architecture:** Expand health check storage from 3 to 24 results per service. Create new card template for `/`, move existing table to `/services`. Use Bootstrap grid for responsive layout and collapse components for accordion behavior.

**Tech Stack:** FastAPI, Jinja2, Bootstrap 5.3, vanilla JavaScript

**Spec:** `docs/superpowers/specs/2026-03-26-card-ui-design.md`
**Mockup:** `docs/superpowers/specs/card-ui-mockup.html`

---

## Chunk 1: Storage and Backend

### Task 1: Increase Health Check Storage Limit

**Files:**
- Modify: `aprs_service_registry/health_checker.py:19`

- [ ] **Step 1: Update MAX_RESULTS_PER_SERVICE constant**

Change line 19 from:
```python
MAX_RESULTS_PER_SERVICE = 3
```
to:
```python
MAX_RESULTS_PER_SERVICE = 24
```

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `pytest tests/test_health_checker.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add aprs_service_registry/health_checker.py
git commit -m "feat: increase health check history to 24 results for heatmap"
```

---

### Task 2: Add Uptime Calculation Helper

**Files:**
- Modify: `aprs_service_registry/health_checker.py`
- Modify: `tests/test_health_checker.py`

- [ ] **Step 1: Write failing test for uptime calculation**

Add to `tests/test_health_checker.py`:
```python
def test_calculate_uptime_all_success():
    """Test uptime calculation with all successful checks."""
    results = [{"success": True} for _ in range(24)]
    assert calculate_uptime(results) == "100%"


def test_calculate_uptime_mixed():
    """Test uptime calculation with mixed results."""
    results = [{"success": True}] * 23 + [{"success": False}]
    assert calculate_uptime(results) == "96%"


def test_calculate_uptime_all_failures():
    """Test uptime calculation with all failures."""
    results = [{"success": False} for _ in range(24)]
    assert calculate_uptime(results) == "0%"


def test_calculate_uptime_empty():
    """Test uptime calculation with no data."""
    assert calculate_uptime([]) == "--"


def test_calculate_uptime_partial():
    """Test uptime calculation with partial data (new service)."""
    results = [{"success": True}] * 6
    assert calculate_uptime(results) == "100%"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_health_checker.py::test_calculate_uptime_all_success -v`
Expected: FAIL with "cannot import name 'calculate_uptime'"

- [ ] **Step 3: Implement calculate_uptime function**

Add to `aprs_service_registry/health_checker.py` (after imports, before class):
```python
def calculate_uptime(results: list) -> str:
    """Calculate uptime percentage from health check results.
    
    Args:
        results: List of health check result dicts with 'success' key
        
    Returns:
        Uptime string like "96%" or "--" if no data
    """
    if not results:
        return "--"
    passed = sum(1 for r in results if r.get("success", False))
    percentage = (passed / len(results)) * 100
    return f"{percentage:.0f}%"
```

- [ ] **Step 4: Add import to test file**

Add to imports in `tests/test_health_checker.py`:
```python
from aprs_service_registry.health_checker import calculate_uptime
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_health_checker.py -v -k "calculate_uptime"`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add aprs_service_registry/health_checker.py tests/test_health_checker.py
git commit -m "feat: add calculate_uptime helper for card UI"
```

---

### Task 3: Add /services Route for Table View

**Files:**
- Modify: `aprs_service_registry/main.py`

- [ ] **Step 1: Find the existing index route and duplicate for /services**

Locate the existing `@app.get("/")` route that renders `index.html`. Add a new route below it:

```python
@app.get("/services", response_class=HTMLResponse)
async def services_page(request: Request):
    """Render the table view of services."""
    services = app_state.services.get_services()
    health_results = app_state.health_checker.results if app_state.health_checker else {}
    return templates.TemplateResponse(
        "services.html",
        {
            "request": request,
            "services": services,
            "health_results": health_results,
            "current_page": "services",
        },
    )
```

- [ ] **Step 2: Verify app starts without errors**

Run: `python -c "from aprs_service_registry.main import app; print('OK')"`
Expected: "OK" (no import errors)

- [ ] **Step 3: Commit**

```bash
git add aprs_service_registry/main.py
git commit -m "feat: add /services route for table view"
```

---

## Chunk 2: Templates

### Task 4: Create Table View Template (services.html)

**Files:**
- Create: `aprs_service_registry/web/templates/services.html`

- [ ] **Step 1: Copy existing index.html to services.html**

```bash
cp aprs_service_registry/web/templates/index.html aprs_service_registry/web/templates/services.html
```

- [ ] **Step 2: Update services.html navigation to show correct active state**

In `services.html`, update the navigation section to mark "Table" as active:
- Find the navigation links
- Add `active` class to Table link
- Remove `active` class from Cards link (if present)
- Add navigation links if not present:

```html
<ul class="nav nav-pills mb-4">
    <li class="nav-item">
        <a class="nav-link" href="/">Cards</a>
    </li>
    <li class="nav-item">
        <a class="nav-link active" href="/services">Table</a>
    </li>
</ul>
```

- [ ] **Step 3: Commit**

```bash
git add aprs_service_registry/web/templates/services.html
git commit -m "feat: create services.html for table view"
```

---

### Task 5: Create Card View Template (index.html)

**Files:**
- Modify: `aprs_service_registry/web/templates/index.html`

- [ ] **Step 1: Replace index.html content with card-based layout**

The new template should include:
1. Navigation pills (Cards active, Table link)
2. Responsive card grid using Bootstrap
3. Card component with collapsed/expanded states
4. Heatmap visualization
5. Accordion JavaScript

Use the mockup at `docs/superpowers/specs/card-ui-mockup.html` as reference.

Key template structure:
```html
{% extends "base.html" %}

{% block content %}
<!-- Navigation -->
<ul class="nav nav-pills mb-4">
    <li class="nav-item">
        <a class="nav-link active" href="/">Cards</a>
    </li>
    <li class="nav-item">
        <a class="nav-link" href="/services">Table</a>
    </li>
</ul>

<!-- Card Grid -->
<div class="row g-4">
{% for service in services %}
    <div class="col-12 col-md-6 col-lg-4">
        <div class="service-card p-3" tabindex="0" onclick="toggleCard(this)" onkeydown="handleCardKeydown(event, this)">
            <!-- Card header with callsign and status badge -->
            <div class="card-header-section">
                <span class="callsign">{{ service.callsign }}</span>
                <span class="status-badge status-{{ service.status }}">{{ service.status }}</span>
            </div>
            
            <!-- Description (truncated) -->
            <p class="description truncated">{{ service.description or "No description provided" }}</p>
            
            <!-- Heatmap + Uptime -->
            <div class="heatmap-container">
                <div class="heatmap">
                    {% set results = health_results.get(service.callsign, []) %}
                    {% for i in range(24) %}
                        {% if i < (24 - results|length) %}
                            <div class="heatmap-dot dot-nodata" title="Hour {{ i + 1 }}: no data"></div>
                        {% else %}
                            {% set idx = i - (24 - results|length) %}
                            {% if results[idx].success %}
                                <div class="heatmap-dot dot-success" title="Hour {{ i + 1 }}: passed"></div>
                            {% else %}
                                <div class="heatmap-dot dot-failure" title="Hour {{ i + 1 }}: failed"></div>
                            {% endif %}
                        {% endif %}
                    {% endfor %}
                </div>
                <span class="uptime">{{ calculate_uptime(health_results.get(service.callsign, [])) }}</span>
            </div>
            
            <!-- Website URL -->
            {% if service.website %}
                <a href="{{ service.website }}" class="website-link" target="_blank" onclick="event.stopPropagation()">{{ service.website }}</a>
            {% endif %}
            
            <!-- Expanded details (hidden by default) -->
            <div class="expanded-details" style="display: none;">
                <!-- Service details -->
            </div>
        </div>
    </div>
{% endfor %}
</div>

<script>
let currentlyExpanded = null;

function toggleCard(card) {
    const details = card.querySelector('.expanded-details');
    const description = card.querySelector('.description');
    
    if (currentlyExpanded === card) {
        details.style.display = 'none';
        description.classList.add('truncated');
        card.classList.remove('expanded');
        currentlyExpanded = null;
        return;
    }
    
    if (currentlyExpanded) {
        const prevDetails = currentlyExpanded.querySelector('.expanded-details');
        const prevDesc = currentlyExpanded.querySelector('.description');
        prevDetails.style.display = 'none';
        prevDesc.classList.add('truncated');
        currentlyExpanded.classList.remove('expanded');
    }
    
    details.style.display = 'block';
    description.classList.remove('truncated');
    card.classList.add('expanded');
    currentlyExpanded = card;
}

function handleCardKeydown(event, card) {
    if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        toggleCard(card);
    }
}
</script>
{% endblock %}
```

- [ ] **Step 2: Verify template renders without errors**

Run the app and check `/` loads:
```bash
cd /Users/I530566/devel/mine/hamradio/aprs-service-registry
python -m aprs_service_registry.main &
sleep 2
curl -s http://localhost:8000/ | head -20
kill %1
```

- [ ] **Step 3: Commit**

```bash
git add aprs_service_registry/web/templates/index.html
git commit -m "feat: replace index.html with card-based layout"
```

---

### Task 6: Update Main Route to Pass calculate_uptime

**Files:**
- Modify: `aprs_service_registry/main.py`

- [ ] **Step 1: Import calculate_uptime in main.py**

Add to imports:
```python
from aprs_service_registry.health_checker import calculate_uptime
```

- [ ] **Step 2: Pass calculate_uptime to template context**

Update the index route to include calculate_uptime in the template context:
```python
return templates.TemplateResponse(
    "index.html",
    {
        "request": request,
        "services": services,
        "health_results": health_results,
        "current_page": "cards",
        "calculate_uptime": calculate_uptime,
    },
)
```

- [ ] **Step 3: Commit**

```bash
git add aprs_service_registry/main.py
git commit -m "feat: pass calculate_uptime helper to card template"
```

---

## Chunk 3: Styling

### Task 7: Add Card and Heatmap CSS

**Files:**
- Modify: `aprs_service_registry/web/static/main.css`

- [ ] **Step 1: Add card styles to main.css**

Append to `main.css`:
```css
/* Service Cards */
.service-card {
    background: var(--card-bg, #ffffff);
    border: 1px solid var(--card-border, #dee2e6);
    border-radius: 8px;
    cursor: pointer;
    transition: box-shadow 0.2s ease, transform 0.2s ease;
}

.service-card:hover {
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    transform: translateY(-2px);
}

.service-card:focus {
    outline: 2px solid var(--primary, #0066cc);
    outline-offset: 2px;
}

.service-card.expanded {
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

.card-header-section {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.75rem;
}

.callsign {
    font-size: 1.25rem;
    font-weight: 600;
    font-family: 'Courier New', monospace;
}

.status-badge {
    padding: 0.25rem 0.75rem;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
}

.status-active {
    background-color: rgba(40, 167, 69, 0.15);
    color: var(--status-active, #28a745);
}

.status-pending {
    background-color: rgba(255, 193, 7, 0.15);
    color: #856404;
}

.status-down {
    background-color: rgba(220, 53, 69, 0.15);
    color: var(--status-down, #dc3545);
}

.description {
    color: #666;
    font-size: 0.9rem;
    margin-bottom: 1rem;
    line-height: 1.4;
}

.description.truncated {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

/* Heatmap */
.heatmap-container {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
}

.heatmap {
    display: flex;
    gap: 3px;
    flex-wrap: nowrap;
}

.heatmap-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
}

.dot-success {
    background-color: var(--status-active, #28a745);
}

.dot-failure {
    background-color: var(--status-down, #dc3545);
}

.dot-nodata {
    background-color: #d0d0d0;
}

.uptime {
    font-weight: 600;
    font-size: 0.9rem;
    white-space: nowrap;
}

.website-link {
    font-size: 0.85rem;
    color: #0066cc;
    text-decoration: none;
    display: block;
}

.website-link:hover {
    text-decoration: underline;
}

/* Expanded card details */
.expanded-details {
    border-top: 1px solid #eee;
    margin-top: 1rem;
    padding-top: 1rem;
}

.detail-row {
    display: flex;
    margin-bottom: 0.5rem;
    font-size: 0.85rem;
}

.detail-label {
    color: #666;
    width: 120px;
    flex-shrink: 0;
}

.detail-value {
    font-weight: 500;
}

.health-log {
    margin-top: 1rem;
}

.health-log-title {
    font-size: 0.85rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
}

.health-log-item {
    font-size: 0.8rem;
    padding: 0.25rem 0;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.check-success {
    color: var(--status-active, #28a745);
}

.check-failure {
    color: var(--status-down, #dc3545);
}

/* Responsive heatmap */
@media (max-width: 768px) {
    .heatmap-dot {
        width: 8px;
        height: 8px;
    }
    .heatmap {
        gap: 2px;
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add aprs_service_registry/web/static/main.css
git commit -m "feat: add card and heatmap CSS styles"
```

---

## Chunk 4: Testing and Deployment

### Task 8: Manual Testing

- [ ] **Step 1: Run the application locally**

```bash
cd /Users/I530566/devel/mine/hamradio/aprs-service-registry
python -m aprs_service_registry.main
```

- [ ] **Step 2: Test card view at /**

Open http://localhost:8000/ and verify:
- Cards display in responsive grid
- Status badges show correct colors
- Heatmaps render (may be all gray for new services)
- Uptime percentages display
- Click to expand works (accordion behavior)
- Keyboard navigation (Tab, Enter/Space)
- Links open in new tab without triggering card expand

- [ ] **Step 3: Test table view at /services**

Open http://localhost:8000/services and verify:
- Table view displays correctly
- Navigation shows Table as active
- All existing functionality works

- [ ] **Step 4: Test responsive layouts**

Resize browser to verify:
- Desktop: 3 cards per row
- Tablet (~768px): 2 cards per row
- Mobile (<768px): 1 card per row

### Task 9: Run All Tests

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v
```

Expected: All tests PASS

- [ ] **Step 2: Commit any test fixes if needed**

### Task 10: Deploy to Production

- [ ] **Step 1: SSH to production server**

```bash
ssh waboring@cloud.hemna.com
```

- [ ] **Step 2: Navigate to app directory and pull changes**

```bash
cd ~/docker/aprs-service-registry
git pull origin master
```

- [ ] **Step 3: Restart the Docker container**

```bash
docker-compose restart
# or
docker restart aprs-service-registry
```

- [ ] **Step 4: Verify production deployment**

Open https://aprs-services.hemna.com (or production URL) and verify card view works.

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Increase health check storage | health_checker.py |
| 2 | Add uptime calculation helper | health_checker.py, tests |
| 3 | Add /services route | main.py |
| 4 | Create services.html (table view) | templates/services.html |
| 5 | Update index.html (card view) | templates/index.html |
| 6 | Pass calculate_uptime to template | main.py |
| 7 | Add card/heatmap CSS | main.css |
| 8 | Manual testing | - |
| 9 | Run test suite | - |
| 10 | Deploy to production | - |

Estimated time: 2-3 hours
