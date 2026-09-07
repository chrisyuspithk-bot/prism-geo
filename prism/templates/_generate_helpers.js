function appendToPrompt(text) {
  const ta = document.getElementById('prompt-input');
  ta.value = ta.value ? ta.value + ', ' + text : text;
  ta.focus();
}

let selectedTrendKeywords = new Set();

async function generateKeywords() {
  const btn = document.getElementById('kw-btn');
  const chips = document.getElementById('kw-chips');
  btn.disabled = true;
  btn.textContent = 'Generating…';

  try {
    const resp = await fetch('/api/generate-keywords', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({site_id: {{ site.id if site else 'null' }}})
    });
    const data = await resp.json();

    if (data.error) {
      chips.innerHTML = '<span class="text-xs text-red-500">' + data.error + '</span>';
      chips.classList.remove('hidden');
      return;
    }

    chips.innerHTML = '';
    (data.keywords || []).forEach(kw => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100 hover:border-indigo-300 transition-colors';
      chip.textContent = kw;
      chip.onclick = function() { appendToPrompt(kw); };
      chips.appendChild(chip);
    });
    chips.classList.remove('hidden');
  } catch(err) {
    chips.innerHTML = '<span class="text-xs text-red-500">Error: ' + err.message + '</span>';
    chips.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = '✨ 10 Keywords';
  }
}

async function fetchTrendKeywords() {
  const btn = document.getElementById('trend-btn');
  const results = document.getElementById('trend-results');
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '搜尋趨勢中…';

  try {
    const resp = await fetch('/api/key-area-keywords', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})
    });
    const data = await resp.json();

    if (data.error) {
      results.innerHTML = '<span class="text-xs text-red-500">' + data.error + '</span>';
      results.classList.remove('hidden');
      return;
    }

    selectedTrendKeywords.clear();
    results.innerHTML = '';

    (data.areas || []).forEach(area => {
      const group = document.createElement('div');
      group.className = 'mb-3';
      const label = document.createElement('div');
      label.className = 'text-xs font-semibold text-slate-500 mb-1';
      label.textContent = area.area;
      group.appendChild(label);

      const wrap = document.createElement('div');
      wrap.className = 'flex flex-wrap gap-2';
      (area.keywords || []).forEach(kw => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'trend-chip rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600 hover:border-indigo-300 transition-colors';
        chip.textContent = kw;
        chip.onclick = function() { toggleTrendKeyword(chip, kw); };
        wrap.appendChild(chip);
      });
      group.appendChild(wrap);
      results.appendChild(group);
    });

    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'mt-1 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700 transition-colors';
    addBtn.textContent = '{{ t('add_selected') }}';
    addBtn.onclick = addSelectedToPrompt;
    results.appendChild(addBtn);

    results.classList.remove('hidden');
  } catch(err) {
    results.innerHTML = '<span class="text-xs text-red-500">Error: ' + err.message + '</span>';
    results.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

function toggleTrendKeyword(chip, kw) {
  if (selectedTrendKeywords.has(kw)) {
    selectedTrendKeywords.delete(kw);
    chip.className = 'trend-chip rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600 hover:border-indigo-300 transition-colors';
  } else {
    selectedTrendKeywords.add(kw);
    chip.className = 'trend-chip rounded-full border border-indigo-300 bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700 transition-colors';
  }
}

function addSelectedToPrompt() {
  const kws = Array.from(selectedTrendKeywords);
  if (!kws.length) return;
  const ta = document.getElementById('prompt-input');
  ta.value = ta.value ? ta.value + ', ' + kws.join(', ') : kws.join(', ');
  ta.focus();
}

async function fetchHkTrending() {
  const btn = document.getElementById('hk-trend-btn');
  const box = document.getElementById('hk-trend-chips');
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '搜尋中…';

  try {
    const resp = await fetch('/api/trending-hk', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})
    });
    const data = await resp.json();

    if (data.error) {
      box.innerHTML = '<span class="text-xs text-red-500">' + data.error + '</span>';
      box.classList.remove('hidden');
      return;
    }

    box.innerHTML = '';
    (data.keywords || []).forEach(kw => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100 hover:border-amber-300 transition-colors';
      chip.textContent = kw;
      chip.onclick = function() { appendToPrompt(kw); };
      box.appendChild(chip);
    });
    box.classList.remove('hidden');
  } catch(err) {
    box.innerHTML = '<span class="text-xs text-red-500">Error: ' + err.message + '</span>';
    box.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}
