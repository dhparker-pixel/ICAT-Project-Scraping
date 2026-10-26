/* ICAT Project Explorer v4 — vanilla JavaScript, no external libraries.
   All filters and statistics derive from the published JSON, never guesses.
   Requires the matching projects-explorer.html and icat-project-explorer.css. */
(() => {
  'use strict';

  function init() {
    const root = document.getElementById('icat-project-explorer');
    if (!root || root.dataset.initialized === 'true') return;
    const el = id => root.querySelector('#' + id);
    const input = el('icat-project-query');
    const suggestionList = el('icat-project-suggestions');
    const count = el('icat-project-count');
    const results = el('icat-project-results');
    const more = el('icat-project-more');
    const error = el('icat-project-error');
    const reset = el('icat-project-reset');
    const advanced = el('icat-project-advanced');
    const activeFilters = el('icat-project-active-filters');
    const stats = el('icat-project-stats');
    const statsValues = el('icat-project-stats-values');
    const statsCategories = el('icat-project-stats-categories');
    const controls = {
      year: el('icat-filter-year'),
      grant: el('icat-filter-grant'),      // multiple category checkboxes
      unit: el('icat-filter-unit'),
      person: el('icat-filter-person'),
      topic: el('icat-filter-topic'),
      sort: el('icat-filter-sort')        // exclusive checkbox-style sorting
    };
    if ([input, suggestionList, count, results, more, error, reset, advanced,
      activeFilters, stats, statsValues, statsCategories, ...Object.values(controls)]
      .some(node => !node)) {
      console.error('ICAT Project Explorer: required v4 HTML elements are missing. Use the matching HTML.');
      return;
    }
    root.dataset.initialized = 'true';

    const PAGE_SIZE = 12;
    const SORT_OPTIONS = [
      ['newest', 'Newest funding year'],
      ['oldest', 'Oldest funding year'],
      ['az', 'Title A–Z'],
      ['za', 'Title Z–A']
    ];
    let projects = [];
    let filtered = [];
    let visibleCount = PAGE_SIZE;
    let suggestions = [];
    let activeSuggestion = -1;
    let candidatePool = [];
    let suggestTimer;
    const norm = value => String(value ?? '').normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase().trim();
    const arr = value => Array.isArray(value)
      ? value.filter(x => typeof x === 'string' && x.trim()) : [];
    const cycles = p => Array.isArray(p.fundingCycles) ? p.fundingCycles : [];
    const years = p => cycles(p).map(c => c && c.year).filter(x => typeof x === 'string' && x.trim());
    const grants = p => cycles(p).map(c => c && c.grantType).filter(x => typeof x === 'string' && x.trim());
    const uniq = values => [...new Set(values.filter(x => typeof x === 'string' && x.trim()))];
    const number = value => value.toLocaleString();

    function text(tag, value, className) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      node.textContent = value;
      return node;
    }

    // On mobile: closed initially, with an accessible native disclosure arrow.
    // On desktop: always expanded. Remember mobile user's last choice on resize.
    const mobile = window.matchMedia('(max-width: 767px)');
    let mobileFiltersOpen = false;
    function syncAdvancedVisibility() {
      advanced.open = mobile.matches ? mobileFiltersOpen : true;
    }
    advanced.addEventListener('toggle', () => {
      if (mobile.matches) mobileFiltersOpen = advanced.open;
    });
    if (typeof mobile.addEventListener === 'function') {
      mobile.addEventListener('change', syncAdvancedVisibility);
    } else if (typeof mobile.addListener === 'function') {
      mobile.addListener(syncAdvancedVisibility);
    }
    syncAdvancedVisibility();

    function setOptions(select, values, emptyLabel, reverse = false) {
      const selected = select.value;
      const items = uniq(values).sort((a, b) => a.localeCompare(b, undefined,
        {sensitivity: 'base', numeric: true}));
      if (reverse) items.reverse();
      select.replaceChildren(new Option(emptyLabel, ''));
      for (const item of items) select.add(new Option(item, item));
      if (items.includes(selected)) select.value = selected;
    }

    function makeCheckboxGroup(container, items, prefix) {
      container.replaceChildren();
      for (const [index, entry] of items.entries()) {
        const [value, labelText] = Array.isArray(entry) ? entry : [entry, entry];
        const label = document.createElement('label');
        label.className = 'icat-projects__check';
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.value = value;
        box.id = `${prefix}-${index}`;
        label.append(box, text('span', labelText));
        container.append(label);
      }
    }

    function checkedValues(container) {
      return [...container.querySelectorAll('input[type="checkbox"]:checked')]
        .map(box => box.value);
    }

    function currentSort() {
      return checkedValues(controls.sort)[0] || 'newest';
    }

    function setupFilters(data) {
      const allYears = uniq([...arr(data.fundingYears), ...projects.flatMap(years)]);
      setOptions(controls.year, allYears, 'All years', true);
      if (projects.some(p => !years(p).length)) {
        controls.year.add(new Option('Year not provided', '__unknown'));
      }
      const allGrants = uniq([...arr(data.grantTypes), ...projects.flatMap(grants)])
        .sort((a, b) => a.localeCompare(b));
      makeCheckboxGroup(controls.grant, allGrants, 'icat-category');
      makeCheckboxGroup(controls.sort, SORT_OPTIONS, 'icat-sort');
      setOptions(controls.unit, [...arr(data.units), ...projects.flatMap(p => arr(p.affiliations))], 'All units');
      setOptions(controls.person, projects.flatMap(p => arr(p.people)), 'All people');
      setOptions(controls.topic, [...arr(data.topicsVocabulary), ...projects.flatMap(p => arr(p.topics))], 'All topics');
    }

    function searchable(p) {
      return norm([p.title, p.description, ...years(p), ...grants(p),
        ...arr(p.people), ...arr(p.affiliations), ...arr(p.topics)].join(' '));
    }

    function matches(p) {
      const query = norm(input.value);
      const selectedGrants = checkedValues(controls.grant);
      return (!query || searchable(p).includes(query)) &&
        (!controls.year.value || (controls.year.value === '__unknown'
          ? !years(p).length : years(p).includes(controls.year.value))) &&
        (!selectedGrants.length || grants(p).some(item => selectedGrants.includes(item))) &&
        (!controls.unit.value || arr(p.affiliations).includes(controls.unit.value)) &&
        (!controls.person.value || arr(p.people).includes(controls.person.value)) &&
        (!controls.topic.value || arr(p.topics).includes(controls.topic.value));
    }

    function sortProjects(a, b) {
      const at = norm(a.title), bt = norm(b.title);
      const mode = currentSort();
      if (mode === 'az') return at.localeCompare(bt);
      if (mode === 'za') return bt.localeCompare(at);
      const ay = years(a).slice().sort().at(-1) || '';
      const by = years(b).slice().sort().at(-1) || '';
      if (!ay && by) return 1;
      if (ay && !by) return -1;
      if (ay !== by) return mode === 'oldest' ? ay.localeCompare(by) : by.localeCompare(ay);
      return at.localeCompare(bt);
    }

    function safeUrl(value) {
      if (typeof value !== 'string') return null;
      try {
        const url = new URL(value, window.location.origin);
        if (url.origin !== window.location.origin ||
            !url.pathname.startsWith('/projects/supported-research/sead-grants/') ||
            !url.pathname.endsWith('.html')) return null;
        return url.pathname;
      } catch (_) { return null; }
    }

    function addDetail(card, title, values, limit = 3) {
      if (!values.length) return;
      const line = text('p', '', 'icat-projects__detail');
      const suffix = values.length > limit ? ` +${values.length - limit} more` : '';
      line.append(text('strong', `${title}: `),
        document.createTextNode(values.slice(0, limit).join(', ') + suffix));
      card.append(line);
    }

    function createCard(p) {
      const card = document.createElement('article');
      card.className = 'icat-projects__card';
      const meta = text('div', '', 'icat-projects__metadata');
      const year = years(p)[0];
      if (year) meta.append(text('span', year));
      for (const kind of uniq(grants(p))) meta.append(text('span', kind));
      card.append(meta);
      const heading = document.createElement('h3');
      const link = safeUrl(p.url);
      if (link) {
        const anchor = document.createElement('a');
        anchor.href = link;
        anchor.textContent = p.title || 'Untitled project';
        heading.append(anchor);
      } else {
        heading.textContent = p.title || 'Untitled project';
      }
      card.append(heading);
      if (typeof p.description === 'string' && p.description.trim()) {
        card.append(text('p', p.description, 'icat-projects__desc'));
      }
      addDetail(card, 'People', arr(p.people));
      addDetail(card, 'Units', arr(p.affiliations));
      addDetail(card, 'Topics', arr(p.topics));
      return card;
    }

    // A quiet statistics footer, derived from ALL matching projects.
    // A project tagged under two grant categories counts once in total projects,
    // but once under each of those categories in the optional breakdown.
    function paintStats() {
      const statsData = [
        [number(filtered.length), 'Projects'],
        [number(uniq(filtered.flatMap(years)).length), 'Funding years'],
        [number(uniq(filtered.flatMap(p => arr(p.people))).length), 'People tagged'],
        [number(uniq(filtered.flatMap(p => arr(p.affiliations))).length), 'Units represented'],
        [number(uniq(filtered.flatMap(p => arr(p.topics))).length), 'Topics tagged']
      ];
      const fragment = document.createDocumentFragment();
      for (const [value, label] of statsData) {
        const item = document.createElement('div');
        item.className = 'icat-projects__stat';
        item.append(text('dt', label), text('dd', value));
        fragment.append(item);
      }
      statsValues.replaceChildren(fragment);
      const categoryCounts = new Map();
      for (const project of filtered) {
        for (const kind of uniq(grants(project))) {
          categoryCounts.set(kind, (categoryCounts.get(kind) || 0) + 1);
        }
      }
      const byCategory = [...categoryCounts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
      statsCategories.textContent = byCategory.length
        ? `Project categories  ·  ${byCategory.map(([type, qty]) => `${type}: ${number(qty)}`).join('  ·  ')}`
        : 'Project categories  ·  None recorded for this selection';
      stats.hidden = false;
    }

    function paint() {
      const fragment = document.createDocumentFragment();
      if (!filtered.length) {
        fragment.append(text('p',
          'No projects match those filters. Try a different search or clear the filters.',
          'icat-projects__empty'));
      } else {
        for (const p of filtered.slice(0, visibleCount)) fragment.append(createCard(p));
      }
      results.replaceChildren(fragment);
      const shown = Math.min(visibleCount, filtered.length);
      count.textContent = `${number(filtered.length)} of ${number(projects.length)} projects · Showing ${number(shown)}`;
      more.hidden = shown >= filtered.length;
      if (!more.hidden) more.textContent = `Show more projects (${number(filtered.length - shown)} remaining)`;
      paintStats();
    }

    function updateActiveCount() {
      const active = Number(Boolean(controls.year.value)) +
        Number(checkedValues(controls.grant).length > 0) +
        Number(Boolean(controls.unit.value)) +
        Number(Boolean(controls.person.value)) +
        Number(Boolean(controls.topic.value));
      activeFilters.textContent = active ? `· ${active} active` : '';
    }

    function update() {
      visibleCount = PAGE_SIZE;
      filtered = projects.filter(matches).sort(sortProjects);
      updateActiveCount();
      paint();
    }

    function closeSuggestions() {
      suggestions = [];
      activeSuggestion = -1;
      suggestionList.hidden = true;
      suggestionList.replaceChildren();
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }

    function candidates() {
      const pool = [];
      for (const p of projects) {
        if (p.title) pool.push({label: p.title, category: 'Project', rank: 0});
        for (const value of arr(p.people)) pool.push({label: value, category: 'Person', rank: 1});
        for (const value of arr(p.affiliations)) pool.push({label: value, category: 'Unit', rank: 2});
        for (const value of arr(p.topics)) pool.push({label: value, category: 'Topic', rank: 3});
      }
      return pool;
    }

    function updateSuggestions() {
      const query = norm(input.value);
      if (query.length < 2 || document.activeElement !== input) {
        closeSuggestions(); return;
      }
      const seen = new Set();
      suggestions = candidatePool.filter(item => norm(item.label).includes(query))
        .sort((a, b) => {
          const qa = norm(a.label), qb = norm(b.label);
          const score = value => value === query ? 0 : value.startsWith(query) ? 1 :
            value.split(/\s+/).some(word => word.startsWith(query)) ? 2 : 3;
          return score(qa) - score(qb) || a.rank - b.rank ||
            a.label.length - b.label.length || a.label.localeCompare(b.label);
        })
        .filter(item => {
          const key = norm(item.label);
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        }).slice(0, 6);
      if (!suggestions.length) { closeSuggestions(); return; }
      activeSuggestion = -1;
      suggestionList.replaceChildren();
      suggestions.forEach((item, index) => {
        const option = text('li', '', 'icat-projects__suggestion');
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', 'false');
        option.id = `icat-project-suggestion-${index}`;
        option.append(text('span', item.label), text('small', item.category));
        option.addEventListener('mousedown', event => event.preventDefault());
        option.addEventListener('click', () => selectSuggestion(index));
        suggestionList.append(option);
      });
      suggestionList.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }

    function setActive(index) {
      if (!suggestions.length) return;
      activeSuggestion = (index + suggestions.length) % suggestions.length;
      [...suggestionList.children].forEach((node, i) =>
        node.setAttribute('aria-selected', i === activeSuggestion ? 'true' : 'false'));
      input.setAttribute('aria-activedescendant', `icat-project-suggestion-${activeSuggestion}`);
      suggestionList.children[activeSuggestion].scrollIntoView({block: 'nearest'});
    }

    function selectSuggestion(index) {
      if (!suggestions[index]) return;
      input.value = suggestions[index].label;
      closeSuggestions();
      update();
      input.focus();
    }

    function synchronizeCheckboxStyles(container) {
      for (const box of container.querySelectorAll('input[type="checkbox"]')) {
        box.closest('label').classList.toggle('is-selected', box.checked);
      }
    }

    input.addEventListener('input', () => {
      update();
      window.clearTimeout(suggestTimer);
      suggestTimer = window.setTimeout(updateSuggestions, 150);
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Escape') { closeSuggestions(); return; }
      if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && !suggestionList.hidden) {
        event.preventDefault();
        setActive(activeSuggestion + (event.key === 'ArrowDown' ? 1 : -1));
      } else if (event.key === 'Enter' && activeSuggestion >= 0 && !suggestionList.hidden) {
        event.preventDefault();
        selectSuggestion(activeSuggestion);
      }
    });
    input.addEventListener('focus', () => {
      if (norm(input.value).length >= 2) updateSuggestions();
    });
    document.addEventListener('pointerdown', event => {
      if (!root.contains(event.target) || !event.target.closest('.icat-projects__suggest-wrap')) closeSuggestions();
    });

    for (const select of [controls.year, controls.unit, controls.person, controls.topic]) {
      select.addEventListener('change', () => { closeSuggestions(); update(); });
    }
    controls.grant.addEventListener('change', event => {
      if (!event.target.matches('input[type="checkbox"]')) return;
      synchronizeCheckboxStyles(controls.grant);
      closeSuggestions();
      update();
    });
    controls.sort.addEventListener('change', event => {
      if (!event.target.matches('input[type="checkbox"]')) return;
      // These are real checkboxes for the requested visual/interaction style,
      // but sorting has one active method at a time.
      if (event.target.checked) {
        for (const box of controls.sort.querySelectorAll('input[type="checkbox"]')) {
          if (box !== event.target) box.checked = false;
        }
      }
      synchronizeCheckboxStyles(controls.sort);
      closeSuggestions();
      update();
    });
    reset.addEventListener('click', () => {
      input.value = '';
      for (const select of [controls.year, controls.unit, controls.person, controls.topic]) select.value = '';
      for (const container of [controls.grant, controls.sort]) {
        for (const box of container.querySelectorAll('input[type="checkbox"]')) box.checked = false;
        synchronizeCheckboxStyles(container);
      }
      closeSuggestions();
      update();
    });
    more.addEventListener('click', () => { visibleCount += PAGE_SIZE; paint(); });

    const jsonPath = root.dataset.json || '/content/dam/icat_vt_edu/projects.json';
    fetch(jsonPath, {credentials: 'same-origin', cache: 'no-store'})
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then(data => {
        if (!data || !Array.isArray(data.projects)) throw new Error('Invalid project JSON schema');
        projects = data.projects.filter(p => p && typeof p === 'object');
        candidatePool = candidates();
        setupFilters(data);
        update();
      })
      .catch(err => {
        console.error('ICAT Project Explorer: failed to load JSON', err);
        count.textContent = 'Project data unavailable';
        error.textContent = 'Projects could not be loaded. Please try again later.';
        error.hidden = false;
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }
})();
