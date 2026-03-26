# Card-Based UI for APRS Service Registry

**Date:** 2026-03-26  
**Status:** Approved  
**Author:** Assistant (via brainstorming session)

## Overview

Replace the current table-based main page with a card-based UI showing service health visualizations. The existing table view moves to a dedicated `/services` page.

## Goals

1. Provide at-a-glance service health status via visual heatmap
2. Show uptime percentage for quick assessment
3. Allow drill-down into service details via card expansion
4. Maintain access to full table view for power users

## Non-Goals

- Historical data beyond 24 hours (future enhancement)
- Real-time updates/WebSocket integration
- Service management (add/edit/delete) from cards
- Search/filtering on card view (use table view for that)
- Pagination (current service count is manageable)

---

## Design

### Storage Changes

**File:** `aprs_service_registry/health_checker.py`

- Change `MAX_RESULTS_PER_SERVICE` from `3` to `24`
- No other storage changes required
- Pickle-based persistence retained

### URL Structure

| URL | Content |
|-----|---------|
| `/` | Card-based view (new) |
| `/services` | Table view (moved from `/`) |

### Card Layout

**Responsive Grid:**
- Desktop (≥992px): 3 cards per row
- Tablet (≥768px): 2 cards per row  
- Mobile (<768px): 1 card per row

**Card Spacing:** Use Bootstrap's `g-4` gutter class

### Card Component

#### Collapsed State (Default)

```
┌─────────────────────────────────────────────────┐
│ CALLSIGN-SS                        [ACTIVE]     │
│                                                 │
│ Service description text goes here, truncated   │
│ if too long...                                  │
│                                                 │
│ [●●●●●●●●●○●●●●●●●●●●●●●●] 96%                  │
│                                                 │
│ 🔗 https://example.com                          │
└─────────────────────────────────────────────────┘
```

**Elements:**
1. **Header:** Callsign with SSID
2. **Status Badge:** Colored badge (green=active, yellow=pending, red=down)
3. **Description:** Truncated to ~100 chars with ellipsis
4. **Health Heatmap:** 24 dots representing hourly checks
   - Green (●): Check passed
   - Red (●): Check failed
   - Gray (○): No data (service too new)
5. **Uptime %:** Calculated as `(passed_checks / total_checks) × 100`, displayed inline right of heatmap
6. **Website URL:** Clickable link with external icon

#### Expanded State (On Click)

```
┌─────────────────────────────────────────────────┐
│ CALLSIGN-SS                        [ACTIVE]     │
│                                                 │
│ Full service description without truncation.    │
│ Can be multiple lines as needed.                │
│                                                 │
│ [●●●●●●●●●○●●●●●●●●●●●●●●] 96%                  │
│                                                 │
│ 🔗 https://example.com                          │
│                                                 │
│ ─────────────────────────────────────────────── │
│                                                 │
│ Type:          APRS Message Service             │
│ Frequency:     144.390 MHz                      │
│ SSID:          -10                              │
│ Registered:    2025-01-15                       │
│ Last Check:    2026-03-26 14:32:00 UTC          │
│                                                 │
│ Recent Health Checks:                           │
│ ✓ 14:32 - OK                                    │
│ ✓ 13:32 - OK                                    │
│ ✗ 12:32 - Timeout                               │
│ ✓ 11:32 - OK                                    │
│ ...                                             │
└─────────────────────────────────────────────────┘
```

**Additional Elements (expanded only):**
- Service type
- Frequency
- SSID  
- Registration date
- Last check timestamp
- Recent health check log (scrollable if many entries)

#### Interaction Behavior

- **Click anywhere on card:** Toggle expand/collapse
- **Accordion mode:** Only one card expanded at a time
- **Animation:** Smooth expand/collapse transition (Bootstrap collapse)
- **URL links:** Click should open in new tab, not trigger card toggle (use `event.stopPropagation()`)
- **Keyboard:** Cards focusable with `tabindex="0"`, Enter/Space to toggle

### Heatmap Visualization

**Data Structure:**
```python
# Health check result stored in checker
{
    "timestamp": "2026-03-26T14:32:00Z",
    "success": True,
    "message": "OK" | "Timeout" | "Error details"
}
```

