# Shipping — Week Folder Picker Spec

A **week-folder selector** for the Shipping charges flow. Replaces the raw
`week_folder` text input (`"WE 05.17.2026"`) with a browsable **year → month →
week** picker fed by one API call. The picker also shows, per week, whether a run
already exists and its status/total — so it doubles as a "what's left to do"
view.

Slots into the Shipping screen (§5 of `API_ROUTES.md`) as the control that
produces the `week_folder` handed to `POST /api/shipping/run`. Mirrors the
look/behavior of the existing React app (`REACT_FRONTEND_SPEC.md` §7–8).

---

## 1. Goals

- Let the user **browse** available WE folders grouped by year → month → week
  instead of typing a folder name.
- Surface each week's **run state** (never run / built / needs review / posted /
  error) and **total** inline, so the picker shows progress at a glance.
- Emit a single value — the chosen `week_folder` string — to the parent Shipping
  screen. The picker itself triggers nothing on the ERP.
- Keep a **"most recent"** default (matches the backend, which uses the newest WE
  folder when `week_folder` is omitted).

Non-goals: creating/renaming SharePoint folders; running/posting the flow (the
parent screen owns those); multi-customer (Creekside only).

---

## 2. Data source — one route

### `GET /api/shipping/folders` *(exists)*
No params. Returns every WE folder on SharePoint, grouped and newest-first at
every level, each week annotated with any existing run:

```json
{ "years": [
    { "year": 2026, "months": [
        { "month": 5, "label": "May 2026", "weeks": [
            { "week_folder": "WE 05.17.2026", "date": "2026-05-17",
              "status": "posted", "total": 4231.55, "has_run": true },
            { "week_folder": "WE 05.03.2026", "date": "2026-05-03",
              "status": "needs_review", "total": null, "has_run": true } ] },
        { "month": 4, "label": "April 2026", "weeks": [
            { "week_folder": "WE 04.26.2026", "date": "2026-04-26",
              "status": null, "total": null, "has_run": false } ] } ] } ] }
```

- `status` ∈ `built` | `needs_review` | `posted` | `error` | `null` (never run).
- `total` is `null` until the week is built; a number (2dp) after.
- `has_run:false` → the week exists on SharePoint but has no run row yet.
- Ordering is guaranteed newest-first by the backend (years, months, weeks) — do
  **not** re-sort; render in received order.

Fetch with TanStack Query, key `['shipping','folders']`. Cache is fine
(`staleTime` ~5 min); expose a manual **refresh** since SharePoint folders and
run statuses change out of band. On error, render the backend `{error}` string.

---

## 3. Data types (TypeScript)

```ts
type ShippingRunStatus = "built" | "needs_review" | "posted" | "error";

type WeekFolderOption = {
  week_folder: string;          // "WE 05.17.2026" — the value emitted
  date: string;                 // "2026-05-17"
  status: ShippingRunStatus | null;
  total: number | null;
  has_run: boolean;
};
type MonthGroup = { month: number; label: string; weeks: WeekFolderOption[] };
type YearGroup  = { year: number; months: MonthGroup[] };
type ShippingFolders = { years: YearGroup[] };
```

Component contract:

```ts
type WeekFolderPickerProps = {
  value: string | null;                     // selected week_folder, or null = "most recent"
  onChange: (weekFolder: string | null) => void;
  disabled?: boolean;                        // e.g. while a run is in flight
};
```

---

## 4. Component — `WeekFolderPicker`

A three-level cascade. Default layout: an **accordion tree** (Year ▸ Month ▸ Week
rows); the picker is compact enough to live inline at the top of the Shipping
screen.

**Structure**
- **"Most recent" row** pinned at the top: selecting it sets `value = null`
  (parent omits `week_folder`, backend picks newest). Show which folder that
  currently resolves to as muted helper text (`years[0].months[0].weeks[0]`),
  e.g. *"Most recent — WE 05.17.2026"*.
- **Year headers** (collapsible). Default: newest year expanded, rest collapsed.
- **Month headers** under each year (`label`, e.g. "May 2026"), collapsible.
  Default: newest month of the expanded year open.
