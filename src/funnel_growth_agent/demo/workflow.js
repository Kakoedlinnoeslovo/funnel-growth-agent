'use strict';

const $ = id => document.getElementById(id);
const el = (tag, className = '', text) => {
  const node = document.createElement(tag); if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text); return node;
};
const labels = {draft:'Draft',awaiting_direction:'Choose a direction',generating:'Generating',researching:'Researching',revising:'Revising',ready:'Ready',publishing:'Publishing',published:'Published',failed:'Needs attention',needs_selection:'Draft'};
const stageLabels = {understand:'Understanding creatives',research:'Researching references',design:'Designing the page',assets:'Creating assets',build:'Building & checking',publish:'Preparing publication'};
const operationStates = new Set(['generating','researching','revising','publishing']);
const PAGE_SIZE = 6;
let catalog = null, active = null, busy = false, uploading = false, sending = false, selected = new Set();
let creativePage = 0, comparison = 'baseline', viewport = 'desktop', previewRevision = null, resultsMode = 'preview';
let stream = null, streamLive = false, eventCursor = 0, snapshotPending = false, snapshotTimer = null, activeRequest = 0;
let editorKey = '', editorSection = '', editorChanges = {}, editorOrder = [], editorOmit = [];
let videoQuery = '', videoKind = '', mobileTab = 'chat', lastDirectionSet = null;
const contentSignatures = new Map(), feedNodes = new Map(), savedViews = new Map();
const format = (value, kind) => value === null || value === undefined ? '—' : kind === 'money' ? '$' + Number(value).toFixed(2) : kind === 'rate' ? (Number(value) * 100).toFixed(2) + '%' : Number(value).toLocaleString();
const operating = () => active && operationStates.has(active.status);
function error(message) { $('error').textContent = friendlyError(message); $('error').hidden = !message; }
function connection(state, text) { $('connection').dataset.state = state; $('connection').textContent = text; }
function friendlyError(value) {
  const text = String(value || '');
  if (/prepay|credits.*deplet|402.*RESOURCE_EXHAUSTED/i.test(text)) return 'AI analysis is unavailable because provider credits are depleted. Saved captures and your preview are still available.';
  if (/429|rate.limit|too many requests/i.test(text)) return 'The provider is busy. Your work is saved; try again shortly.';
  if (/timed? ?out|timeout/i.test(text)) return 'The request took too long. Your work is saved; you can retry.';
  if (/ANTHROPIC_API_KEY/i.test(text)) return 'Connect Claude in the workspace configuration to chat. Your draft is saved.';
  if (/401|authentication|invalid.*api.?key/i.test(text)) return 'The provider connection needs attention. Check the workspace configuration, then retry.';
  if (text.length > 240 || /Traceback|ClientError|\{'error'|"error"\s*:/.test(text)) return 'This step could not finish. Your work is saved. Open Activity for technical details.';
  return text;
}
function safeLink(value) { if (!value || typeof value !== 'string') return null; try { const url = new URL(value,location.origin); return ['http:','https:'].includes(url.protocol) ? url.href : null; } catch { return null; } }
function assetUrl(value) { const url = safeLink(value); if (!url) return null; const parsed = new URL(url); return parsed.origin === location.origin && parsed.pathname.startsWith('/api/assets/') ? url : null; }
function link(label,url,className = 'text-link') { const target = safeLink(url); if (!target) return el('span','muted',label); const node = el('a',className,label); node.href = target; node.target = '_blank'; node.rel = 'noopener noreferrer'; return node; }
function imageNode(url,alt,className = '') { const safe = assetUrl(url); if (!safe) return null; const node = el('img',className); node.src = safe; node.alt = alt || ''; node.loading = 'lazy'; return node; }
function cleanDetails(value,depth = 0) {
  if (depth > 10) return '[nested details]';
  if (Array.isArray(value)) return value.map(item => cleanDetails(item,depth + 1));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key,item]) => [key,/api.?key|authorization|password|secret|token/i.test(key) ? '[redacted]' : cleanDetails(item,depth + 1)]));
  if (typeof value === 'string') return value.replace(/(?:sk-ant-|sk-)[A-Za-z0-9_-]{10,}/g,'[redacted]').replace(/\/(?:Users|home|private|tmp)\/[^\s"']+/g,'[local artifact]');
  return value;
}
async function api(path,data) {
  const response = await fetch(path,data === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  let result; try { result = await response.json(); } catch { throw new Error('The workspace returned an unreadable response. Please retry.'); }
  if (!response.ok) { const failure = new Error(result.error || 'The request could not be completed.'); failure.status = response.status; throw failure; } return result;
}
function remember(id) { try { if (id) localStorage.setItem('creative-landing-draft',id); else localStorage.removeItem('creative-landing-draft'); } catch {} history.replaceState(null,'',id ? '/?draft=' + id : '/'); }
function cacheInput() { try { sessionStorage.setItem('growth-input-' + (active?.id || 'new'),$('goal-prompt').value); } catch {} }
function restoreInput() { try { $('goal-prompt').value = sessionStorage.getItem('growth-input-' + (active?.id || 'new')) || ''; } catch { $('goal-prompt').value = ''; } }
function rememberView() { cacheInput(); savedViews.set(active?.id || 'new',{scroll:$('chat-scroll').scrollTop,comparison,previewRevision,viewport,mobileTab}); }
function button(text,fn,className = 'small-button') { const node = el('button',className,text); node.type = 'button'; node.onclick = fn; return node; }
function guarded(fn) { return () => Promise.resolve().then(fn).catch(e => error(e.message)); }
function hostLabel(url) { try { return new URL(url).hostname.replace(/^www\./,''); } catch { return 'Reference'; } }
function firstSentence(text,max = 150) { const value = String(text || '').trim(); const first = value.match(/^.*?[.!?](?:\s|$)/)?.[0]?.trim() || value; return first.length > max ? first.slice(0,max).replace(/\s+\S*$/,'') + '…' : first; }
function fact(host,title,value) { if (!value?.length) return; const row = el('div','labeled-fact'); row.append(el('span','',title),el('p','',Array.isArray(value) ? value.join(' · ') : value)); host.append(row); }
function disclosure(title,render,subtext = '') {
  const root = el('details','inline-card'), summary = el('summary'), label = el('span','',title);
  root.dataset.card = title.startsWith('Research ·') ? 'research' : {'Video understanding':'video','What changed':'changes','Activity':'activity'}[title] || 'details';
  if (subtext) label.append(el('small','context-meta',subtext));
  summary.append(label,el('span','disclosure-mark','＋')); root.append(summary);
  let mounted = false; root.addEventListener('toggle',() => { if (root.open && !mounted) { mounted = true; const body = el('div','card-body'); root.append(body); render(body); } }); return root;
}
function progressive(host,rows,render,size = 5) {
  let shown = 0; const list = el('div'); const more = button('Show more',draw,'text-button');
  function draw() { rows.slice(shown,shown + size).forEach(row => list.append(render(row))); shown += size; more.hidden = shown >= rows.length; if (!more.hidden) more.textContent = `Show more · ${rows.length - shown} remaining`; }
  host.append(list,more); draw();
}
function metricsGrid(metrics = {}, secondary = false) {
  const grid = el('div','metrics' + (secondary ? ' secondary' : ''));
  const fields = secondary ? [['clicks','Clicks'],['cpc','CPC','money'],['leads','Meta leads'],['checkouts','Checkouts'],['impressions','Impressions']] : [['spend','Spend','money'],['ctr','CTR','rate'],['payments','Payments']];
  for (const [key,label,kind] of fields) { const metric = el('div','metric'); metric.append(el('strong','',format(metrics[key],kind)),el('span','',label)); grid.append(metric); }
  return grid;
}
function creativeCard(row, selectable = false) {
  const card = el('article','creative-card' + (selectable && selected.has(row.id) ? ' selected' : ''));
  const media = el('div','creative-media');
  const image = imageNode(row.imageUrl,row.name);
  if (row.videoUrl && row.source !== 'upload' && assetUrl(row.videoUrl)) {
    const video = el('video'); video.src = assetUrl(row.videoUrl); video.controls = true; video.preload = 'none'; video.playsInline = true;
    if (assetUrl(row.imageUrl)) video.poster = assetUrl(row.imageUrl); media.append(video);
  } else if (image) media.append(image);
  else media.append(el('span','','Asset unavailable'));
  media.append(el('span','media-kind',row.videoUrl ? 'VIDEO' : 'IMAGE'));
  const info = el('div','creative-info');
  if (selectable) {
    const label = el('label','creative-select'); const input = el('input'); input.type = 'checkbox'; input.checked = selected.has(row.id);
    input.addEventListener('change',() => { $('adset').value = ''; if (input.checked) selected.add(row.id); else selected.delete(row.id); card.classList.toggle('selected',input.checked); updateSelection(); });
    label.append(input,el('span','creative-name',row.name)); info.append(label);
  } else info.append(el('strong','creative-name',row.name));
  const subtitle = row.source === 'upload' ? 'Your upload · no measured performance' : [row.adsetName,row.campaignName].filter(Boolean).join(' · ') || row.id;
  const sub = el('p','muted',subtitle); sub.title = subtitle; info.append(sub);
  if (row.source !== 'upload') info.append(metricsGrid(row.metrics));
  if (row.signal) info.append(el('span','signal',row.signal));
  const details = el('details','creative-details'); details.append(el('summary','','Creative details'));
  if (row.title) details.append(el('p','',row.title));
  if (row.body) details.append(el('p','creative-copy muted',row.body));
  if (row.source !== 'upload') details.append(metricsGrid(row.metrics,true));
  if (row.videoUrl) details.append(link('Open original video ↗',assetUrl(row.videoUrl)));
  if (row.frameSource) details.append(el('p','muted',row.frameSource));
  for (const warning of row.warnings || []) details.append(el('p','warning',warning));
  info.append(details); card.append(media,info); return card;
}
function renderMediaLibrary(node, media) {
  const videos = media?.youtube || [], index = media?.youtubeIndex || {};
  if (!videos.length) return;
  const entry = el('div','context-entry video-library');
  entry.append(el('h3','',`YouTube library · ${videos.length} uploads`));
  const saved = index.indexedAt ? ' · saved ' + new Date(index.indexedAt).toLocaleDateString() : '';
  entry.append(el('p','context-meta','Available from the saved channel index' + saved));
  const controls = el('div','video-library-controls');
  const search = el('input'); search.type = 'search'; search.placeholder = 'Search titles or topics'; search.setAttribute('aria-label','Search YouTube library'); search.value = videoQuery;
  const filter = el('select'); filter.setAttribute('aria-label','YouTube upload type');
  for (const [value,label] of [['','All uploads'],['video','Videos'],['short','Shorts'],['stream','Livestreams']]) { const option = el('option','',label); option.value = value; filter.append(option); }
  filter.value = videoKind; controls.append(search,filter);
  const count = el('p','context-meta'); count.setAttribute('role','status');
  const list = el('div','video-library-list');
  const more = el('button','small-button','Show more'); more.type = 'button';
  let limit = 12;
  function draw() {
    const words = videoQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const matches = videos.filter(video => (!videoKind || (video.kind || 'video') === videoKind) && words.every(word => [video.title,...(video.topics || [])].join(' ').toLowerCase().includes(word)));
    list.replaceChildren();
    count.textContent = matches.length ? `${Math.min(limit,matches.length)} of ${matches.length} uploads` : 'No videos match this search.';
    for (const video of matches.slice(0,limit)) {
      const row = el('div','video-library-row');
      row.append(link(video.title || video.videoId,'https://www.youtube.com/watch?v=' + encodeURIComponent(video.videoId)));
      const seconds = video.durationSeconds;
      const duration = seconds ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2,'0')}` : 'Duration unavailable';
      row.append(el('p','context-meta',[{video:'Video',short:'Short',stream:'Livestream'}[video.kind || 'video'],duration,...(video.topics || [])].filter(Boolean).join(' · ')));
      list.append(row);
    }
    more.hidden = matches.length <= limit;
  }
  search.addEventListener('input',() => { videoQuery = search.value; limit = 12; draw(); });
  filter.addEventListener('change',() => { videoKind = filter.value; limit = 12; draw(); });
  more.onclick = () => { limit += 12; draw(); };
  entry.append(controls,count,list,more); draw(); node.append(entry);
}
function uploadFile(file,row) {
  return new Promise((resolve,reject) => {
    const xhr = new XMLHttpRequest(); xhr.open('POST','/api/uploads'); xhr.setRequestHeader('Content-Type',file.type || 'application/octet-stream'); xhr.setRequestHeader('X-File-Name',encodeURIComponent(file.name)); xhr.timeout = 180000;
    xhr.upload.onprogress = event => { if (event.lengthComputable) row.textContent = `${file.name} · ${Math.round(event.loaded / event.total * 100)}% uploaded · preparing preview…`; };
    xhr.onload = () => { let result; try { result = JSON.parse(xhr.responseText); } catch { reject(new Error('Upload returned an unreadable response.')); return; } if (xhr.status >= 200 && xhr.status < 300) resolve(result); else reject(new Error(result.error || 'Upload failed.')); };
    xhr.onerror = () => reject(new Error('Connection interrupted. Choose the file again.')); xhr.ontimeout = () => reject(new Error('Upload timed out. Choose the file again.')); xhr.send(file);
  });
}
async function uploadFiles(files) {
  if (uploading || busy || !catalog) return;
  const queue = Array.from(files); if (!queue.length) return;
  uploading = true; $('upload-files').disabled = true; $('new-version').disabled = true; $('upload-results').replaceChildren(); updateSelection();
  try {
    for (const file of queue) {
      const row = el('p','small',`${file.name} · uploading…`); $('upload-results').append(row);
      try {
        const suffix = file.name.split('.').at(-1).toLowerCase(), isImage = ['jpg','jpeg','png','webp'].includes(suffix);
        if (!isImage && !['mp4','mov','webm'].includes(suffix)) throw new Error('Choose a JPEG, PNG, WebP, MP4, MOV, or WebM file.');
        if (!file.size) throw new Error('This file is empty.');
        if (file.size > (isImage ? 20 : 100) * 1024 * 1024) throw new Error(`File exceeds the ${isImage ? 20 : 100} MiB limit.`);
        const creative = await uploadFile(file,row); catalog.creatives.unshift(creative); selected.add(creative.id); $('adset').value = ''; $('creative-search').value = ''; creativePage = 0; renderCreatives(); row.textContent = `${file.name} · ready and selected`;
      } catch(e) { row.classList.add('warning'); row.textContent = `${file.name} · ${e.message}`; }
    }
  } finally { uploading = false; $('upload-files').disabled = false; $('upload-files').value = ''; $('new-version').disabled = busy; updateSelection(); }
}


function screenshots(value) {
  const found = new Set();
  function visit(row) { if (typeof row === 'string' && assetUrl(row) && !/\.(mp4|webm|mov)(?:\?|$)/i.test(row)) found.add(assetUrl(row)); else if (Array.isArray(row)) row.forEach(visit); else if (row && typeof row === 'object') Object.values(row).forEach(visit); }
  visit(value); return [...found];
}
function researchCard(record) {
  const sources = record.context?.competitors || [], partial = sources.filter(s => s.error || ['partial','failed','blocked'].includes(s.status)).length;
  const inspected = sources.filter(s => s.inspected || s.pageText || s.screenshots?.length).length, understood = sources.filter(s => s.read).length;
  return disclosure(`Research · ${sources.length} source${sources.length === 1 ? '' : 's'}`,body => {
    body.append(el('p','small muted',`${sources.filter(s => s.url).length} discovered · ${inspected} inspected · ${understood} visually understood`));
    const problems = new Map();
    for (const source of sources) if (source.error) { const key = friendlyError(source.error); if (!problems.has(key)) problems.set(key,[]); problems.get(key).push(source.competitorName || source.competitor || hostLabel(source.url)); }
    for (const [message,names] of problems) body.append(el('p','notice',message + ' Affected: ' + names.join(', ') + '.'));
    if (!sources.length) body.append(el('p','muted','No research observations were saved for this operation.'));
    for (const source of sources) {
      const name = source.competitorName || source.competitor || hostLabel(source.url);
      const card = disclosure(name,entry => {
        if (source.url) entry.append(link('Open ' + hostLabel(source.url) + ' ↗',source.url));
        const kind = ({first_party:'Recraft product evidence',competitor:'Campaign competitor',adjacent:'Adjacent workflow reference',discovery:'Discovery'})[source.sourceKind] || (source.sourceKind === 'ad_destination' ? 'Observed ad destination' : source.sourceKind === 'ads_library' ? 'Ad Library' : 'Reference page');
        entry.append(el('p','small muted',kind + ' · ' + (source.status || 'Status unavailable').replaceAll('_',' ')));
        const adaptations = record.proposal?.competitorAdaptations?.filter(row => hostLabel(row.sourceUrl) === hostLabel(source.url)) || [];
        for (const row of adaptations) { fact(entry,'Observed pattern',row.observedPattern); fact(entry,'Why it fits',row.whyItFits); fact(entry,'Recraft adaptation',row.recraftAdaptation); }
        if (source.selectionReason) fact(entry,'Why this source',source.selectionReason);
        if (source.queries?.length) fact(entry,'Searches',source.queries);
        const read = source.read || {};
        fact(entry,'Observed headline',read.heroHeadline); fact(entry,'Primary CTA',read.primaryCta);
        for (const [key,title] of [['notablePatterns','Patterns'],['sectionSequence','Section order'],['heroComposition','Hero'],['typography','Typography'],['spacing','Spacing'],['proofPlacement','Proof placement'],['ctaRepetition','Calls to action'],['phoneDifferences','Mobile differences']]) if (read[key]?.length) fact(entry,title,read[key].map ? read[key].map(row => typeof row === 'string' ? row : JSON.stringify(row)) : read[key]);
        for (const url of screenshots(source.screenshots)) { const image = imageNode(url,name + ' landing screenshot','research-image'); if (image) { const open = link('',url); open.append(image); entry.append(open); } }
        const ad = source.ads?.find(row => String(row.id) === String(source.selectedAdId));
        if (ad) fact(entry,'Advertiser evidence',ad.advertiserVerified ? `Verified · ${ad.pageName || name}` : 'Advertiser verification unavailable');
        for (const [title,url] of [['Ad Library',source.adLibraryUrl || ad?.adLibraryUrl],['Advertised destination',source.advertisedUrl || ad?.destinationUrl],['Resolved destination',source.resolvedUrl]]) if (safeLink(url)) { fact(entry,title,hostLabel(url)); entry.append(link('Open source ↗',url)); }
        const steps = Array.isArray(source.journey) ? source.journey : source.journey?.steps || [];
        for (const step of steps) fact(entry,'Observed journey',typeof step === 'string' ? step : [step.title || step.label,step.url || step.destinationUrl,step.status,step.reason].filter(Boolean).join(' · '));
        if (source.ads?.length) { entry.append(el('h3','','Sampled ads')); progressive(entry,source.ads,row => { const item = el('div','activity-step'); item.append(link(row.pageName || name,row.adLibraryUrl)); fact(item,'Ad copy',row.text); fact(item,'Status',row.activeStatus); if (row.destinationUrl) item.append(link('Destination ↗',row.destinationUrl)); return item; },3); }
        if (source.observedAt || source.capturedAt) fact(entry,'Observed',new Date(source.observedAt || source.capturedAt).toLocaleString());
        if (source.error) entry.append(el('p','small muted','Analysis was incomplete. The original provider error is available in Activity.'));
      },hostLabel(source.url) + ' · ' + (source.error ? 'Partial evidence' : (source.status || 'Saved evidence').replaceAll('_',' ')));
      const thumbnail = imageNode(screenshots(source.screenshots)[0],name + ' preview','source-thumb'); if (thumbnail) card.querySelector('summary').prepend(thumbnail);
      body.append(card);
    }
    if (active.mediaLibrary?.youtube?.length) body.append(disclosure('Product video library',node => renderMediaLibrary(node,active.mediaLibrary),'Current catalog · separate from this saved research'));
    const refresh = button('Refresh research',guarded(() => action('research',{...(active.research || {}),refresh:true})));
    refresh.dataset.needsIdle = 'true'; refresh.disabled = busy || !editableDraft(); body.append(refresh);
  },partial ? `${partial} partially available` : 'Observed patterns and source links');
}
const videoTime = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2,'0')}`;
function videoCard(record,item) {
  const evidence = item.analysis.videoEvidence, creative = record.creatives?.find(c => c.id === item.creativeId) || {};
  const status = evidence.method === 'visual_only' ? 'Visual-only · narration unavailable' : evidence.audioAvailable ? 'Video + audio' : 'Silent video';
  const card = disclosure('Video understanding',body => {
    fact(body,'What this video is communicating',evidence.concept); fact(body,'Narrative',evidence.narrative);
    body.append(el('p',evidence.method === 'visual_only' ? 'warning' : 'small muted',status));
    let player; const url = assetUrl(creative.videoUrl || creative.videoPath);
    if (url) { player = el('video','evidence-player'); player.src = url; player.controls = true; player.preload = 'metadata'; player.playsInline = true; body.append(player); }
    const seek = seconds => { if (!player) return; const jump = () => { player.currentTime = Math.min(seconds,Math.max(0,player.duration - .05)); }; if (player.readyState >= 1) jump(); else player.addEventListener('loadedmetadata',jump,{once:true}); player.focus({preventScroll:true}); };
    const frames = el('div','storyboard');
    for (const frame of (evidence.storyboard || []).slice(0,12)) { const tile = button('',() => seek(frame.timestamp),'storyboard-frame'); tile.setAttribute('aria-label','Seek to ' + videoTime(frame.timestamp)); const image = imageNode(frame.imageUrl || frame.path,'Frame at ' + videoTime(frame.timestamp)); if (image) tile.append(image); tile.append(el('span','',videoTime(frame.timestamp))); frames.append(tile); }
    body.append(frames);
    for (const [key,title] of [['spokenText','Narration'],['visibleText','On-screen text']]) {
      body.append(el('h3','',title)); const rows = (evidence.moments || []).filter(m => m[key]?.length);
      if (!rows.length) body.append(el('p','small muted',key === 'spokenText' && !evidence.audioAvailable ? 'Narration unavailable.' : 'No confident observations.'));
      progressive(body,rows,m => { const row = el('div','moment-row'); row.append(button(videoTime(m.start),() => seek(m.start),'text-button'),el('p','',m[key].join(' · '))); return row; },3);
    }
    body.append(el('h3','','Key moments'));
    progressive(body,evidence.moments || [],m => { const row = el('div','moment-row'); row.append(button(videoTime(m.start) + ' · ' + m.kind,() => seek(m.start),'text-button'),el('p','',m.description)); return row; },4);
    fact(body,'Observed actions',evidence.observedActions); fact(body,'Advertised claims · unverified',evidence.advertisedClaims);
    fact(body,'Landing implications',evidence.landingImplications);
    fact(body,'Coverage',(evidence.coveredIntervals || []).map(row => `${videoTime(row.start ?? row[0] ?? 0)}–${videoTime(row.end ?? row[1] ?? 0)}`));
    for (const limitation of evidence.limitations || []) body.append(el('p','warning',friendlyError(limitation)));
    const retry = button('Reanalyze video',guarded(() => action('reanalyze'))); retry.dataset.needsIdle = 'true'; retry.disabled = busy || !editableDraft(); body.append(retry);
  },firstSentence(evidence.concept,100) + ' · ' + status);
  const poster = creative.imageUrl || creative.imagePath || evidence.storyboard?.[0]?.imageUrl || evidence.storyboard?.[0]?.path;
  const thumbnail = imageNode(poster,creative.name || 'Video thumbnail','source-thumb');
  if (thumbnail) card.querySelector('summary').prepend(thumbnail);
  return card;
}
function changesCard(record) {
  return disclosure('What changed',body => {
    const proposal = record.proposal || {}, brief = proposal.interpretedBrief;
    for (const change of record.changes || []) body.append(el('p','',change));
    if (brief) { fact(body,'Audience',brief.audience); fact(body,'Promise',brief.promise); fact(body,'Design direction',brief.designDirection); fact(body,'Objections',brief.objections); }
    fact(body,'Problem observed',proposal.problem); fact(body,'Hypothesis · to be measured',proposal.hypothesis);
    if (proposal.evidence?.length) { body.append(el('h3','','Supporting evidence')); progressive(body,proposal.evidence,row => el('p','',typeof row === 'string' ? row : row.summary || JSON.stringify(row))); }
    if (!record.context?.competitors?.length) for (const adaptation of proposal.competitorAdaptations || []) { fact(body,'Observed pattern',adaptation.observedPattern); fact(body,'Why it fits',adaptation.whyItFits); fact(body,'Recraft adaptation',adaptation.recraftAdaptation); body.append(link('Observed source ↗',adaptation.sourceUrl)); }
  },'Brief, hypothesis and supporting evidence');
}
function activityCard(operationId,record = null) {
  const card = disclosure('Activity',body => { card.bodyNode = body; refreshActivity(card); },'Execution steps and technical details');
  card.operationId = operationId; card.record = record; return card;
}
function refreshActivity(card) {
  if (!card.bodyNode) return;
  const events = (active?.events || []).filter(e => (card.record?.eventIds ? card.record.eventIds.includes(e.seq) : e.operationId === card.operationId) && !e.kind.startsWith('chat_'));
  card.seen ||= new Set();
  for (const event of events) {
    if (card.seen.has(event.seq)) continue; card.seen.add(event.seq);
    const data = event.data || {}, label = data.label || {operation_started:'Operation started',operation_completed:'Operation completed',operation_failed:'Operation needs attention',model_request_started:'Preparing the proposal',model_request_completed:'Proposal step completed'}[event.kind] || event.kind.replaceAll('_',' ');
    const row = disclosure(label,details => details.append(el('pre','',JSON.stringify(cleanDetails(data),null,2))));
    card.bodyNode.append(row);
  }
  if (!card.rawAdded && card.record) { card.rawAdded = true; card.bodyNode.append(disclosure('Saved evidence details',body => body.append(el('pre','',JSON.stringify(cleanDetails({error:card.record.error,sources:card.record.context?.competitors,analyses:card.record.analyses,audience:card.record.audience}),null,2))))); }
}
function renderMessageCopy(host,text,assistant) {
  host.replaceChildren();
  if (!assistant) { host.textContent = text; return; }
  function formatted(value) {
    const block = el('div','message-copy');
    for (const paragraph of value.split(/\n\s*\n/).filter(Boolean)) {
      const p = el('p');
      for (const part of paragraph.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
        p.append(part.startsWith('**') && part.endsWith('**') ? el('strong','',part.slice(2,-2)) : part.startsWith('`') && part.endsWith('`') ? el('code','',part.slice(1,-1)) : document.createTextNode(part));
      }
      block.append(p);
    }
    return block;
  }
  const full = formatted(text);
  if (text.split(/\s+/).length <= 160) { host.append(full); return; }
  const paragraphs = text.split(/\n\s*\n/), lead = [];
  for (const paragraph of paragraphs) { if (lead.join(' ').split(/\s+/).length + paragraph.split(/\s+/).length > 120) break; lead.push(paragraph); }
  const excerpt = lead.length ? lead.join('\n\n') : text.split(/\s+/).slice(0,100).join(' ') + '…';
  const short = formatted(excerpt); full.hidden = true;
  const expand = button('Read full reply',() => { const open = full.hidden; full.hidden = !open; short.hidden = open; expand.textContent = open ? 'Show less' : 'Read full reply'; expand.setAttribute('aria-expanded',String(open)); },'text-button');
  expand.setAttribute('aria-expanded','false'); host.append(short,full,expand);
}
function messageNode(message) {
  let row = feedNodes.get('message-' + message.id);
  if (!row) { row = el('article','chat-message ' + message.role); row.dataset.messageId = message.id;
    if (message.role === 'assistant') { const label = el('div','message-label'); label.append(el('span','agent-spark','✳'),document.createTextNode('Growth agent')); row.append(label); }
    row.textNode = el('div','message-text'); row.append(row.textNode); row.stateNode = el('div'); row.append(row.stateNode); feedNodes.set('message-' + message.id,row);
  }
  const signature = JSON.stringify([message.text,message.status,message.error]);
  if (row.signature !== signature) { row.signature = signature; renderMessageCopy(row.textNode,message.text || '',message.role === 'assistant'); row.stateNode.replaceChildren();
    if (message.status === 'pending') { const progress = el('div','progress-line'); progress.append(el('span','spinner'),document.createTextNode('Thinking about your landing…')); row.stateNode.append(progress); }
    if (message.status === 'failed') { row.stateNode.append(el('p','notice',friendlyError(message.error))); const retry = button('Retry message',guarded(() => retryMessage(message))); retry.dataset.chatRetry = message.id; row.stateNode.append(retry); row.stateNode.append(disclosure('Technical details',body => body.append(el('pre','',String(cleanDetails(message.error)))))); }
  }
  return row;
}
function operationNode(id,record,events,legacy = false) {
  let node = feedNodes.get('operation-' + id);
  if (!node) {
    node = el('article','chat-message assistant' + (legacy ? ' legacy' : '')); node.dataset.operationId = id;
    node.append(el('div','message-label',legacy ? 'Saved activity · before chat' : '✳  Growth agent'));
    node.heading = el('h3'); node.progress = el('div','progress-line'); node.summary = el('ul','change-list'); node.links = el('div','result-links'); node.cards = el('div');
    node.append(node.heading,node.progress,node.summary,node.links,node.cards); feedNodes.set('operation-' + id,node);
  }
  const last = events.at(-1), terminal = record || ['operation_completed','operation_failed'].includes(last?.kind);
  if (record && !node.completed) {
    node.completed = true; node.progress.replaceChildren();
    const generated = ['generate','revise','select_direction','prepare_publish'].includes(record.action);
    node.heading.textContent = legacy ? `Saved ${active.variant}${record.revision ? ' · revision ' + record.revision : ''}` : record.status === 'failed' ? 'This operation needs attention' : record.status === 'awaiting_direction' ? 'Three directions to explore' : generated && record.revision ? `Revision ${record.revision} ready` : record.action === 'research' ? 'Research updated' : record.action === 'reanalyze' ? 'Video evidence updated' : record.action === 'publish' ? 'Publication updated' : 'Saved activity';
    if (record.error) node.progress.append(el('p','warning',friendlyError(record.error)));
    if (legacy) node.progress.append(el('p','small muted','This draft predates chat. These are its saved results, not a reconstructed conversation.'));
    const succeeded = record.status === 'ready' || record.status === 'published';
    if ((generated && succeeded) || legacy) for (const text of (record.changes || []).slice(0,3)) node.summary.append(el('li','',firstSentence(text,180)));
    if (record.revision) node.links.append(button((record.status === 'failed' ? 'Last ready revision ' : 'View revision ') + record.revision,() => viewRevision(record.revision),'text-button'));
    if (record.status === 'awaiting_direction') { const explore = button('Explore directions →',() => { if (active.status !== 'awaiting_direction' || active.operationId !== id) return; resultsMode = 'directions'; renderPreview(); setWorkspaceTab('preview'); },'text-button'); explore.dataset.directionOperation = id; node.links.append(explore); }
    if (((generated && succeeded) || legacy) && record.proposal) node.cards.append(changesCard(record));
    if (record.context?.competitors?.length || record.action === 'research' || legacy) node.cards.append(researchCard(record));
    for (const item of record.analyses || []) if (item.analysis?.videoEvidence) node.cards.append(videoCard(record,item));
    if (record.audience || (legacy && active.audience)) { const audience = record.audience || active.audience; node.cards.append(disclosure('Audience evidence',body => renderAudienceEvidence(body,audience))); }
    if (node.activity) node.activity.record = record; else node.activity = activityCard(id,record);
    node.cards.append(node.activity);
  } else if (!node.completed) {
    const stage = last?.stage || 'design'; node.heading.textContent = terminal ? 'Operation finished' : stageLabels[stage] || 'Working on your landing';
    node.progress.replaceChildren(); if (!terminal) node.progress.append(el('span','spinner'));
    const timing = el('span','',terminal ? 'Loading saved results…' : elapsed(events[0]?.at) + ' · progress is saved'); timing.dataset.started = !terminal ? events[0]?.at || '' : ''; node.progress.append(timing);
    if (!node.activity) { node.activity = activityCard(id); node.cards.append(node.activity); }
  }
  if (node.completed && node.activity && node.cards.firstChild === node.activity && record) { node.activity.remove(); node.cards.append(node.activity); }
  if (node.activity) refreshActivity(node.activity);
  return node;
}
function renderAudienceEvidence(body,audience) {
  fact(body,'Evidence summary',audience.summary || 'Observed outcomes from the saved report.');
  fact(body,'Period',audience.period ? `${audience.period.start} → ${audience.period.end}` : 'Unavailable');
  body.append(el('p','small muted','Observed outcomes, attribution and inferred intent are separate. Missing values do not mean zero.'));
  progressive(body,audience.outcomes || [],row => { const item = el('div','activity-step'); item.append(el('strong','',row.name || row.adId),el('p','',`Checkout: ${format(row.checkouts)} · PostHog payments: ${format(row.posthogPayments)} · Warehouse payers: ${format(row.warehousePayers)}`)); return item; },4);
  for (const [name,coverage] of Object.entries(audience.coverage || {})) fact(body,name,[(coverage.status || 'unavailable').replaceAll('_',' '),coverage.freshness,coverage.observed_at].filter(Boolean).join(' · '));
  body.append(el('p','small muted','Complete saved fields are available in Activity → Saved evidence details.'));
}
function inputsNode() {
  let node = feedNodes.get('inputs'); if (node) return node;
  node = el('article','chat-message inputs');
  node.append(disclosure(`Your inputs · ${active.creatives.length} creative${active.creatives.length === 1 ? '' : 's'}`,body => {
    progressive(body,active.creatives,creative => creativeCard(creative,false),3);
    if (editableDraft()) { const analyze = button('Analyze / refresh creatives',guarded(() => action('reanalyze'))); analyze.dataset.needsIdle = 'true'; analyze.disabled = busy; body.append(analyze); }
    if (active.mediaLibrary?.youtube?.length) body.append(disclosure('Product video library',host => renderMediaLibrary(host,active.mediaLibrary),'Current saved catalog'));
  },'Selected assets and their observed performance'));
  feedNodes.set('inputs',node); return node;
}
function renderConversation() {
  if (!active) return;
  const scroll = $('chat-scroll'), nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 100, priorTop = scroll.scrollTop;
  const bounds = scroll.getBoundingClientRect(), anchor = !nearBottom ? document.elementFromPoint(bounds.left + bounds.width / 2,bounds.top + 12) : null;
  const anchorTop = anchor && scroll.contains(anchor) ? anchor.getBoundingClientRect().top : null;
  const messages = active.conversation?.messages || [], results = active.operationResults || [], groups = new Map();
  for (const event of active.events || []) if (!event.kind.startsWith('chat_')) { const id = event.operationId || 'legacy'; if (!groups.has(id)) groups.set(id,[]); groups.get(id).push(event); }
  const items = messages.map((message,index) => ({key:'message-' + message.id,at:Date.parse(message.at) || index,node:messageNode(message)}));
  if (active.creatives?.length) items.push({key:'inputs',at:Date.parse(active.createdAt) - 2,node:inputsNode()});
  const resultIds = new Set(results.map(r => r.id));
  const legacy = active.legacyEvidence || ((!results.length && !messages.length && ((active.revisions || []).length || (active.events || []).length) ? {id:'legacy-summary',action:'saved_history',revision:active.readyRevision,status:active.status,at:active.createdAt,proposal:active.proposal,changes:active.revisions?.find(r => r.number === active.readyRevision)?.changeSummary || [],context:active.context,analyses:active.analyses,creatives:active.creatives} : null));
  if (legacy) items.push({key:'operation-legacy-summary',at:Date.parse(active.createdAt) - 1,node:operationNode('legacy-summary',legacy,(active.events || []).filter(e => legacy.eventIds ? legacy.eventIds.includes(e.seq) : !resultIds.has(e.operationId)),true)});
  for (const [id,events] of groups) {
    const record = results.find(r => r.id === id), isCurrent = id === active.operationId && operating();
    if (!record && !isCurrent && !messages.some(m => m.operationId === id)) continue;
    const reply = messages.find(m => m.operationId === id);
    items.push({key:'operation-' + id,at:reply ? Date.parse(reply.at) + 1 : Date.parse(events[0].at),node:operationNode(id,record,events)});
  }
  items.sort((a,b) => a.at - b.at);
  const host = $('conversation-list');
  // Insert only missing/moved entries: disclosures and players keep their DOM identity.
  items.forEach((item,index) => { if (host.children[index] !== item.node) host.insertBefore(item.node,host.children[index] || null); });
  const keep = new Set(items.map(i => i.node)); for (const child of [...host.children]) if (!keep.has(child)) child.remove();
  if (nearBottom) scroll.scrollTop = scroll.scrollHeight; else { scroll.scrollTop = priorTop + (anchorTop !== null && anchor.isConnected ? anchor.getBoundingClientRect().top - anchorTop : 0); if (items.length !== host.previousCount) $('new-activity').hidden = false; }
  host.previousCount = items.length;
  const latestReply = messages.filter(m => m.role === 'assistant').at(-1), latestResult = results.at(-1);
  const announcementKey = `${latestReply?.id}:${latestReply?.status}:${latestResult?.id}`;
  if (host.announcementKey !== announcementKey) { host.announcementKey = announcementKey; $('chat-announcement').textContent = latestReply?.status === 'completed' ? 'Growth agent replied. ' + firstSentence(latestReply.text) : latestReply?.status === 'failed' ? 'Your message needs attention. Retry is available.' : latestResult ? 'New operation result is available in chat.' : ''; }
  if (mobileTab !== 'chat' && active.conversation?.status === 'idle') $('chat-unread').hidden = false;
}
function elapsed(start) { const seconds = Math.max(0,Math.floor((Date.now() - Date.parse(start || '')) / 1000)); if (!Number.isFinite(seconds)) return ''; return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`; }

function researchConfig() { const urls = id => $(id).value.split(/\n/).map(s => s.trim()).filter(Boolean); return {discovery:'auto',competitors:urls('research-competitors'),excludedDomains:urls('research-exclusions'),country:$('research-country').value,adLibraryUrls:urls('research-ad-urls'),landingUrls:urls('research-landing-urls'),refresh:$('research-refresh').checked}; }
function updateResearchSummary() { const config = researchConfig(); $('research-summary-label').textContent = `Auto discovery · ${config.competitors.length} prioritized · ${config.landingUrls.length} references`; }
function renderCreatives() {
  if (!catalog) return;
  const query = $('creative-search').value.trim().toLowerCase(); const rows = catalog.creatives.filter(row => [row.name,row.adsetName,row.campaignName,row.title].join(' ').toLowerCase().includes(query));
  const pages = Math.max(1,Math.ceil(rows.length / PAGE_SIZE)); creativePage = Math.min(creativePage,pages - 1); const start = creativePage * PAGE_SIZE;
  $('creative-list').replaceChildren(...rows.slice(start,start + PAGE_SIZE).map(row => creativeCard(row,true)));
  if (!rows.length) $('creative-list').append(el('p','empty','No matching creatives. Upload your own or try another search.'));
  $('creative-page-label').textContent = rows.length ? `${start + 1}–${Math.min(start + PAGE_SIZE,rows.length)} of ${rows.length}` : 'No matching creatives';
  $('creative-prev').disabled = creativePage === 0; $('creative-next').disabled = creativePage >= pages - 1; updateSelection();
}
function updateSelection() {
  if (!catalog) return;
  $('selection-count').textContent = selected.size || 'Optional'; $('clear-selection').hidden = !selected.size;
  const group = catalog.adsets.find(row => row.id === $('adset').value); $('adset-summary').textContent = group ? `${group.creativeIds.length} creatives from this ad set` : '';
  updateComposer();
}
function selectBaseline() {
  const base = catalog?.baselines.find(row => row.version === $('baseline').value);
  $('baseline-description').textContent = base ? (base.supported ? base.description || 'Your work becomes an independent version.' : base.limitation || 'This baseline is preview-only.') : '';
  comparison = 'baseline'; updateComposer(); renderPreview();
}
async function refreshCatalog() {
  const previous = $('baseline').value; catalog = await api('/api/catalog'); busy = !!catalog.busy; catalog.creatives ||= []; catalog.baselines ||= []; catalog.adsets ||= []; catalog.report ||= {};
  $('baseline').replaceChildren(new Option('Choose a landing',''));
  for (const base of catalog.baselines) $('baseline').append(new Option(base.version + (base.default ? ' · current default' : '') + (!base.supported ? ' · preview only' : ''),base.version));
  if (catalog.baselines.some(row => row.version === previous)) $('baseline').value = previous;
  $('adset').replaceChildren(new Option('Select individual creatives','')); for (const group of catalog.adsets) $('adset').append(new Option(`${group.name} · ${group.creativeIds.length} ads`,group.id));
  const period = catalog.report.period?.current; $('report-label').textContent = period ? `${period.start} → ${period.end}` : 'Your uploaded creatives';
  $('catalog-warnings').replaceChildren(...(catalog.warnings || []).map(text => el('p','warning',friendlyError(text)))); renderCreatives();
}
async function refreshHistory() {
  const result = await api('/api/drafts'); busy = !!result.busy; $('history-count').textContent = result.drafts.length;
  const signature = JSON.stringify([active?.id,result.drafts]); if (contentSignatures.get('history') !== signature) { contentSignatures.set('history',signature); $('history-list').replaceChildren();
    for (const row of result.drafts) { const node = button('',guarded(() => openDraft(row.id)),'history-card' + (active?.id === row.id ? ' active' : '')); node.append(el('strong','',row.variant),el('span','history-names',row.goalPrompt || row.creativeNames?.join(', ') || 'Goal-led landing'),el('span','small muted',labels[row.status] || row.status)); $('history-list').append(node); }
    if (!result.drafts.length) $('history-list').append(el('p','empty','Your first landing starts here.'));
  }
  updateComposer();
}
function resetDraftUi() {
  contentSignatures.clear(); feedNodes.clear(); $('conversation-list').replaceChildren(); eventCursor = 0; previewRevision = null; resultsMode = 'preview'; lastDirectionSet = null;
  editorKey = ''; editorSection = ''; editorChanges = {}; editorOrder = []; editorOmit = []; $('editor-card').hidden = true; $('editor-card').open = false; $('new-activity').hidden = true;
}
async function fresh(base = '') {
  if (busy || uploading || sending) return;
  rememberView(); const token = ++activeRequest; stopStream(); active = null; resetDraftUi(); remember(null); error(''); selected = new Set(); creativePage = 0;
  await Promise.all([refreshCatalog(),refreshHistory()]); if (token !== activeRequest) return;
  $('setup').hidden = false; $('draft-title').textContent = 'New landing'; $('draft-status').hidden = true; $('draft-meta').textContent = '';
  for (const id of ['publish','public-link','use-baseline','refresh-publication','retry','generate-saved','edit-page','draft-notice','show-directions']) $(id).hidden = true;
  $('history-menu').hidden = true; $('toggle-history').setAttribute('aria-expanded','false'); $('baseline').value = base;
  $('change-level').value = 'auto'; $('adset').value = ''; $('creative-search').value = ''; restoreInput(); selectBaseline(); renderCreatives(); setWorkspaceTab('chat'); $('chat-scroll').scrollTop = 0;
}
async function openDraft(id, preserveComposer = false) {
  rememberView(); const token = ++activeRequest; stopStream(); error(''); const draft = await api('/api/drafts/' + id); if (token !== activeRequest) return;
  active = draft; resetDraftUi(); remember(id); if (!preserveComposer) restoreInput(); $('setup').hidden = true; $('history-menu').hidden = true; $('toggle-history').setAttribute('aria-expanded','false');
  const view = savedViews.get(id); comparison = view?.comparison || (draft.previewUrl ? 'draft' : 'baseline'); previewRevision = view?.previewRevision || null; viewport = view?.viewport || 'desktop';
  $('change-level').value = draft.changeLevel || 'medium'; setWorkspaceTab(view?.mobileTab || 'chat'); renderDraft();
  $('chat-scroll').scrollTop = view?.scroll ?? (draft.conversation?.messages?.length ? $('chat-scroll').scrollHeight : 0);
  connectStream(id); await refreshHistory();
}
function editableDraft() { return active && active.status !== 'published' && !active.publicUrl && !active.publicOrigin && !active.githubPublicationPending; }
function updateComposer() {
  const blocked = busy || uploading || sending || !!previewRevision, text = $('goal-prompt').value.trim();
  const base = catalog?.baselines.find(row => row.version === $('baseline').value);
  $('generate').disabled = blocked || (!active && !base?.supported) || (!text && (active || !selected.size));
  $('new-version').disabled = busy || uploading || sending; $('attach-creatives').hidden = !!active; $('change-level').disabled = blocked;
  const level = $('change-level').value; $('level-description').textContent = {auto:'New audience or task: rebuild the page. Specific edits: keep them local.',light:'Copy only. Preserve layout, theme and imagery.',medium:'Copy, supported layouts, section order and media.',heavy:'Explore three directions, then choose a composition.'}[level];
  $('composer-status').textContent = previewRevision ? `Viewing revision ${previewRevision}. Select Latest to chat or edit.` : sending ? 'Sending your message…' : uploading ? 'Preparing your upload…' : busy ? 'Working — keep typing; send when this operation finishes.' : !active && !base ? 'Choose a starting page to begin.' : 'Ask to explore. Describe a change to apply it.';
  const context = active ? [active.baseVersion,active.readyRevision ? `Revision ${active.readyRevision}` : 'No generated preview'] : [base?.version,selected.size ? `${selected.size} creatives` : null];
  $('input-context').replaceChildren(...context.filter(Boolean).map(t => el('span','input-chip',t)));
  document.querySelectorAll('[data-needs-idle]').forEach(node => { node.disabled = blocked || !editableDraft(); });
  document.querySelectorAll('[data-chat-retry]').forEach(node => { node.disabled = blocked; });
  document.querySelectorAll('[data-direction-operation]').forEach(node => { node.hidden = active?.status !== 'awaiting_direction' || node.dataset.directionOperation !== active?.operationId; });
  for (const id of ['publish','refresh-publication','retry']) $(id).disabled = blocked;
  for (const id of ['edit-page','generate-saved']) $(id).disabled = blocked || !editableDraft();
  $('editing-area').querySelectorAll('button,input,select,textarea').forEach(control => { control.disabled = blocked || !editableDraft() || control.dataset.unavailable === 'true'; });
}
async function sendMessage() {
  if ($('generate').disabled) return;
  const text = $('goal-prompt').value.trim() || 'Create a landing based on these selected creatives.'; const changeLevel = $('change-level').value;
  sending = true; error(''); updateComposer();
  try {
    if (!active) {
      const baseline = catalog.baselines.find(row => row.version === $('baseline').value), adsetId = $('adset').value || null;
      const draft = await api('/api/drafts',{baseVersion:baseline.version,baseHash:baseline.hash,reportToken:catalog.reportToken,creativeIds:adsetId ? [] : [...selected],adsetId,research:researchConfig(),goalPrompt:text,changeLevel});
      await openDraft(draft.id,true); $('change-level').value = changeLevel;
    }
    const id = active.id, key = 'growth-pending-' + id; let pending;
    try { pending = JSON.parse(sessionStorage.getItem(key) || 'null'); } catch {}
    if (!pending || pending.text !== text || pending.changeLevel !== changeLevel) pending = {text,requestId:crypto.randomUUID().replaceAll('-',''),expectedRevision:active.readyRevision || 0,changeLevel};
    try { sessionStorage.setItem(key,JSON.stringify(pending)); } catch {}
    await api(`/api/drafts/${id}/messages`,pending);
    try { sessionStorage.removeItem(key); sessionStorage.removeItem('growth-input-new'); } catch {}
    if (active?.id === id) { if ($('goal-prompt').value.trim() === text) $('goal-prompt').value = ''; cacheInput(); await refreshActive(); $('chat-scroll').scrollTop = $('chat-scroll').scrollHeight; }
  } catch(e) { if (e.status === 400 && /newer revision/.test(e.message) && active) { try { sessionStorage.removeItem('growth-pending-' + active.id); } catch {} await refreshActive(); } error(e.message); }
  finally { sending = false; updateComposer(); }
}
async function retryMessage(message) {
  const input = active.conversation.messages.find(m => m.id === message.replyTo)?.request; if (!input) return;
  await api(`/api/drafts/${active.id}/messages`,{...input,retryFailed:true}); await refreshActive();
}
async function action(name,payload = {}) {
  if (!active || busy) return; const id = active.id; error('');
  if (previewRevision) throw new Error('Select Latest before changing or publishing this draft.');
  await api(`/api/drafts/${id}/${name}`,payload); if (active?.id !== id) return;
  await refreshActive(); await refreshHistory();
}
function stopStream() { if (stream) stream.close(); stream = null; streamLive = false; if (snapshotTimer) clearTimeout(snapshotTimer); snapshotTimer = null; }
function connectStream(id) {
  stopStream(); if (typeof EventSource === 'undefined') return;
  eventCursor = Math.max(0,...(active.events || []).map(e => e.seq || 0));
  const source = new EventSource(`/api/drafts/${id}/events?after=${eventCursor}`); stream = source;
  source.onopen = () => { if (stream === source) { streamLive = true; connection('live','All changes saved'); } };
  source.onerror = () => { if (stream === source) { streamLive = false; connection('reconnecting','Reconnecting…'); } };
  const receive = message => {
    if (stream !== source || active?.id !== id) return; let event; try { event = JSON.parse(message.data); } catch { return; }
    const seq = Number(event.seq || message.lastEventId) || 0; if (seq && seq <= eventCursor) return; eventCursor = Math.max(eventCursor,seq);
    if (event.kind) { active.events ||= []; active.events.push(event); if (event.data && 'busy' in event.data) busy = !!event.data.busy; if (event.data?.status) active.status = event.data.status; if (event.kind === 'operation_started') active.operationId = event.operationId; renderConversation(); updateComposer(); }
    if (!snapshotTimer) snapshotTimer = setTimeout(() => { snapshotTimer = null; refreshActive().catch(() => connection('reconnecting','Reconnecting…')); },180);
  };
  source.onmessage = receive; source.addEventListener('event',receive); source.addEventListener('state',() => refreshActive().catch(() => {}));
}
async function refreshActive() {
  if (snapshotPending || !active) return; snapshotPending = true; const id = active.id;
  try {
    const next = await api('/api/drafts/' + id); if (active?.id !== id) return;
    const snapshotSeq = Math.max(0,...(next.events || []).map(e => e.seq || 0)), extra = (active.events || []).filter(e => e.seq > snapshotSeq);
    if (extra.length) { next.events = [...next.events,...extra]; for (const event of extra) { if (event.data && 'busy' in event.data) next.busy = event.data.busy; if (event.data?.status) next.status = event.data.status; if (event.kind === 'operation_started') next.operationId = event.operationId; } }
    const before = active.readyRevision; active = next; busy = !!next.busy; renderDraft();
    if (before !== next.readyRevision && mobileTab !== 'preview') $('preview-unread').hidden = false;
  } finally { snapshotPending = false; }
}
function renderDraft() {
  if (!active) return; busy = !!active.busy;
  $('draft-title').textContent = active.variant; $('draft-status').hidden = false; $('draft-status').textContent = labels[active.status] || active.status; $('draft-status').dataset.status = active.status;
  $('draft-meta').textContent = `From ${active.baseVersion}`;
  const notice = active.publicationNotice || ''; $('draft-notice').hidden = !notice; $('draft-notice').textContent = notice;
  $('retry').hidden = active.status !== 'failed' || !active.retry; $('retry').disabled = busy;
  $('generate-saved').hidden = !['draft','needs_selection'].includes(active.status); $('generate-saved').disabled = busy || !editableDraft();
  $('publish').hidden = !active.readyRevision || operating() || active.status === 'published' || !!active.publicUrl; $('publish').disabled = busy;
  const github = catalog?.publishingProvider === 'github', prepared = active.revisions?.find(r => r.number === active.readyRevision)?.publicationPrepared;
  $('publish').textContent = github && !prepared ? 'Prepare publish ↗' : 'Publish ↗';
  $('refresh-publication').hidden = !github || !prepared || operating() || active.status === 'published' || !!active.publicUrl; $('refresh-publication').disabled = busy;
  $('public-link').hidden = !safeLink(active.publicUrl); if (safeLink(active.publicUrl)) $('public-link').href = safeLink(active.publicUrl);
  $('use-baseline').hidden = active.status !== 'published'; $('edit-page').hidden = !active.readyRevision || !editableDraft(); $('edit-page').disabled = busy;
  if (active.status === 'awaiting_direction' && active.directionSetId !== lastDirectionSet) { lastDirectionSet = active.directionSetId; resultsMode = 'directions'; if (mobileTab !== 'preview') $('preview-unread').hidden = false; }
  if (active.status !== 'awaiting_direction') resultsMode = 'preview';
  if (active.previewUrl && contentSignatures.get('ready-preview') !== active.readyRevision) { if (!previewRevision) comparison = 'draft'; contentSignatures.set('ready-preview',active.readyRevision); }
  renderConversation(); renderPreview();
  if (!$('editor-card').hidden) renderEditor();
  updateComposer();
}

function setFrame(frame,url) { const target = safeLink(url); if (target && frame.getAttribute('src') !== target) frame.src = target; }
function setWorkspaceTab(tab) { mobileTab = tab; $('workspace').dataset.mobileTab = tab; document.querySelectorAll('[data-workspace-tab]').forEach(node => node.setAttribute('aria-pressed',String(node.dataset.workspaceTab === tab))); $(tab === 'chat' ? 'chat-unread' : 'preview-unread').hidden = true; }
function viewRevision(number) { previewRevision = number === active?.readyRevision ? null : number; comparison = 'draft'; resultsMode = 'preview'; renderPreview(); updateComposer(); setWorkspaceTab('preview'); }
function showComparison(mode) { comparison = mode; renderPreview(); }
function renderPreview() {
  const base = catalog?.baselines.find(row => row.version === $('baseline').value);
  const baseline = active?.baselinePreviewUrl || (base ? `${catalog.previewBase}/${base.version}` : null);
  const preview = active?.previewUrl ? previewRevision ? `/api/drafts/${active.id}/preview/${previewRevision}` : active.previewUrl : null;
  if (!preview) comparison = 'baseline';
  $('preview-area').hidden = resultsMode === 'directions'; $('direction-previews').hidden = resultsMode !== 'directions';
  $('show-directions').hidden = active?.status !== 'awaiting_direction'; $('show-directions').textContent = resultsMode === 'directions' ? 'Back to preview' : 'Choose a direction';
  if (resultsMode === 'directions') renderDirections();
  for (const mode of ['draft','baseline','compare']) { $('show-' + mode).setAttribute('aria-pressed',String(mode === comparison)); $('show-' + mode).disabled = mode !== 'baseline' && !preview; }
  $('draft-preview-placeholder').hidden = !!baseline || !!preview;
  $('baseline-slot').hidden = !baseline || comparison === 'draft'; $('draft-slot').hidden = !preview || comparison === 'baseline';
  if (baseline) setFrame($('compare-frame'),baseline); if (preview) setFrame($('draft-frame'),preview);
  $('preview-frames').classList.toggle('comparing',comparison === 'compare'); $('preview-frames').classList.toggle('mobile',viewport === 'mobile');
  for (const mode of ['desktop','mobile']) $(mode).setAttribute('aria-pressed',String(mode === viewport));
  const url = comparison === 'baseline' ? baseline : preview; $('open-preview').hidden = !url; if (url) $('open-preview').href = safeLink(url);
  $('preview-caption').textContent = !url ? 'Your starting page will appear here' : comparison === 'baseline' ? `Baseline · ${active?.baseVersion || base?.version}` : comparison === 'compare' ? 'Baseline and your draft' : 'Your landing preview';
  $('preview-revision').textContent = preview ? `Revision ${previewRevision || active.readyRevision}${operating() ? ' · updating' : ''}` : '';
  const revisions = (active?.revisions || []).filter(r => r.status === 'ready');
  const signature = JSON.stringify([active?.id,revisions.map(r => r.number),active?.readyRevision]);
  if (contentSignatures.get('revision-options') !== signature) { contentSignatures.set('revision-options',signature); $('preview-revision-select').replaceChildren(new Option('Latest' + (active?.readyRevision ? ' · ' + active.readyRevision : ''),'')); for (const revision of revisions.filter(r => r.number !== active.readyRevision).reverse()) $('preview-revision-select').append(new Option('Revision ' + revision.number,String(revision.number))); }
  $('preview-revision-select').hidden = revisions.length < 2; $('preview-revision-select').value = previewRevision || '';
}
function renderDirections() {
  const signature = JSON.stringify([active.directionSetId,busy]); if (contentSignatures.get('directions') === signature) return; contentSignatures.set('directions',signature);
  const host = $('direction-previews'); host.replaceChildren(el('h2','','Choose a direction'),el('p','small muted','Explore the layouts. Final imagery is generated after you choose.'));
  const grid = el('div','direction-grid');
  for (const direction of active.directions || []) {
    const card = el('article','direction-card'); card.append(el('h3','',direction.label),el('p','small muted',firstSentence(direction.hypothesis,160)));
    const frame = el('iframe'); frame.title = direction.label + ' preview'; frame.setAttribute('sandbox','allow-scripts allow-same-origin'); setFrame(frame,direction.previewUrl); card.append(frame,link('Open full preview ↗',direction.previewUrl));
    const choose = button('Choose this direction',guarded(() => action('select_direction',{directionSetId:active.directionSetId,directionId:direction.id})),'primary'); choose.disabled = busy; card.append(choose); grid.append(card);
  }
  host.append(grid);
}

let editorBaseRevision = 0, editorDefinition = null;
function editingDefinition() {
  if (active.editing) return active.editing;
  const groups = {'kittl-hero':'hero','hero-carousel':'hero','quiz-hero':'hero','video-cta':'videoCta','inline-cta':'inlineCta','final-cta':'finalCta'};
  return {sections:(active.landing?.sections || []).map(section => ({...section,label:section.component.replaceAll('-',' '),visible:true,copyKey:groups[section.component],fields:['headline','subhead','ctaLabel','reassurance'].filter(name => section[name] !== undefined).map(name => ({name,label:{headline:'Headline',subhead:'Supporting copy',ctaLabel:'CTA label',reassurance:'Reassurance'}[name],value:section[name],multiline:name !== 'ctaLabel'})),layoutOptions:[],mediaSlots:[]})),order:[],omit:[],presets:[],maxOmit:2};
}
function hasEditorChanges() { return Object.keys(editorChanges).length > 0; }
function pruneChanges() {
  for (const key of Object.keys(editorChanges.copy || {})) if (!Object.keys(editorChanges.copy[key]).length) delete editorChanges.copy[key];
  if (editorChanges.copy && !Object.keys(editorChanges.copy).length) delete editorChanges.copy;
  if (editorChanges.layout && !Object.keys(editorChanges.layout).length) delete editorChanges.layout;
  if (editorChanges.media && !editorChanges.media.showcase?.length) delete editorChanges.media;
}
function setCopyChange(section,name,value,original) {
  editorChanges.copy ||= {}; editorChanges.copy[section] ||= {};
  if (JSON.stringify(value) === JSON.stringify(original)) delete editorChanges.copy[section][name];
  else editorChanges.copy[section][name] = value;
  pruneChanges();
}
function buttonUnavailable(button,unavailable) { button.dataset.unavailable = String(!!unavailable); button.disabled = busy || !!unavailable; }
function renderEditor() {
  const key = `${active.id}:${active.readyRevision}`;
  if (editorKey === key) return;
  if (editorKey && hasEditorChanges()) {
    $('editor-note').textContent = `A newer revision is available. Your unsaved edits still refer to revision ${editorBaseRevision}.`;
    if (!$('reload-editor')) { const reload = el('button','text-button','Discard edits & load latest revision'); reload.id = 'reload-editor'; reload.type = 'button'; reload.onclick = () => { editorChanges = {}; editorKey = ''; reload.remove(); renderEditor(); }; $('editor-note').after(reload); }
    return;
  }
  $('reload-editor')?.remove(); editorKey = key; editorBaseRevision = active.readyRevision; editorDefinition = editingDefinition(); editorChanges = {};
  editorOrder = [...(editorDefinition.order || [])]; editorOmit = [...(editorDefinition.omit || [])];
  if (!editorOrder.length) editorOrder = (editorDefinition.sections || []).slice(1).filter(section => section.visible !== false).map(section => section.id);
  if (!editorDefinition.sections?.some(section => section.id === editorSection)) editorSection = editorDefinition.sections?.[0]?.id || '';
  $('editor-revision').textContent = 'Revision ' + editorBaseRevision;
  if (editorDefinition.blueprint) { editorSection = editorDefinition.blueprint.blocks[0].id; renderBlueprintEditor(); return; }
  renderOutline(); renderElementFields(); renderPresets();
}
function orderedSections() {
  const sections = editorDefinition?.sections || [], byId = new Map(sections.map(section => [section.id,section]));
  const ids = [sections[0]?.id,...editorOrder,...sections.filter(section => editorOmit.includes(section.id) || !editorOrder.includes(section.id)).map(section => section.id)];
  return [...new Set(ids)].map(id => byId.get(id)).filter(Boolean);
}
function renderOutline() {
  $('section-outline').replaceChildren();
  orderedSections().forEach((section,index) => {
    const row = el('div','section-row' + (editorOmit.includes(section.id) ? ' omitted' : ''));
    const button = el('button','section-button'); button.type = 'button'; button.setAttribute('aria-pressed',String(editorSection === section.id)); button.disabled = busy;
    button.append(el('span','section-index',String(index + 1).padStart(2,'0')),el('span','',section.label || section.component || section.id));
    if (editorOmit.includes(section.id)) button.append(el('span','section-note','Hidden'));
    else if (!section.fields?.length && !section.canReorder && !section.canOmit && !section.mediaSlots?.length) button.append(el('span','section-note','Fixed'));
    button.onclick = () => { editorSection = section.id; renderOutline(); renderElementFields(); }; row.append(button); $('section-outline').append(row);
  });
}
function renderElementFields() {
  $('copy-fields').replaceChildren(); $('layout-fields').replaceChildren(); $('composition-fields').replaceChildren();
  const section = editorDefinition?.sections?.find(item => item.id === editorSection);
  if (!section) return;
  const fields = el('fieldset','copy-section'); fields.append(el('legend','',section.label || section.component));
  if (section.copyKey === 'inlineCta') fields.append(el('p','small muted','These copy changes apply to every inline CTA on this page.'));
  for (const field of section.fields || []) {
    if (!section.copyKey) continue;
    const id = 'field-' + section.id + '-' + field.name, label = el('label','',field.label); label.htmlFor = id;
    const input = el(field.multiline || Array.isArray(field.value) ? 'textarea' : 'input'); input.id = id; input.required = true; input.maxLength = 5000;
    if (input.tagName === 'TEXTAREA') input.rows = field.name === 'headline' ? 3 : 2;
    const original = field.value, value = editorChanges.copy?.[section.copyKey]?.[field.name] ?? original;
    input.value = Array.isArray(value) ? value.join('\n') : value ?? ''; input.disabled = busy;
    input.addEventListener('input',() => setCopyChange(section.copyKey,field.name,Array.isArray(original) ? input.value.split('\n') : input.value,original));
    fields.append(label,input);
  }
  $('copy-fields').append(fields);
  if (section.layoutOptions?.length) {
    const label = el('label','','Hero layout'); label.htmlFor = 'element-layout'; const select = el('select'); select.id = 'element-layout';
    for (const option of section.layoutOptions) select.append(new Option(typeof option === 'string' ? option.replaceAll('-',' ') : option.label,typeof option === 'string' ? option : option.value));
    const original = section.layout || active.landing?.sections?.find(item => item.id === section.id)?.layout || 'copy-first'; select.value = editorChanges.layout?.layout || original; select.disabled = busy;
    select.onchange = () => { editorChanges.layout ||= {}; if (select.value === original) delete editorChanges.layout.layout; else editorChanges.layout.layout = select.value; pruneChanges(); };
    $('layout-fields').append(label,select);
  }
  if (typeof section.stickyCta === 'boolean') {
    const label = el('label','check-label'); const input = el('input'); input.type = 'checkbox'; input.checked = editorChanges.layout?.stickyCta ?? section.stickyCta; input.disabled = busy;
    input.onchange = () => { editorChanges.layout ||= {}; if (input.checked === section.stickyCta) delete editorChanges.layout.stickyCta; else editorChanges.layout.stickyCta = input.checked; pruneChanges(); };
    label.append(input,document.createTextNode('Keep the CTA visible while scrolling')); $('layout-fields').append(label);
  }
  const composition = el('div','composition-controls');
  if (section.canReorder && !editorOmit.includes(section.id)) {
    const index = editorOrder.indexOf(section.id);
    for (const [offset,label] of [[-1,'↑ Move up'],[1,'↓ Move down']]) {
      const button = el('button','',label); button.type = 'button';
      const neighbor = editorDefinition.sections.find(item => item.id === editorOrder[index + offset]);
      buttonUnavailable(button,index < 0 || !neighbor?.canReorder);
      button.onclick = () => { [editorOrder[index],editorOrder[index + offset]] = [editorOrder[index + offset],editorOrder[index]]; saveComposition(); renderOutline(); renderElementFields(); };
      composition.append(button);
    }
  }
  if (section.canOmit) {
    const hidden = editorOmit.includes(section.id), button = el('button','',hidden ? 'Restore section' : 'Hide section'); button.type = 'button'; buttonUnavailable(button,!hidden && editorOmit.length >= (editorDefinition.maxOmit ?? 2));
    button.onclick = () => {
      if (hidden) {
        editorOmit = editorOmit.filter(id => id !== section.id);
        const baseOrder = editorDefinition.sections.map(item => item.id), baseIndex = baseOrder.indexOf(section.id);
        const nextId = baseOrder.slice(baseIndex + 1).find(id => editorOrder.includes(id)), position = nextId ? editorOrder.indexOf(nextId) : editorOrder.length;
        editorOrder.splice(position,0,section.id);
      } else { editorOmit.push(section.id); editorOrder = editorOrder.filter(id => id !== section.id); }
      saveComposition(); renderOutline(); renderElementFields();
    };
    composition.append(button);
  }
  if (composition.childNodes.length) $('composition-fields').append(composition);
  for (const slot of section.mediaSlots || []) renderMediaSlot(slot,fields);
  const editable = section.fields?.length || section.layoutOptions?.length || typeof section.stickyCta === 'boolean' || section.canOmit || section.canReorder || section.mediaSlots?.length;
  if (!editable) fields.append(el('p','small muted','This section is fixed in the selected baseline.'));
}
function saveComposition() {
  const composition = {order:editorOrder.filter(id => !editorOmit.includes(id)),omit:[...editorOmit]};
  const previous = {order:editorDefinition.order || [],omit:editorDefinition.omit || []};
  if (JSON.stringify(composition) === JSON.stringify(previous)) delete editorChanges.composition; else editorChanges.composition = composition;
}
function renderMediaSlot(slot,fieldset) {
  const wrap = el('div','media-slot'); wrap.append(el('strong','',slot.label || `${slot.group} · slot ${slot.slot}`));
  if (slot.plan) {
    const current = editorChanges.media?.showcase?.find(plan => plan.group === slot.group && plan.slot === slot.slot) || slot.plan;
    const id = 'media-' + String(slot.group).replace(/[^a-z0-9]/gi,'-') + '-' + slot.slot;
    const label = el('label','','Image direction'); label.htmlFor = id; const input = el('textarea'); input.id = id; input.rows = 3; input.value = current.brief || ''; input.maxLength = 5000; input.disabled = busy;
    const mediumLabel = el('label','','Visual style'); mediumLabel.htmlFor = id + '-medium'; const select = el('select'); select.id = id + '-medium'; select.disabled = busy;
    for (const medium of ['flat-vector','lettering','photo','illustration','3d-icons','mockup','ugc-selfie','ugc-candid','ugc-unboxing','lifestyle','device-screen']) select.append(new Option(medium.replaceAll('-',' '),medium));
    if (current.medium) select.value = current.medium; else select.prepend(new Option('Keep current style','',true,true));
    const update = () => {
      const plan = {...slot.plan,brief:input.value,medium:select.value || slot.plan.medium};
      editorChanges.media ||= {showcase:[]}; editorChanges.media.showcase = editorChanges.media.showcase.filter(item => !(item.group === slot.group && item.slot === slot.slot));
      if (plan.brief !== slot.plan.brief || plan.medium !== slot.plan.medium) editorChanges.media.showcase.push(plan); pruneChanges();
    };
    input.oninput = update; select.onchange = update; wrap.append(label,input,mediumLabel,select);
  }
  const request = el('button','text-button','Describe a change to this image ↗'); request.type = 'button'; request.disabled = busy;
  request.onclick = () => { $('goal-prompt').value = `Update the image in ${slot.group}, slot ${slot.slot}: `; $('goal-prompt').focus(); cacheInput(); updateComposer(); };
  wrap.append(request); fieldset.append(wrap);
}
function renderPresets() {
  const presets = editorDefinition?.presets || []; $('preset-area').hidden = !presets.length; $('layout-presets').replaceChildren();
  for (const preset of presets) {
    const button = el('button','preset-button',preset.label); button.type = 'button'; button.disabled = busy;
    button.onclick = () => {
      if (!preset.changes) return;
      editorChanges = mergeObjects(editorChanges,preset.changes);
      if (preset.changes.composition) { editorOrder = [...(preset.changes.composition.order || editorOrder)]; editorOmit = [...(preset.changes.composition.omit || editorOmit)]; }
      renderOutline(); renderElementFields(); $('editor-note').textContent = `${preset.label} selected. Save edits to create a new revision.`;
    };
    $('layout-presets').append(button);
  }
}
function mergeObjects(base,patch) {
  const result = {...base}; for (const [key,value] of Object.entries(patch)) result[key] = value && typeof value === 'object' && !Array.isArray(value) ? mergeObjects(result[key] || {},value) : value; return result;
}

function renderBlueprintEditor() {
  const page = editorChanges.page || editorDefinition.blueprint;
  $('preset-area').hidden = true; $('section-outline').replaceChildren();
  const update = (id,field,value) => { editorChanges.page ||= structuredClone(editorDefinition.blueprint); editorChanges.page.blocks.find(b => b.id === id)[field] = value; };
  page.blocks.forEach((block,index) => {
    const row = el('div','section-row'); const button = el('button','section-button',`${index + 1}. ${block.kind} · ${block.headline}`); button.type = 'button'; button.setAttribute('aria-pressed',String(editorSection === block.id)); button.disabled = busy; button.onclick = () => { editorSection = block.id; renderBlueprintEditor(); }; row.append(button); $('section-outline').append(row);
  });
  for (const id of ['copy-fields','layout-fields','composition-fields']) $(id).replaceChildren();
  const block = page.blocks.find(b => b.id === editorSection); if (!block) return;
  if (block.kind === 'proof') { $('copy-fields').append(el('p','small muted','Verified proof is preserved from the baseline.')); return; }
  for (const [key,label] of [['headline','Headline'],['body','Supporting copy'],...(block.kind === 'hero' || block.kind === 'cta' ? [['ctaLabel','CTA label']] : [])]) {
    const wrapper = el('label','',label); const input = el(key === 'body' ? 'textarea' : 'input'); input.value = block[key] || ''; input.maxLength = key === 'body' ? 2000 : 200; input.disabled = busy; input.oninput = () => update(block.id,key,input.value); wrapper.append(input); $('copy-fields').append(wrapper);
  }
  (block.items || []).forEach((item,index) => {
    for (const key of ['title','body']) { const label = el('label','',`Item ${index + 1} ${key}`); const input = el(key === 'body' ? 'textarea' : 'input'); input.value = item[key]; input.maxLength = key === 'body' ? 1500 : 200; input.disabled = busy; input.oninput = () => { const items = structuredClone((editorChanges.page || page).blocks.find(b => b.id === block.id).items); items[index][key] = input.value; update(block.id,'items',items); }; label.append(input); $('copy-fields').append(label); }
  });
  if (block.kind === 'hero') {
    const label = el('label','','Hero arrangement'); const select = el('select'); for (const [value,text] of [['split','Side by side'],['centered','Centered'],['media-first','Visual first']]) select.append(new Option(text,value)); select.value = block.layout; select.disabled = busy; select.onchange = () => update(block.id,'layout',select.value); label.append(select); $('layout-fields').append(label);
  }
  const index = page.blocks.indexOf(block);
  if (index > 0 && index < page.blocks.length - 1) { const label = el('label','check-label'); const box = el('input'); box.type = 'checkbox'; box.checked = !block.hidden; box.onchange = () => update(block.id,'hidden',!box.checked); label.append(box,document.createTextNode('Show this section')); $('composition-fields').append(label); }
  if (index > 0 && index < page.blocks.length - 1) for (const [delta,label] of [[-1,'Move up'],[1,'Move down']]) {
    const button = el('button','small-button',label); button.type = 'button'; buttonUnavailable(button,index + delta === 0 || index + delta === page.blocks.length - 1); button.onclick = () => { editorChanges.page ||= structuredClone(page); const rows = editorChanges.page.blocks; [rows[index],rows[index + delta]] = [rows[index + delta],rows[index]]; renderBlueprintEditor(); }; $('composition-fields').append(button);
  }
  for (const plan of page.media?.showcase?.filter(item => item.group === block.id) || []) {
    const label = el('label','',`Image ${plan.slot + 1} brief`); const input = el('textarea'); input.value = plan.brief || plan.prompt || ''; input.maxLength = plan.brief ? 400 : 1200; input.disabled = busy; input.oninput = () => { editorChanges.page ||= structuredClone(page); const target = editorChanges.page.media.showcase.find(item => item.group === block.id && item.slot === plan.slot); target[plan.brief ? 'brief' : 'prompt'] = input.value; }; label.append(input); $('layout-fields').append(label);
  }
  if (['hero','gallery','comparison'].includes(block.kind)) { const button = el('button','text-button','Replace an image with AI…'); button.type = 'button'; button.onclick = () => { $('change-level').value = 'medium'; $('goal-prompt').value = `Replace the image in section ${block.id}, slot 0: `; $('goal-prompt').focus(); }; $('layout-fields').append(button); }
}


$('toggle-history').onclick = () => { const open = $('history-menu').hidden; $('history-menu').hidden = !open; $('toggle-history').setAttribute('aria-expanded',String(open)); if (open) refreshHistory().catch(e => error(e.message)); };
$('new-version').onclick = guarded(() => fresh());
$('baseline').onchange = selectBaseline;
$('creative-search').oninput = () => { creativePage = 0; renderCreatives(); };
$('creative-prev').onclick = () => { creativePage = Math.max(0,creativePage - 1); renderCreatives(); };
$('creative-next').onclick = () => { creativePage++; renderCreatives(); };
$('clear-selection').onclick = () => { selected.clear(); $('adset').value = ''; renderCreatives(); };
$('adset').onchange = () => { const group = catalog.adsets.find(row => row.id === $('adset').value), uploads = catalog.creatives.filter(row => row.source === 'upload' && selected.has(row.id)).map(row => row.id); selected = new Set([...uploads,...(group?.creativeIds || [])]); if (uploads.length) $('adset').value = ''; creativePage = 0; renderCreatives(); };
$('attach-creatives').onclick = () => { $('creative-picker').open = true; $('creative-picker').scrollIntoView({block:'start'}); $('upload-files').focus({preventScroll:true}); };
$('upload-files').onchange = event => uploadFiles(event.target.files);
for (const name of ['dragenter','dragover']) $('upload-zone').addEventListener(name,event => { event.preventDefault(); $('upload-zone').classList.add('dragging'); });
for (const name of ['dragleave','drop']) $('upload-zone').addEventListener(name,event => { event.preventDefault(); $('upload-zone').classList.remove('dragging'); });
$('upload-zone').addEventListener('drop',event => uploadFiles(event.dataTransfer.files));
for (const id of ['research-country','research-ad-urls','research-landing-urls','research-competitors','research-exclusions']) $(id).addEventListener('input',updateResearchSummary);
document.querySelectorAll('.competitor-options input').forEach(input => input.addEventListener('change',updateResearchSummary));
$('goal-prompt').oninput = () => { cacheInput(); updateComposer(); };
$('change-level').onchange = updateComposer;
$('message-form').onsubmit = event => { event.preventDefault(); sendMessage(); };
$('goal-prompt').addEventListener('keydown',event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(); } });
$('retry').onclick = guarded(() => action('retry'));
$('generate-saved').onclick = guarded(() => action('generate',{instruction:active.conversation?.agreedBrief || active.goalPrompt || 'Create a landing from the selected creatives.',expectedRevision:0,changeLevel:$('change-level').value}));
$('publish').onclick = guarded(() => action('publish',{expectedRevision:active.readyRevision}));
$('refresh-publication').onclick = guarded(() => action('prepare_publish',{expectedRevision:active.readyRevision}));
$('use-baseline').onclick = guarded(() => fresh(active.variant));
$('edit-page').onclick = () => { if (!active || busy || previewRevision || !editableDraft()) return; renderPreview(); $('editor-card').hidden = false; $('editor-card').open = true; renderEditor(); setWorkspaceTab('chat'); $('editor-card').scrollIntoView({block:'start'}); $('editor-card').querySelector('summary').focus(); };
$('copy-form').onsubmit = async event => {
  event.preventDefault(); pruneChanges(); if (!hasEditorChanges()) { $('editor-note').textContent = 'Edit a field or move a section before saving.'; return; }
  for (const fields of Object.values(editorChanges.copy || {})) for (const value of Object.values(fields)) if (Array.isArray(value) ? !value.length || value.some(line => !line.trim()) : !String(value).trim()) { error('Copy fields cannot be empty.'); return; }
  try { await action('revise',{changes:structuredClone(editorChanges),changeLevel:'medium',expectedRevision:editorBaseRevision || active.readyRevision}); editorChanges = {}; $('editor-card').open = false; }
  catch(e) { error(e.message); }
};
for (const mode of ['draft','baseline','compare']) $('show-' + mode).onclick = () => showComparison(mode);
for (const mode of ['desktop','mobile']) $(mode).onclick = () => { viewport = mode; renderPreview(); };
$('preview-revision-select').onchange = () => { previewRevision = Number($('preview-revision-select').value) || null; comparison = 'draft'; renderPreview(); updateComposer(); };
$('show-directions').onclick = () => { resultsMode = resultsMode === 'directions' ? 'preview' : 'directions'; renderPreview(); };
$('fullscreen').onclick = guarded(() => document.fullscreenElement ? document.exitFullscreen() : $('results-pane').requestFullscreen());
document.querySelectorAll('[data-workspace-tab]').forEach(node => { node.onclick = () => setWorkspaceTab(node.dataset.workspaceTab); });
$('new-activity').onclick = () => { $('chat-scroll').scrollTop = $('chat-scroll').scrollHeight; $('new-activity').hidden = true; };
$('chat-scroll').addEventListener('scroll',() => { const node = $('chat-scroll'); if (node.scrollHeight - node.scrollTop - node.clientHeight < 80) $('new-activity').hidden = true; },{passive:true});
document.addEventListener('click',event => { if (!event.target.closest('.draft-switcher')) { $('history-menu').hidden = true; $('toggle-history').setAttribute('aria-expanded','false'); } });
document.addEventListener('keydown',event => { if (event.key === 'Escape' && !$('history-menu').hidden) { $('history-menu').hidden = true; $('toggle-history').setAttribute('aria-expanded','false'); $('toggle-history').focus(); } });
function setPaneWidth(width) { if (innerWidth < 900) return; const max = Math.min(560,innerWidth * .48), value = Math.round(Math.max(320,Math.min(max,width))); document.documentElement.style.setProperty('--chat-width',value + 'px'); $('pane-divider').setAttribute('aria-valuenow',String(value)); $('pane-divider').setAttribute('aria-valuemax',String(Math.floor(max))); try { localStorage.setItem('growth-chat-width',String(value)); } catch {} }
$('pane-divider').addEventListener('pointerdown',event => { event.preventDefault(); $('pane-divider').setPointerCapture(event.pointerId); document.body.classList.add('resizing'); });
$('pane-divider').addEventListener('pointermove',event => { if ($('pane-divider').hasPointerCapture(event.pointerId)) setPaneWidth(event.clientX); });
for (const event of ['pointerup','pointercancel','lostpointercapture']) $('pane-divider').addEventListener(event,() => document.body.classList.remove('resizing'));
$('pane-divider').addEventListener('keydown',event => { if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) { event.preventDefault(); const current = Number($('pane-divider').getAttribute('aria-valuenow')); setPaneWidth(event.key === 'Home' ? 320 : event.key === 'End' ? 560 : current + (event.key === 'ArrowLeft' ? -20 : 20)); } });
window.addEventListener('resize',() => { if (innerWidth >= 900) setPaneWidth(Number($('pane-divider').getAttribute('aria-valuenow'))); });
setInterval(() => document.querySelectorAll('[data-started]').forEach(node => { if (node.dataset.started) node.textContent = elapsed(node.dataset.started) + ' · progress is saved'; }),1000);
setInterval(() => { if (active && (busy || !streamLive)) refreshActive().catch(() => connection('reconnecting','Reconnecting…')); },1500);
window.addEventListener('beforeunload',() => { cacheInput(); stopStream(); });
(async () => {
  try { setPaneWidth(Number(localStorage.getItem('growth-chat-width')) || 400); } catch {}
  await Promise.all([refreshCatalog(),refreshHistory()]); updateResearchSummary(); restoreInput();
  let id = new URLSearchParams(location.search).get('draft'); try { id ||= localStorage.getItem('creative-landing-draft'); } catch {}
  if (id) { try { await openDraft(id); } catch { await fresh(); } } else { selectBaseline(); updateComposer(); }
})().catch(e => error(e.message));
