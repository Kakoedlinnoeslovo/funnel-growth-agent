'use strict';
const $ = id => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
let catalog = null, active = null, selected = new Set(), busy = false, uploading = false, signature = '', comparison = 'draft';
const labels = {draft:'Saved draft', generating:'Generating', revising:'Revising', ready:'Ready to review', publishing:'Publishing', published:'Published', failed:'Needs attention', needs_selection:'Saved draft'};
function error(message) { $('error').textContent = message || ''; $('error').hidden = !message; }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
  return result;
}
function remember(id) {
  try { if (id) localStorage.setItem('creative-landing-draft', id); else localStorage.removeItem('creative-landing-draft'); } catch {}
  history.replaceState(null, '', id ? '/?draft=' + id : '/');
}
const format = (value, kind) => value === null || value === undefined ? '—' : kind === 'money' ? '$' + Number(value).toFixed(2) : kind === 'rate' ? (Number(value) * 100).toFixed(2) + '%' : Number(value).toLocaleString();
function metricsGrid(metrics) {
  const grid = el('div','metrics');
  for (const [key,label,kind] of [['spend','Spend','money'],['clicks','Clicks'],['ctr','CTR','rate'],['cpc','CPC','money'],['leads','Meta leads'],['payments','Payments'],['checkouts','Checkouts'],['impressions','Impressions']]) {
    const metric = el('div','metric'); metric.append(el('strong','',format(metrics[key],kind)),el('span','',label)); grid.append(metric);
  }
  return grid;
}
function creativeCard(row, selectable = false) {
  const card = el('article', 'creative-card' + (selectable && selected.has(row.id) ? ' selected' : ''));
  const media = el('div','creative-media');
  if (row.videoUrl && row.source !== 'upload') {
    const video = el('video'); video.src = row.videoUrl; video.controls = true; video.preload = 'none'; video.playsInline = true; if (row.imageUrl) video.poster = row.imageUrl; media.append(video);
  } else if (row.imageUrl) {
    const image = el('img'); image.src = row.imageUrl; image.alt = row.name; image.loading = 'lazy'; media.append(image);
  } else media.append(el('span','','Ad asset unavailable'));
  const info = el('div','creative-info');
  if (selectable) {
    const label = el('label','creative-select'); const input = el('input'); input.type = 'checkbox'; input.checked = selected.has(row.id);
    input.addEventListener('change', () => {
      $('adset').value = '';
      if (input.checked) selected.add(row.id); else selected.delete(row.id);
      card.classList.toggle('selected',input.checked); updateSelection();
    });
    label.append(input,el('span','creative-name',row.name)); info.append(label);
  } else info.append(el('strong','creative-name',row.name));
  info.append(el('p','muted', row.source === 'upload' ? (row.frameSource || 'Uploaded image') : [row.adsetName,row.campaignName].filter(Boolean).join(' · ') || row.id));
  if (row.source === 'upload' && row.videoUrl) {
    const link = el('a','text-link','Open original video ↗'); link.href = row.videoUrl; link.target = '_blank'; link.rel = 'noopener'; info.append(link);
  }
  if (row.title) info.append(el('p','',row.title));
  if (row.body) info.append(el('p','creative-copy muted',row.body));
  if (row.source !== 'upload') info.append(metricsGrid(row.metrics));
  info.append(el('span','signal',row.signal));
  const analysis = !selectable && active?.analyses?.find(item => item.creativeId === row.id);
  if (analysis) {
    info.append(el('p','evidence-label','Visible text'));
    info.append(el('p','small',analysis.model === 'title-body-fallback' ? 'Visual text could not be checked.' : analysis.analysis.visibleText?.join(' · ') || 'No readable text observed.'));
    info.append(el('p','evidence-label','CTA direction · inferred'));
    info.append(el('p','small',analysis.analysis.ctaIntent || 'Unspecified; the page will use supported Recraft context.'));
  }
  for (const warning of row.warnings) info.append(el('div','warning',warning));
  card.append(media,info); return card;
}
function renderCreatives() {
  const query = $('creative-search').value.trim().toLowerCase();
  const rows = catalog.creatives.filter(row => [row.name,row.adsetName,row.campaignName,row.title].join(' ').toLowerCase().includes(query));
  $('creative-list').replaceChildren(...rows.map(row => creativeCard(row,true)));
  if (!rows.length) $('creative-list').append(el('p','empty','No creatives match this search.'));
  updateSelection();
}
function updateSelection() {
  const count = selected.size;
  $('selection-count').textContent = count ? `${count} creative${count === 1 ? '' : 's'} selected` : 'No creatives selected';
  const base = $('baseline').value;
  const selectedNames = catalog.creatives.filter(row => selected.has(row.id)).map(row => row.name);
  $('generate-summary').textContent = base && count ? `${base} + ${count === 1 ? selectedNames[0] : `${count} creatives`} → one new landing` : 'Choose a baseline and creative direction';
  $('generate').disabled = busy || uploading || !base || !count || !catalog.baselines.find(row => row.version === base)?.supported;
  const group = catalog.adsets.find(row => row.id === $('adset').value);
  $('adset-summary').textContent = group ? `${group.creativeIds.length} ads · ${format(group.metrics?.spend,'money')} spend · ${format(group.metrics?.clicks)} clicks · ${format(group.metrics?.checkouts)} checkouts · ${format(group.metrics?.payments)} payments. Totals cover this report’s creatives.` : '';
}
function selectBaseline() {
  const base = catalog.baselines.find(row => row.version === $('baseline').value);
  $('baseline-description').textContent = base ? base.description + (base.supported ? '' : ' Preview only: this baseline has fixed page content.') : 'Your new draft will be an independent copy.';
  $('baseline-open').hidden = !base;
  $('baseline-frame').hidden = !base;
  if (base) {
    const url = catalog.previewBase + '/' + base.version;
    $('baseline-open').href = url;
    // The pre-generation preview points to the existing dev server. Preview builds below
    // use a separate server that blocks checkout, analytics and advertising connections.
    $('baseline-frame').src = url;
  } else $('baseline-frame').removeAttribute('src');
  updateSelection();
}
async function refreshHistory() {
  const result = await api('/api/drafts'); busy = Boolean(result.busy);
  $('new-version').disabled = busy || uploading;
  $('history-list').replaceChildren();
  for (const row of result.drafts) {
    const button = el('button','history-card' + (row.id === active?.id ? ' active' : ''));
    button.append(el('strong','',row.variant),el('span','small',row.creativeNames.join(', ')));
    const status = el('span','small muted'); status.append(el('span','history-dot ' + row.status),document.createTextNode(labels[row.status] || row.status)); button.append(status);
    button.onclick = () => openDraft(row.id).catch(e => error(e.message)); $('history-list').append(button);
  }
  if (!result.drafts.length) $('history-list').append(el('p','small muted','Your first version starts here.'));
}
async function refreshCatalog() {
  catalog = await api('/api/catalog'); busy = Boolean(catalog.busy);
  $('baseline').replaceChildren(new Option('Select a version from the pricing lab',''));
  for (const baseline of catalog.baselines) {
    const option = new Option(baseline.version + (baseline.default ? ' · current default' : '') + (!baseline.supported ? ' · custom renderer' : ''),baseline.version);
    option.title = baseline.limitation || baseline.description; $('baseline').append(option);
  }
  $('adset').replaceChildren(new Option('Select individual creatives below',''));
  for (const group of catalog.adsets) $('adset').append(new Option(`${group.name} · ${group.creativeIds.length} ads · ${group.campaignName}`,group.id));
  const period = catalog.report.period?.current;
  $('report-label').textContent = period ? `${period.start} → ${period.end} · latest weekly report` : catalog.report.title || 'Weekly report unavailable';
  $('catalog-warnings').replaceChildren(...catalog.warnings.map(text => el('p','warning',text)));
  renderCreatives();
}
async function fresh(base = '', ids = []) {
  if (uploading) return;
  active = null; signature = ''; comparison = 'draft'; remember(null); error(''); selected = new Set();
  $('selection-view').hidden = false; $('draft-view').hidden = true; $('creative-search').value = '';
  await Promise.all([refreshCatalog(),refreshHistory()]);
  if (base && catalog.baselines.some(row => row.version === base && row.supported)) $('baseline').value = base;
  selected = new Set(ids.filter(id => catalog.creatives.some(row => row.id === id)));
  selectBaseline(); renderCreatives();
}
async function openDraft(id) {
  error(''); active = await api('/api/drafts/' + id); remember(id); signature = '';
  $('selection-view').hidden = true; $('draft-view').hidden = false;
  renderDraft(); await refreshHistory();
}
function setFrame(frame, url) { if (url && frame.getAttribute('src') !== url) frame.src = url; }
function showComparison(mode) {
  comparison = mode;
  for (const name of ['draft','baseline','compare']) $('show-' + name).setAttribute('aria-pressed',String(name === mode));
  $('baseline-slot').hidden = mode === 'draft'; $('draft-slot').hidden = mode === 'baseline';
  $('open-preview').href = mode === 'baseline' ? active.baselinePreviewUrl : active.previewUrl;
}
function fillCopyFields() {
  $('copy-fields').replaceChildren();
  const sections = active.landing?.sections || [];
  const mappings = [['kittl-hero','hero','Hero'],['hero-carousel','hero','Hero slides'],['quiz-hero','hero','Quiz opening'],['video-cta','videoCta','Video CTA'],['inline-cta','inlineCta','Inline CTAs'],['final-cta','finalCta','Final CTA']];
  for (const [component,key,title] of mappings) {
    const section = sections.find(row => row.component === component); if (!section) continue;
    const fieldset = el('fieldset','copy-section'); fieldset.append(el('legend','',title));
    for (const [field,label] of [['headline','Headline'],['subhead','Supporting copy'],['ctaLabel','CTA label'],['reassurance','Reassurance']]) {
      if (section[field] === undefined) continue;
      const id = 'copy-' + key + '-' + field; const labelNode = el('label','',label); labelNode.htmlFor = id;
      const input = el(field === 'ctaLabel' ? 'input' : 'textarea'); input.id = id; input.dataset.section = key; input.dataset.field = field;
      input.dataset.lines = Array.isArray(section[field]) ? 'true' : 'false';
      input.value = Array.isArray(section[field]) ? section[field].join('\n') : section[field]; input.dataset.original = input.value;
      input.required = true; input.maxLength = 5000; if (input.tagName === 'TEXTAREA') input.rows = 2;
      fieldset.append(labelNode,input);
    }
    $('copy-fields').append(fieldset);
  }
}
function renderContext() {
  $('context').replaceChildren();
  for (const warning of [...active.warnings,...active.creatives.flatMap(row => row.warnings)]) $('context').append(el('p','warning',warning));
  const period = active.report.period?.current;
  $('context').append(el('p','small muted',period ? `Observed report period: ${period.start} → ${period.end}` : active.report.title || 'Report period unavailable'));
  for (const source of active.context?.competitors || []) {
    const entry = el('div','context-entry');
    const link = el('a','text-link',source.url || 'Competitor reference'); link.href = source.url; link.target = '_blank'; link.rel = 'noopener'; entry.append(link);
    const read = source.read;
    if (source.error || !read) entry.append(el('p','warning',source.error || 'No reference analysis available.'));
    else {
      if (read.heroHeadline) entry.append(el('p','',read.heroHeadline));
      if (read.primaryCta) entry.append(el('p','muted','Primary CTA: ' + read.primaryCta));
      for (const observation of [...(read.notablePatterns || []),...(read.phoneDifferences || [])]) entry.append(el('p','',observation));
    }
    $('context').append(entry);
  }
  const media = active.context?.media;
  if (media) {
    const entry = el('div','context-entry'); entry.append(el('strong','','YouTube context'));
    for (const video of media.youtube || []) {
      const p = el('p'); const a = el('a','text-link',video.title || video.videoId); a.href = 'https://www.youtube.com/watch?v=' + encodeURIComponent(video.videoId); a.target = '_blank'; a.rel = 'noopener'; p.append(a); entry.append(p);
    }
    $('context').append(entry);
  }
}
function renderDraft() {
  const sig = [active.id,active.updatedAt,active.status,active.events.length,active.readyRevision,active.busy].join(':');
  if (sig === signature) return; signature = sig;
  busy = Boolean(active.busy); $('new-version').disabled = busy || uploading;
  const operating = ['generating','revising','publishing'].includes(active.status);
  $('draft-kicker').textContent = active.status === 'published' ? 'PUBLISHED LANDING' : 'CREATIVE-TO-LANDING DRAFT';
  $('draft-title').textContent = active.variant;
  $('draft-meta').textContent = `Based on ${active.baseVersion} · ${active.creatives.length} creative${active.creatives.length === 1 ? '' : 's'} · ${active.readyRevision ? 'revision ' + active.readyRevision : 'first draft'}`;
  $('draft-status').textContent = labels[active.status] || active.status;
  const notice = (active.status !== 'needs_selection' && active.error) || active.publicationNotice || (active.readyRevision && !active.publicUrl && !catalog?.publishingConfigured ? (catalog?.publishingProvider === 'github' ? 'GitHub publishing needs the GitHub CLI signed in and the public site address configured.' : 'Public publishing needs a linked Vercel project or the existing GitHub deployment configured. Your draft and previews are saved.') : '');
  $('draft-notice').hidden = !notice; $('draft-notice').textContent = notice;
  $('draft-progress').hidden = !operating;
  const last = active.events.at(-1);
  $('progress-label').textContent = last?.data?.message || (last?.kind === 'tile_candidate' ? 'Generating image candidates' : labels[active.status]);
  $('retry').hidden = active.status !== 'failed' || !active.retry;
  $('generate-saved').hidden = !['draft','needs_selection'].includes(active.status);
  $('generate-saved').disabled = busy;
  $('retry').disabled = busy;
  $('publish').hidden = !active.readyRevision || operating || active.status === 'published' || Boolean(active.publicUrl);
  $('publish').disabled = busy;
  const github = catalog?.publishingProvider === 'github';
  const prepared = active.revisions.find(row => row.number === active.readyRevision)?.publicationPrepared;
  $('publish').textContent = github && !prepared ? 'Prepare publishing preview →' : 'Publish public version ↗';
  $('refresh-publication').hidden = !github || !prepared || operating || active.status === 'published' || Boolean(active.publicUrl);
  $('refresh-publication').disabled = busy;
  $('public-link').hidden = !active.publicUrl; if (active.publicUrl) $('public-link').href = active.publicUrl;
  $('use-baseline').hidden = active.status !== 'published';
  $('preview-area').hidden = !active.previewUrl;
  if (active.previewUrl) {
    setFrame($('draft-frame'),active.previewUrl); setFrame($('compare-frame'),active.baselinePreviewUrl); showComparison(comparison);
  }
  $('selected-creatives').replaceChildren(...active.creatives.map(row => creativeCard(row)));
  $('selection-review').replaceChildren();
  if (active.selectionReview) {
    $('selection-review').append(el('p','evidence-label','Creative direction'),el('p','',active.selectionReview.commonPromise),el('p','small muted',active.selectionReview.reason));
  }
  $('rationale').replaceChildren();
  if (active.proposal) {
    $('rationale').append(el('p','',active.proposal.problem),el('div','evidence-label','Hypothesis · to be measured'),el('p','',active.proposal.hypothesis));
    const list = el('ul'); for (const item of active.proposal.evidence || []) list.append(el('li','small',item)); $('rationale').append(list);
  } else $('rationale').append(el('p','muted','The page’s creative direction and supporting evidence will appear here.'));
  renderContext();
  $('editing-area').hidden = !active.readyRevision || operating || active.status === 'published' || Boolean(active.publicUrl) || Boolean(active.githubPublicationPending);
  if (!$('editing-area').hidden) fillCopyFields();
  $('revision-history').replaceChildren();
  for (const revision of active.revisions.slice().reverse()) {
    const row = el('div','revision-row'); row.append(el('strong','',`Revision ${revision.number} · ${revision.status}`));
    row.append(el('div','muted',revision.input.publicationRefresh ? 'Preview refreshed for GitHub publication' : revision.input.instruction || (revision.input.copy ? 'Direct copy edits' : 'Initial generation')));
    if (revision.status === 'ready') { const a = el('a','text-link','Open saved preview ↗'); a.href = `/api/drafts/${active.id}/preview/${revision.number}`; a.target = '_blank'; a.rel = 'noopener'; row.append(a); }
    if (revision.error) row.append(el('p','warning',revision.error)); $('revision-history').append(row);
  }
  $('activity').replaceChildren(...active.events.slice(-50).reverse().map(event => el('div','activity-row',`${event.at.slice(11,19)} · ${event.kind.replaceAll('_',' ')}${event.data.message ? ' · ' + event.data.message : ''}`)));
}
async function action(name,payload = {}) {
  error(''); await api(`/api/drafts/${active.id}/${name}`,payload); signature = ''; active = await api('/api/drafts/' + active.id); renderDraft(); await refreshHistory();
}
function uploadFile(file, row) {
  return new Promise((resolve,reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST','/api/uploads');
    xhr.setRequestHeader('Content-Type',file.type || 'application/octet-stream');
    xhr.setRequestHeader('X-File-Name',encodeURIComponent(file.name));
    xhr.timeout = 180000;
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) row.textContent = `${file.name} · ${Math.round(event.loaded / event.total * 100)}% uploaded · preparing preview…`;
    };
    xhr.onload = () => {
      let result; try { result = JSON.parse(xhr.responseText); } catch { reject(new Error('Upload returned an unreadable response.')); return; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(result);
      else reject(new Error(result.error || 'Upload failed.'));
    };
    xhr.onerror = () => reject(new Error('Connection interrupted. Choose the file again.'));
    xhr.ontimeout = () => reject(new Error('Upload timed out. Choose the file again.'));
    xhr.send(file);
  });
}
async function uploadFiles(files) {
  if (uploading || busy || !catalog) return;
  const queue = Array.from(files); if (!queue.length) return;
  uploading = true; $('upload-files').disabled = true; $('new-version').disabled = true;
  $('upload-results').replaceChildren(); updateSelection();
  try {
    for (const file of queue) {
      const row = el('p','small',`${file.name} · uploading…`); $('upload-results').append(row);
      try {
        const suffix = file.name.split('.').at(-1).toLowerCase();
        const isImage = ['jpg','jpeg','png','webp'].includes(suffix);
        if (!isImage && !['mp4','mov','webm'].includes(suffix)) throw new Error('Choose a JPEG, PNG, WebP, MP4, MOV, or WebM file.');
        if (!file.size) throw new Error('This file is empty.');
        if (file.size > (isImage ? 20 : 100) * 1024 * 1024) throw new Error(`File exceeds the ${isImage ? 20 : 100} MiB limit.`);
        const creative = await uploadFile(file,row);
        catalog.creatives.unshift(creative); selected.add(creative.id); $('adset').value = '';
        $('creative-search').value = ''; renderCreatives();
        row.textContent = `${file.name} · ready and selected`;
      } catch(e) { row.classList.add('warning'); row.textContent = `${file.name} · ${e.message}`; }
    }
  } finally {
    uploading = false; $('upload-files').disabled = false; $('upload-files').value = '';
    $('new-version').disabled = busy; updateSelection();
  }
}
$('upload-files').addEventListener('change',event => uploadFiles(event.target.files));
for (const name of ['dragenter','dragover']) $('upload-zone').addEventListener(name,event => { event.preventDefault(); $('upload-zone').classList.add('dragging'); });
for (const name of ['dragleave','drop']) $('upload-zone').addEventListener(name,event => { event.preventDefault(); $('upload-zone').classList.remove('dragging'); });
$('upload-zone').addEventListener('drop',event => uploadFiles(event.dataTransfer.files));
$('baseline').addEventListener('change',selectBaseline);
$('creative-search').addEventListener('input',renderCreatives);
$('adset').addEventListener('change',() => {
  const group = catalog.adsets.find(row => row.id === $('adset').value);
  const uploads = catalog.creatives.filter(row => row.source === 'upload' && selected.has(row.id)).map(row => row.id);
  selected = new Set([...uploads,...(group?.creativeIds || [])]);
  if (uploads.length) $('adset').value = ''; // Submit the expanded mixed selection.
  $('creative-search').value = ''; renderCreatives();
});
$('clear-selection').onclick = () => { selected.clear(); $('adset').value = ''; renderCreatives(); };
$('new-version').onclick = () => fresh().catch(e => error(e.message));
$('generate').onclick = async () => {
  $('generate').disabled = true; $('generate').textContent = 'Saving draft…'; $('new-version').disabled = true; error('');
  try {
    const baseline = catalog.baselines.find(row => row.version === $('baseline').value);
    const adsetId = $('adset').value || null;
    const draft = await api('/api/drafts',{baseVersion:baseline.version,baseHash:baseline.hash,reportToken:catalog.reportToken,creativeIds:adsetId ? [] : [...selected],adsetId});
    await openDraft(draft.id); await action('generate');
  } catch(e) { error(e.message); } finally { $('generate').textContent = 'Generate landing →'; if (!active) { $('new-version').disabled = busy; updateSelection(); } }
};
$('retry').onclick = () => action('retry').catch(e => error(e.message));
$('generate-saved').onclick = () => action('generate').catch(e => error(e.message));
$('publish').onclick = () => action('publish',{expectedRevision:active.readyRevision}).catch(e => error(e.message));
$('refresh-publication').onclick = () => action('prepare_publish',{expectedRevision:active.readyRevision}).catch(e => error(e.message));
$('use-baseline').onclick = () => fresh(active.variant).catch(e => error(e.message));
$('instruction-form').onsubmit = async event => { event.preventDefault(); try { await action('revise',{instruction:$('revision-instruction').value,expectedRevision:active.readyRevision}); $('revision-instruction').value = ''; } catch(e) { error(e.message); } };
$('copy-form').onsubmit = async event => {
  event.preventDefault(); const copy = {};
  for (const input of $('copy-fields').querySelectorAll('input,textarea')) {
    if (input.value === input.dataset.original) continue;
    const value = input.value.trim(); if (!value) { error('Copy fields cannot be empty.'); return; }
    (copy[input.dataset.section] ||= {})[input.dataset.field] = input.dataset.lines === 'true' ? value.split('\n').filter(Boolean) : value;
  }
  if (!Object.keys(copy).length) { error('Change a copy field before saving.'); return; }
  try { await action('revise',{copy,expectedRevision:active.readyRevision}); } catch(e) { error(e.message); }
};
for (const mode of ['draft','baseline','compare']) $('show-' + mode).onclick = () => showComparison(mode);
for (const mode of ['desktop','mobile']) $(mode).onclick = () => { $('preview-frames').classList.toggle('mobile',mode === 'mobile'); for (const item of ['desktop','mobile']) $(item).setAttribute('aria-pressed',String(item === mode)); };
let polling = false;
setInterval(async () => {
  if (polling || !active || (!busy && !['generating','revising','publishing'].includes(active.status))) return;
  polling = true;
  try { const id = active.id; const next = await api('/api/drafts/' + id); if (active?.id !== id) return; active = next; renderDraft(); if (!['generating','revising','publishing'].includes(active.status)) await refreshHistory(); }
  catch(e) { error('Connection interrupted. Your saved draft will reload when the demo reconnects.'); }
  finally { polling = false; }
},1500);
(async () => {
  let id = new URLSearchParams(location.search).get('draft');
  try { id ||= localStorage.getItem('creative-landing-draft'); } catch {}
  await Promise.all([refreshCatalog(),refreshHistory()]);
  if (id) { try { await openDraft(id); } catch { await fresh(); } }
})().catch(e => error(e.message));