- **Week rows** — the selectable leaves. Each row:
  - **Left:** `week_folder` (or a friendlier `date` formatted as `MMM D, YYYY`).
    Builder's choice which is primary; keep the raw `week_folder` visible since
    it's the identifier.
  - **Right:** a **status chip** (§6) and, when `total != null`, the formatted
    total (`$#,##0.00`, tabular-nums).
  - Selected row is highlighted (accent border/background).

**Selection**
- Clicking a week row → `onChange(week.week_folder)`.
- Clicking "Most recent" → `onChange(null)`.
- The selected `value` should stay visibly marked even if its group is
  collapsed — reflect selection in the collapsed month/year header (e.g. a small
  dot or the selected week's chip).

**Optional compact mode:** if inline space is tight, render as two dependent
selects (Year/Month) + a week `<select>`, or a single searchable combobox over
all weeks (label = `week_folder`, grouped by month). Same `onChange` contract.
Accordion is the default; combobox is a nice-to-have for long histories.

---

## 5. States

- **Loading:** skeleton rows (3–4 shimmer lines). Keep the "Most recent" row
  usable only after data arrives (it needs the resolved folder name).
- **Error:** inline error card with the backend `{error}` and a **Retry** button
  (refetch). Do not block the rest of the Shipping screen.
- **Empty** (`years: []`): message *"No WE folders found on SharePoint."* with a
  refresh button. This usually means a SharePoint/Graph config issue — surface
  plainly, don't fail silently.
- **Disabled** (`disabled` prop true): dim the whole picker and ignore clicks
  (e.g. while `POST /api/shipping/run` is pending).

---

## 6. Status chips (reuse app tokens, §8 of the React spec)

| `status`        | chip label      | token   |
|-----------------|-----------------|---------|
| `posted`        | Posted          | good    |
| `built`         | Ready           | accent  |
| `needs_review`  | Needs review    | warn    |
| `error`         | Error           | bad     |
| `null` (no run) | Not run         | muted   |

Tabular-nums for the total column. Chip styling matches the existing status
chips (billed=good, billable=accent, etc.).

---

## 7. Integration

The Shipping screen owns run state; the picker only chooses a week:

```tsx
const [weekFolder, setWeekFolder] = useState<string | null>(null); // null = most recent
<WeekFolderPicker value={weekFolder} onChange={setWeekFolder} disabled={runPending} />
<button disabled={runPending} onClick={() => runMutation.mutate({ week_folder: weekFolder ?? undefined })}>
  Run week
</button>
```

- `POST /api/shipping/run` body: `{ "week_folder": value }`, or `{}` when `value`
  is `null` (most recent).
- After a successful run/review/post, **invalidate `['shipping','folders']`** so
  the picker's chips/totals refresh (the just-run week flips to Ready/Needs
  review/Posted).
- Deep-link friendly: if the Shipping screen reads `?week_folder=` from the URL,
  initialize `value` from it and expand the matching year/month on mount.

---

## 8. Cross-cutting (inherit from React spec §7–8)

- Loading / empty / error per fetch; render backend `{error}` strings.
- Money `$#,##0.00`, tabular-nums; dates `MMM D, YYYY` for display, but the
  emitted value is always the raw `week_folder` string.
- Theme tokens: `bg #0f1115  panel #181b22  line #272b35  text #e6e8ee
  muted #9aa3b2  accent #4f8cff  good #36b37e  warn #e2b53d  bad #e0556b`.
- No client-side business logic — the picker only displays and selects.

---

## 9. Suggested build order

1. `useShippingFolders()` query hook (`GET /api/shipping/folders`) + types (§3).
2. `WeekFolderPicker` accordion (Year ▸ Month ▸ Week) with status chips + totals.
3. "Most recent" pinned row (emits `null`) + selection highlight across collapse.
4. Wire into the Shipping screen; invalidate the folders query after run/review/post.
5. (optional) Combobox / searchable compact mode for long folder histories.
