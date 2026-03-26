# Documentation Rework Design Spec

**Date:** 2026-03-26
**Status:** Draft

## Overview

Rework the APRS Service Registry online documentation to provide a comprehensive, multi-audience documentation system that explains the purpose of the site, how it works, and how to integrate with the API.

## Audience & Assumptions

### Primary Audiences
1. **APRS service operators** — People running APRS services (digipeaters, iGates, etc.) who want to register their service
2. **Developers** — People building applications that consume the registry API
3. **Ham radio enthusiasts** — General users browsing to discover APRS services

### Knowledge Level
- Assume basic ham radio knowledge (callsigns, frequencies, etc.)
- Briefly explain APRS-specific terms (digipeater, iGate, APRS-IS)
- No need to explain what ham radio is

## URL Structure

Flat multi-page structure with intuitive, self-documenting URLs:

| Route | Purpose | Primary Audience |
|-------|---------|------------------|
| `/about` | Overview, purpose, what is APRS Service Registry | All |
| `/guide` | How to register your service, keep-alive requirements | Operators |
| `/developers` | API overview, authentication, code examples | Developers |
| `/service-types` | Explanation of digipeaters, iGates, APRS-IS servers | All |
| `/faq` | Common questions, troubleshooting | All |
| `/docs` | Swagger UI (existing, unchanged) | Developers |
| `/redoc` | ReDoc (existing, unchanged) | Developers |

### Existing Routes (Unchanged)
- `/` — Card-based main page (service listing)
- `/services` — Table view of services
- `/docs` — FastAPI Swagger UI
- `/redoc` — FastAPI ReDoc

## Page Layout

### Sidebar Navigation Layout

All documentation pages share a consistent layout:

```
+-------------------------------------------------------------+
|  APRS Service Registry                    [Theme Toggle]      |
+-------------+-----------------------------------------------+
|             |                                               |
|  DOCS       |  Page Title                                   |
|  ---------- |  Subtitle/description                         |
|  About      |                                               |
|  Guide  <-- |  +-----------------------------------------+  |
|  Developers |  | Quick Start / TL;DR                     |  |
|  Service    |  | Brief summary for fast readers          |  |
|  Types      |  +-----------------------------------------+  |
|  FAQ        |                                               |
|             |  ## Section Heading                          |
|  ---------- |  Content paragraphs, code examples...         |
|  API Ref    |                                               |
|   Swagger   |  ## Another Section                           |
|   ReDoc     |  More content...                              |
|             |                                               |
|  ---------- |                                               |
|  < Home     |                                               |
|             |                                               |
+-------------+-----------------------------------------------+
```

### Layout Specifications
- **Sidebar width:** ~180-200px fixed
- **Content area:** Fluid, max-width ~800px for readability
- **Current page:** Highlighted in sidebar with accent color
- **External links:** Swagger/ReDoc open in same tab (internal to site)
- **Home link:** Returns to main registry page (`/`)

## Visual Design

### Consistency with Main Site
- Use existing CSS variables for colors (`--bg-primary`, `--text-primary`, etc.)
- Same light/dark theme toggle, respects localStorage preference
- Same font stack and sizing conventions
- Sidebar uses `--bg-tertiary` background

### Theme Toggle
- Present on all documentation pages
- Same position as main site (top right area)
- Syncs with main site preference via localStorage

## Page Content Specifications

### 1. About Page (`/about`)

**Purpose:** Explain what the APRS Service Registry is and why it exists.

**Sections:**
- Quick Start (TL;DR)
- What is APRS Service Registry?
- Why use this registry?
- How it works (high-level)
- Who maintains this?

### 2. Guide Page (`/guide`)

**Purpose:** Help service operators register and maintain their services.

**Sections:**
- Quick Start (TL;DR) — minimal steps to register
- Prerequisites (callsign, service running)
- Registration process
- Keep-alive / health check requirements
- Updating your service information
- Removing your service

### 3. Developers Page (`/developers`)

**Purpose:** API documentation for building integrations.

**Sections:**
- Quick Start (TL;DR) — fetch all services in 30 seconds
- API Overview
- Base URL and versioning
- Authentication (if any) / Rate limits
- Endpoints summary table
- Code examples for each major operation:
  - List all services
  - Get a specific service
  - Register a service
  - Health check / keep-alive
- Link to full API reference (Swagger/ReDoc)

**Code Example Languages:**
- curl (always first, universal)
- Python (requests library)
- JavaScript (fetch API)
- Go (net/http)

### 4. Service Types Page (`/service-types`)

**Purpose:** Explain different APRS service types for users unfamiliar with them.

**Sections:**
- Quick Start (TL;DR) — one-sentence definitions
- Digipeaters
- iGates
- APRS-IS Servers
- Other service types (if applicable)

### 5. FAQ Page (`/faq`)

**Purpose:** Answer common questions and troubleshooting.

**Sections:**
- Quick Start (common issues at a glance)
- General Questions
- For Operators
- For Developers
- Health Check Questions

## Technical Implementation

### New Templates
- `templates/docs_base.html` — Base template with sidebar layout
- `templates/about.html`
- `templates/guide.html`
- `templates/developers.html`
- `templates/service_types.html`
- `templates/faq.html`

### New Routes
```python
@app.get("/about", response_class=HTMLResponse, include_in_schema=False)
@app.get("/guide", response_class=HTMLResponse, include_in_schema=False)
@app.get("/developers", response_class=HTMLResponse, include_in_schema=False)
@app.get("/service-types", response_class=HTMLResponse, include_in_schema=False)
@app.get("/faq", response_class=HTMLResponse, include_in_schema=False)
```

### CSS Updates
- `.docs-layout` — Flexbox container for sidebar + content
- `.docs-sidebar` — Fixed-width sidebar styling
- `.docs-content` — Main content area
- `.docs-nav-item` — Sidebar navigation items
- `.docs-nav-item.active` — Current page highlight
- `.quick-start` — Styled callout box for TL;DR sections

### OpenAPI Integration
- Keep `/docs` (Swagger UI) unchanged
- Keep `/redoc` unchanged
- Developers page links to these with explanatory text

## Navigation Updates

### Main Site Header
Add "Docs" link to main site navigation on `/` and `/services` pages.

### Documentation Sidebar
All doc pages include sidebar with:
- All documentation page links
- Separator
- API Reference section (Swagger, ReDoc links)
- Separator
- Home link back to `/`

## Success Criteria

1. Users can understand what the registry is from `/about`
2. Operators can register a service following `/guide`
3. Developers can make their first API call following `/developers`
4. All pages render correctly in light and dark themes
5. Sidebar navigation works on all documentation pages
6. Quick start sections provide immediate value
7. Code examples are copy-pasteable and functional

## Out of Scope

- Search functionality within documentation
- Versioned documentation
- PDF export
- User comments or feedback forms
- Internationalization (i18n)

## Future Considerations

- Could add search later with client-side search
- Could add syntax highlighting for code blocks
- Could add "Edit this page" links if docs move to markdown files
