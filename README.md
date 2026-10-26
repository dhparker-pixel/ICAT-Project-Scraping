# ICAT Project Explorer v4 — Frontend Update

This updates **only the frontend**. Keep using your existing v3 Python scraper and the existing JSON schema.

## Files

- `projects-explorer.html` — complete HTML snippet; replaces v3 markup
- `icat-project-explorer.css` — complete stylesheet; replaces v3 stylesheet
- `icat-project-explorer.js` — complete JavaScript; replaces v3 JavaScript

**Deploy all three v4 files together.** The new JavaScript depends on the updated HTML IDs for checkboxes, the mobile filter disclosure, and statistics.

## Features

1. **Project category:** multiple selectable checkboxes generated from `grantTypes` and `fundingCycles[].grantType`. Within this group, checked categories use OR; categories combine with other filters using AND.
2. **Sort projects:** exclusive checkboxes, so only one sort can be applied. If none are checked, default is newest funding year. Unchecking the active sort returns to that default.
3. **Mobile filters:** at 767px or less, everything in the advanced filter controls is collapsed below the main search input, behind a native details/summary arrow. The project results still appear. On desktop, the controls are always open. The mobile choice is remembered while resizing the page.
4. **Project statistics:** a quiet strip beneath results shows counts across **all matching projects**, even when only 12 cards are currently rendered: projects, distinct funding years, unique tagged people, unique tagged units, unique tagged topics, and category breakdown. Values update as visitors filter.
5. **Existing features retained:** autocomplete, person/unit/topic/year dropdowns, Show more pagination, reset button, text-only DOM rendering, and restricted project links.

## Ensemble setup

1. In a test/staging page, replace the entire v3 HTML snippet with `projects-explorer.html`.
2. Upload the new CSS and JS to your approved Ensemble asset location, replacing or versioning the earlier assets. Ensure the page references the **v4 files** rather than cached v3 versions. A version query suffix (`?v=4`) can help invalidate cached assets if permitted by your existing loading mechanism.
3. Keep the section attribute `data-json="/content/dam/icat_vt_edu/projects.json"` or point it to your staging JSON on the same origin.
4. Test category multiselect, each sort option, reset, search suggestions, mobile expansion and collapsing, and filter statistics. Test small mobile screens for horizontal overflow. Test keyboard-only controls and visible focus; check Chrome, Firefox and Safari.
5. Publish after verifying the full scraper-produced JSON and its validator report.

No extra libraries or API keys are required. The browser loads the existing JSON once and performs all filtering and aggregation locally.

## Notes

- Statistics count **distinct literal tags** exactly as stored; differences in capitalization or aliases can lead to separate counts. They aren't claims about unique employees or verified affiliations.
- Category breakdowns count projects with each category. Multi-cycle records may appear in more than one category, so the breakdown need not sum to the project total.
- Yearless Rapid Response / Miscellaneous projects are counted in the total but not in the distinct year count, and they remain available through the "Year not provided" option.
- This version hasn't yet been tested inside your particular Ensemble component. Test in staging before deploying publicly.
