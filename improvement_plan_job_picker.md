# Improvement Plan — Job Picker UX
> **Scope:** `iastronauts_creditiq_front/` (frontend) + minor `local_server.py` additions.
> Work top-down, one item per chat. Mark each item ✅ DONE when finished.

---

## Context

The job picker is a modal dialog in `AnalysisPage.tsx` (lines ~1939–2010).
It fetches `GET /jobs` on open and renders a scrollable flat list.

**Current `JobSummary` shape (frontend interface + backend response):**
```ts
{
  job_id: string          // e.g. "a1b2c3d4-..."
  date: string            // S3 folder name, e.g. "2025-06-05"
  status: string          // "completed" | "failed" | "extraction_complete" | ...
  company_name: string | null
  periods: string[]       // e.g. ["2025-03", "2024-12"]
}
```

**User pain:** "it is difficult to know which one I need" — the list is flat, dates are
raw strings, and the only filter is a text search by company name or job ID.

---

## Item 1 — Status filter chips  *(PRIORITY: HIGH)* ✅ DONE

**Goal.** One-click filter to see only jobs in a given state.
Most of the time the user wants completed jobs only.

**Current state.** No status filter. All statuses are mixed in the list.

**How.**
1. Add a row of filter chips above the search box in the job picker modal:
   `All | Completed | In Progress | Failed`
   - "In Progress" covers: `pending`, `processing`, `extraction_complete`, `analysis_complete`, `scoring_complete`
   - "Failed" covers: `failed`, `cancelled`
2. Add a `statusFilter: string` state variable (default `"all"`).
3. Apply the filter after the existing text search filter.
4. Highlight the active chip (filled background vs outlined).

**Files.**
- `src/pages/AnalysisPage.tsx` — modal section only (~lines 1939–2010)

**Acceptance.**
- Clicking "Completed" shows only completed jobs; count badge on chip is optional.
- Default view shows all (no regression in current behavior).
- Chips are visible on mobile width without overflow.

---

## Item 2 — Human-readable dates + relative time  *(PRIORITY: HIGH)* ✅ DONE

**Goal.** Replace raw ISO date strings ("2025-06-05") with readable labels
("Jun 5, 2025" + "3 days ago") so users can identify recent jobs at a glance.

**Current state.** The `date` field from the API is an S3 folder name like `"2025-06-05"`.
It is rendered verbatim in the card.

**How.**
1. Add a pure helper `formatJobDate(dateStr: string): { label: string; relative: string }`:
   - `label`: e.g. `"Jun 5, 2025"` using `Intl.DateTimeFormat`.
   - `relative`: e.g. `"3 days ago"` / `"Today"` / `"Yesterday"` using date math.
2. In the job card, replace the raw date with `label` as primary text and `relative`
   as a secondary muted tag next to it.
3. Add **date-based section headers**: group jobs into "Today", "This week", "This month",
   "Older" buckets in the rendered list (purely client-side grouping, no API change).

**Files.**
- `src/pages/AnalysisPage.tsx` — card rendering + list grouping

**Acceptance.**
- A job created today shows "Today" and "Jun 12, 2025".
- A job from last month shows "Older" section with its absolute date.
- No section header appears if that bucket is empty.

---

## Item 3 — Sort control  *(PRIORITY: MEDIUM)* ✅ DONE

**Goal.** Let the user toggle between Newest first, Oldest first, and Company A–Z.

**Current state.** Always sorted newest-first (backend sorts by date desc).
No user control.

**How.**
1. Add a small sort toggle (icon button or segmented control) in the modal header row:
   `↓ Newest | ↑ Oldest | A–Z`
2. Add `sortMode: 'newest' | 'oldest' | 'alpha'` state (default `'newest'`).
3. Apply the sort after status + text filters client-side.
4. `'alpha'` sorts by `company_name` (null names go to the end).

**Files.**
- `src/pages/AnalysisPage.tsx`

**Acceptance.**
- Toggling "A–Z" reorders the list alphabetically by company name.
- Sort state resets to "Newest" when the modal is closed and reopened.

---

## Item 4 — Period quick-filter  *(PRIORITY: MEDIUM)* ✅ DONE

**Goal.** Let the user filter jobs by reporting period (e.g. "show all Q1 2025 analyses").

**Current state.** Periods are displayed as raw strings ("2025-03", "2024-12") in the card.
No filtering by period.

**How.**
1. After the job list loads, derive unique current periods from all jobs:
   `[...new Set(jobs.map(j => j.periods[0]).filter(Boolean))].sort().reverse()`
2. Render them as selectable chips below the search box (e.g. "Mar 2025", "Jun 2025").
   Format using `formatPeriod("2025-03") → "Mar 2025"` helper.
3. Clicking a chip sets `periodFilter: string | null`; clicking the active chip clears it.
4. Apply filter: `job.periods[0] === periodFilter` (match on first/current period only).
5. If fewer than 2 distinct periods exist, don't render the chips (no value).

**Files.**
- `src/pages/AnalysisPage.tsx`

**Acceptance.**
- Selecting "Mar 2025" shows only jobs whose first period is "2025-03".
- Displayed chip label is human-readable ("Mar 2025", not "2025-03").
- No chip row when all jobs share the same period.

---

## Item 5 — Richer job card  *(PRIORITY: MEDIUM)* ✅ DONE

**Goal.** Show enough context in each card to identify a job without opening it:
comparative period label, pipeline progress bar, and a cleaner layout.

**Current state.** Card shows: company name | status badge | indicator dots | raw date | raw periods | job ID.
Periods shown as "2025-03, 2024-12" — hard to read.

**How.**
1. **Period label.** Format as `"Mar 2025 vs Dec 2024"` using `formatPeriod()` from Item 4.
   Source: `periods[0]` (current) and `periods[1]` (comparative).
   If only one period present, show `"Mar 2025"` only.
2. **Pipeline progress bar.** Replace the current dot indicators with a compact
   horizontal stepper (4 segments: Extract → Analyze → Score → Report).
   Fill each segment based on status:
   - `extraction_complete` → 1/4 filled
   - `analysis_complete` → 2/4
   - `scoring_complete` → 3/4
   - `completed` → 4/4 (full, accent color)
   - `failed` → red tint on the last filled segment
3. **Layout.** Two-row card:
   - Row 1: `[Company name bold]` .............. `[status chip]`
   - Row 2: `[period label]` · `[relative date]` ........ `[pipeline bar]`
   - Row 3 (collapsed): job ID in tiny muted text (keep for copy-paste)
4. **No backend change needed** — all data already in `JobSummary`.

**Files.**
- `src/pages/AnalysisPage.tsx`

**Acceptance.**
- A completed job shows "Mar 2025 vs Dec 2024" and a fully-filled green bar.
- A half-done job (analysis_complete) shows the bar 2/4 filled.
- A failed job shows a red segment.
- The job ID is still visible (small, bottom of card).

---

## Execution order

| Item | Effort | Dependency | Theme |
|------|--------|------------|-------|
| 1 | Low | None | Filter by status |
| 2 | Low | None | Readable dates + grouping |
| 3 | Low | None | Sort control |
| 4 | Medium | Item 2 helper | Period filter |
| 5 | Medium | Item 4 helper | Richer card layout |

Items 1 and 2 can be done in the same chat if desired — they are independent.
Items 4 and 5 share the `formatPeriod()` helper; do 4 before 5.

---

## Definition of done (whole plan)

The job picker must allow a user to answer "which analysis for Fondo X from March 2025?" 
in under 3 seconds — without knowing the job ID or needing to open each card individually.
