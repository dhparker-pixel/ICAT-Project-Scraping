# ICAT supported research: scraper + project explorer (v3)

This bundle expands the previous ICAT JSON scraper and provides a **standalone, accessible frontend implementation** for the same JSON. It uses publicly available Ensemble page metadata and tags. It does not create unsupported people, units, years, or topics.

## 1. Install and run the Python scraper

In the directory containing `icat_sead_scraper.py`:

```bash
python3 -m pip install requests beautifulsoup4 playwright
python3 -m playwright install chromium
```

**All published project categories** (Major, Mini, Student, Rapid Response, and Miscellaneous):

```bash
python3 icat_sead_scraper.py --all
```

**One funding year only** (e.g., projects in URLs beginning with `.../2024-2025/`):

```bash
python3 icat_sead_scraper.py --year 2024-2025
```

**Refresh just one year inside an existing complete master JSON** (preserve all other years and undated projects):

```bash
python3 icat_sead_scraper.py --year 2024-2025 --merge-baseline --baseline ./projects.json
```

Important: First download/save your current production `projects.json` as `./projects.json`. The merge refuses to run without a valid nonempty baseline; it also refuses to replace any portion of the baseline if the selected year's scrape is incomplete. Make a backup before publishing any changes.

**Optional explicit project count**: `--expected-count N` compares the number of records scraped by *this run* against N. **Do not reuse `--expected-count 236` for the expanded all-projects mode**, because Rapid Response and Miscellaneous add projects beyond the original SEAD listing count. The script still checks that it has collected at least the SEAD listing's reported number of regular Major/Mini/Student projects (where available) and logs the separate special-archive counts.

If the regular list cannot be exhausted, try:

```bash
python3 icat_sead_scraper.py --all --browser always
```

### Output files

| Mode | JSON (on a successful crawl) | Log |
| --- | --- | --- |
| Full archive | `projects.generated.json` | `projects.scrape.log` |
| `--year 2024-2025` | `projects.2024-2025.json` | `projects.2024-2025.scrape.log` |
| `--year 2024-2025 --merge-baseline` | `projects.merged.2024-2025.json` | `projects.2024-2025.scrape.log` |

If the crawl has known failures, the JSON is named `*.INCOMPLETE.json` instead, with a nonzero exit status. **Do not deploy an incomplete file.** `--allow-incomplete` can force output for debugging, but never use it for production. For `--merge-baseline`, a known-incomplete update is refused even with that option.

### Data and provenance rules

- `id`: existing ID for the exact same published URL, if available; otherwise a deterministic slug (with collision protection).
- `title`: page's published H1.
- `url`: published same-site project path.
- `description`: excerpt from the first substantive paragraph after the H1 (never AI-generated).
- `fundingCycles[].grantType`: Major/Mini/Student from explicit grant tag and corresponding URL; Rapid Response from its dedicated published project directory; Miscellaneous from the site's `misc` archive category **(a grouping, not proof of a grant award)**.
- `fundingCycles[].year`: the exact `YYYY-YYYY` in a regular grant URL. For `rapid-response` and `misc`, it is **an empty string**. **Publication dates are not substituted for funding years.**
- `people`: only tag labels in the `People` namespace.
- `affiliations`: only tag labels in the `Department-School` and `ICAT-Center` namespaces.
- `topics`: only classified topic tags already supported by the preceding scraper.
- `grantTypes`, `fundingYears`, `topicsVocabulary`, `units`: rebuilt automatically from actual project records for the new frontend dropdowns.

Unmapped tags, conflicts, failed links, missing data, and undated special-category projects are written to the log. The original schema remains compatible with the existing `projects.json` fields, including `schemaVersion: 1`.

### Coverage note

The previous user's count of 236 referred to the regular SEAD listing. Expanding scope changes the total; **the correct new all-project count has not yet been verified with a complete live run.** Local regression tests validate parsing and discovery with fixtures but cannot prove that every active Ensemble page is discoverable. Verify log lines beginning `GRANT_TYPE_COUNT` and `SUMMARY` after running, then review the warnings.

For a single-year crawl, undated `rapid-response` and `misc` pages are intentionally excluded. They remain included in all-project mode and are retained untouched in a year merge.

## 2. Integrate the project explorer

Files:

- `projects-explorer.html`: HTML markup for the Explorer section (a standalone snippet).
- `icat-project-explorer.css`: scoped, responsive styles.
- `icat-project-explorer.js`: plain JavaScript with dropdown filters, sorting, result cards, and quiet autocomplete.

### Ensemble deployment

1. Put the HTML markup in the appropriate supported custom markup area for your Ensemble site. If Ensemble sanitizes that component, use your normal approved mechanism for placing HTML.
2. Upload `icat-project-explorer.css` and `icat-project-explorer.js` to your approved site asset location. Include them through your existing approved stylesheet/script loading arrangement. The package does **not** assume inline `<script>` execution is allowed in Ensemble.
3. Keep `data-json="/content/dam/icat_vt_edu/projects.json"` on the outer section if using the published JSON. To preview a new file, point `data-json` at the new JSON **on the same origin** before replacing the master.
4. Disable any old JavaScript that also mounts results into `#icat-project-results`; do not run two renderers against the same element.
5. After testing in a draft or staging page, check keyboard navigation, mobile stacking, color contrast, reduced-motion settings, filtering combinations, and missing-year records in production Chrome, Firefox, and Safari.

The frontend reads the top-level JSON vocabularies **and** extracts values from every project's `fundingCycles`, `people`, `affiliations`, and `topics`; future data additions appear in dropdown options without hardcoded changes. It will list "Year not provided" if undated projects are present. All filters combine with AND, sorting is stable, and records can be revealed 12 at a time.

### Autocomplete behavior

- Appears after two typed characters, with 150 ms debounce, maximum six suggestions.
- Gives priority to project titles, then people, units, and topics.
- Never selects a value automatically or changes the search until the user chooses it.
- Supports up/down arrows, Enter, Escape, pointer/touch selection, visible focus outlines and descriptive listbox roles.
- Suggestion list is discreet and does not cover content until the user starts typing.
- Suggestions are local to the already downloaded JSON—**no external API, tracking, or AI calls**.
- Results and text are rendered using DOM `textContent`, rather than injecting HTML from JSON.

### Existing frontend compatibility

This is a ready-to-test **reference implementation**, **not** a patch against your unknown current JavaScript/HTML. Your current frontend source wasn't supplied in this conversation. To preserve your exact existing design, selectors, event behavior, and Ensemble placement, compare or integrate the relevant pieces rather than blindly replacing your live page.

## 3. Tests

Run all 16 local unit tests:

```bash
python3 -m unittest -v test_icat_sead_scraper.py test_icat_projects_v3.py
```

Check the JavaScript syntax if Node.js is installed:

```bash
node --check icat-project-explorer.js
```

Version 3 has passed those tests. **A full live scrape and production-browser integration test have not been performed in this environment.**