**Display Logic:**
- Show 24 most recent hourly checks
- Order: Oldest on left, newest on right
- Dot colors:
  - `var(--status-active)` (#28a745): Success
  - `var(--status-down)` (#dc3545): Failure
  - `var(--text-muted)` (#6c757d): No data
- Dot size: 8-10px diameter
- Dot spacing: 2-4px gap

**Uptime Calculation:**
```python
def calculate_uptime(results: list) -> str:
    if not results:
        return "--"  # No data
    passed = sum(1 for r in results if r["success"])
    percentage = (passed / len(results)) * 100
    return f"{percentage:.0f}%"
```

**Display Format:**
- `96%` for normal values
- `100%` when perfect
- `0%` when all checks failed
- `--` when no health check data available (new service)

### Accessibility

- Each heatmap dot has `title` attribute: "Hour {N}: passed/failed/no data"
- Status badges include text label, not just color
- Cards focusable with `tabindex="0"` for keyboard navigation
- Expand/collapse triggered by Enter or Space key
- Color is not the only indicator (dots also have title tooltips)

### Edge Cases

| Case | Handling |
|------|----------|
| No health check data | Show 24 gray dots, display "--" for uptime |
| Empty description | Show "No description provided" in italics |
| Very long callsign | Truncate at 15 chars with ellipsis (rare edge case) |
| Missing website URL | Don't show URL section |
| 0% uptime | Show "0%" (not hidden or special-cased) |
| API/health checker unavailable | Show cached data with "Data may be stale" warning |

### Status Badge Colors

Use existing CSS variables from design system:
- Active: `var(--status-active)` - Green
- Pending: `var(--status-pending)` - Yellow/Orange  
- Down: `var(--status-down)` - Red

### Navigation

**Header Navigation:**
Add navigation links to switch between views:
- "Cards" → `/` (active when on card view)
- "Table" → `/services` (active when on table view)

Use Bootstrap nav pills or tabs styling.

---

## Implementation Plan

### Phase 1: Storage Update
1. Update `MAX_RESULTS_PER_SERVICE = 24` in health_checker.py
2. Update tests if they depend on the old value

### Phase 2: Backend Routes
1. Create `/services` route serving current table template
2. Modify `/` route to serve new card template
3. Add health check data to template context (for heatmap)

### Phase 3: Card Template
1. Create `cards.html` template (or rename existing)
2. Implement responsive card grid with Bootstrap
3. Build collapsed card component
4. Add expand/collapse JavaScript (accordion behavior)
5. Implement heatmap dot visualization
6. Calculate and display uptime percentage

### Phase 4: Table View Migration  
1. Move current index.html content to services.html
2. Update any links pointing to old structure
3. Add navigation between views

### Phase 5: Styling
1. Add CSS for heatmap dots
2. Style expanded card details section
3. Add hover states and transitions
4. Ensure mobile responsiveness

### Phase 6: Testing & Deployment
1. Test all breakpoints (mobile/tablet/desktop)
2. Test accordion behavior
3. Test with services that have varying health history
4. Deploy to production

---

## Files to Modify

| File | Changes |
|------|---------|
| `aprs_service_registry/health_checker.py` | `MAX_RESULTS_PER_SERVICE = 24` |
| `aprs_service_registry/main.py` | Add `/services` route, modify `/` route context |
| `aprs_service_registry/web/templates/index.html` | Replace with card-based layout |
| `aprs_service_registry/web/templates/services.html` | New file - table view (copy from old index.html) |
| `aprs_service_registry/web/static/main.css` | Add heatmap and card styles |
| `tests/test_health_checker.py` | Update if tests depend on MAX_RESULTS value |

---

## Open Questions

None - all design decisions resolved in brainstorming session and spec review.

---

## Spec Review Notes

Spec reviewed by subagent on 2026-03-26. Key improvements made:
- Added accessibility section (keyboard nav, ARIA/title attributes)
- Added edge cases table
- Fixed uptime calculation inconsistency (now returns string "--" for no data)
- Added `event.stopPropagation()` note for URL click handling
- Clarified non-goals (no search/filtering on cards, no pagination)

---

## Appendix: Brainstorming Session Decisions

| Question | Decision |
|----------|----------|
| Card layout | Responsive grid: 3/2/1 columns |
| Graph style | Heatmap with 24 dots (hourly) |
| Card content | Callsign, status, description, heatmap, URL |
| Navigation | Separate pages (cards at `/`, table at `/services`) |
| Uptime display | Inline right of heatmap |
| Click behavior | Expand card with full details |
| Multiple expanded | No - accordion style (one at a time) |
| Storage approach | Keep pickle, increase to 24 results |
