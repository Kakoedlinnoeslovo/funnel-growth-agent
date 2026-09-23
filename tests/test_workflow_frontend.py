"""Exercise workspace action boundaries in the real JavaScript, without a browser."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "src/funnel_growth_agent/demo/workflow.js"

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
const editorControls = [{dataset:{}}, {dataset:{unavailable:'true'}}];
function node(id = '') {
  return {
    id, value:'', textContent:'', hidden:false, disabled:false, dataset:{}, children:[],
    classList:{toggle(){},add(){},remove(){}},
    addEventListener(){}, setAttribute(name,value){this[name] = value;},
    getAttribute(name){return this[name] || null;},
    replaceChildren(...children){this.children = children;}, append(...children){this.children.push(...children);},
    querySelectorAll(){return id === 'editing-area' ? editorControls : [];},
    querySelector(){return {focus(){}};}, scrollIntoView(){}, focus(){}
  };
}
const get = id => { if (!nodes.has(id)) nodes.set(id,node(id)); return nodes.get(id); };
const requests = [];
const context = vm.createContext({
  document:{getElementById:get,createElement:()=>node(),querySelectorAll:()=>[],addEventListener(){}},
  window:{addEventListener(){}}, location:{origin:'http://localhost:8787'},
  URL, URLSearchParams, Option:function(text,value){return {text,value};},
  setInterval(){}, clearTimeout(){},
  fetch:async (path,options) => {requests.push({path,options}); return {ok:true,json:async()=>({})};}
});
const source = fs.readFileSync(process.argv[1],'utf8');
// Register all real controls; omit only the network-dependent initial page load.
vm.runInContext(source.slice(0,source.lastIndexOf('(async () => {')),context);
vm.runInContext(`
  active = {id:'draft-one',status:'ready',readyRevision:2,previewUrl:'http://localhost:9999/pm/v8_a1',
    baseVersion:'v8',revisions:[{number:1,status:'ready'},{number:2,status:'ready'}]};
  catalog = {baselines:[]};
  refreshActive = async () => {}; refreshHistory = async () => {};
`,context);
get('change-level').value = 'medium';
get('goal-prompt').value = 'Improve the heading';
"""


def run_frontend(scenario: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for workspace JavaScript regression checks")
    result = subprocess.run(
        [node, "-e", HARNESS + scenario, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_historical_preview_blocks_publish_and_open_editor_mutations():
    run_frontend(r"""
    (async () => {
      get('preview-revision-select').value = '1';
      get('preview-revision-select').onchange();
      for (const id of ['generate','publish','refresh-publication','edit-page','retry','generate-saved']) {
        assert.equal(get(id).disabled,true,id + ' should be disabled for an old preview');
      }
      assert.ok(editorControls.every(control => control.disabled));
      get('editor-card').hidden = true;
      get('edit-page').onclick();
      assert.equal(get('editor-card').hidden,true);
      assert.equal(vm.runInContext('previewRevision',context),1);
      await get('publish').onclick();
      await get('refresh-publication').onclick();
      vm.runInContext("editorChanges = {copy:{hero:{ctaLabel:'Try it'}}}; editorBaseRevision = 2;",context);
      await get('copy-form').onsubmit({preventDefault(){}});
      assert.equal(requests.length,0,'historical preview must never issue a mutation');
      assert.equal(vm.runInContext('editorChanges.copy.hero.ctaLabel',context),'Try it');

      get('preview-revision-select').value = '';
      get('preview-revision-select').onchange();
      assert.equal(get('publish').disabled,false);
      assert.equal(get('edit-page').disabled,false);
      assert.equal(editorControls[0].disabled,false);
      assert.equal(editorControls[1].disabled,true,'unavailable controls must stay disabled');
      await get('publish').onclick();
      assert.equal(requests.length,1);
      assert.equal(requests[0].path,'/api/drafts/draft-one/publish');
      assert.equal(JSON.parse(requests[0].options.body).expectedRevision,2);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_running_job_cannot_leave_the_observed_draft_via_new():
    run_frontend(r"""
    (async () => {
      vm.runInContext('busy = true; updateComposer();',context);
      assert.equal(get('new-version').disabled,true);
      assert.equal(get('goal-prompt').disabled,false,'typing must remain available during work');
      await vm.runInContext('fresh()',context);
      await vm.runInContext("fresh('v8_a1')",context);
      assert.equal(vm.runInContext('active.id',context),'draft-one');
      assert.equal(requests.length,0);
      vm.runInContext('busy = false; updateComposer();',context);
      assert.equal(get('new-version').disabled,false);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_run_progress_names_the_running_tool_and_the_stages_still_ahead():
    run_frontend(r"""
    (async () => {
      const events = [
        {seq:1,operationId:'op-1',kind:'operation_started',stage:'understand',at:'2026-09-23T10:00:00Z',data:{action:'generate'}},
        {seq:2,operationId:'op-1',kind:'tool_call',stage:'understand',at:'2026-09-23T10:00:01Z',
         data:{id:'call-1',name:'get_top_creatives',input:{variant:'v8'}}},
        {seq:3,operationId:'op-1',kind:'tool_result',stage:'understand',at:'2026-09-23T10:00:04Z',
         data:{id:'call-1',name:'get_top_creatives',result:{ok:true}}},
        {seq:4,operationId:'op-1',kind:'step_started',stage:'research',at:'2026-09-23T10:00:05Z',
         data:{id:'capture:one',label:'Capture the reference page',url:'https://example.com/pricing',
               screenshots:['http://localhost:8787/api/assets/shot-a.png']}},
        {seq:5,operationId:'op-1',kind:'tool_call',stage:'research',at:'2026-09-23T10:00:06Z',
         data:{id:'call-2',name:'research_landing',input:{url:'https://competitor.example.com/plans'}}}
      ];
      const runs = vm.runInContext('toolRuns',context)(events);
      assert.equal(runs.length,2);
      assert.equal(runs[0].done,true,'a tool_result must close its tool_call');
      assert.equal(runs[1].done,false,'the unanswered call is the one running now');

      const log = node('tool-log');
      vm.runInContext('renderToolLog',context)(log,events);
      const lines = log.children.map(row => row.children.map(part => part.textContent).join(' '));
      assert.ok(lines.some(line => line.includes('Researching a reference page')),
                'the running tool must be named: ' + lines.join(' / '));
      assert.ok(lines.some(line => line.includes('competitor.example.com')),
                'the running tool must show what it is working on');
      assert.ok(lines.some(line => line.includes('1 tool call finished')),lines.join(' / '));

      // Stage rail: understand is done, research is current, the plan's remaining stages show as ahead.
      const rail = node('stage-rail');
      const position = vm.runInContext('renderStageRail',context)(rail,events,false);
      const states = rail.children.map(item => item.dataset.state);
      const names = rail.children.map(item => item.children.at(-1).textContent);
      assert.deepEqual(names,['Understanding creatives','Researching references','Designing the page',
                              'Creating assets','Building & checking']);
      assert.deepEqual(states,['done','current','pending','pending','pending']);
      assert.equal(position,'stage 2 of 5','the rail must say how much of the run is left');

      // Intermediate visuals are surfaced while the run is still going.
      const shots = vm.runInContext('eventVisuals',context)(events);
      assert.equal(shots.length,1);
      assert.equal(shots[0].url,'http://localhost:8787/api/assets/shot-a.png');
      assert.equal(shots[0].caption,'Capture the reference page');
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_visuals_ignore_unresolved_paths_and_estimates_need_observed_runs():
    run_frontend(r"""
    (async () => {
      // Paths the backend did not resolve to an /api/assets URL must never be rendered.
      const unresolved = vm.runInContext('eventVisuals',context)([
        {seq:1,operationId:'op-1',kind:'tile_candidate',stage:'assets',at:'2026-09-23T10:00:00Z',
         data:{stem:'hero',index:0,path:'/Users/someone/secret/hero.png'}},
        {seq:2,operationId:'op-1',kind:'tile_candidate',stage:'assets',at:'2026-09-23T10:00:01Z',
         data:{stem:'hero',index:1,path:'https://elsewhere.example.com/hero.png'}}
      ]);
      assert.equal(unresolved.length,0,'only backend-resolved asset URLs may be shown');

      const duplicated = vm.runInContext('eventVisuals',context)([
        {seq:1,operationId:'op-1',kind:'tile_candidate',stage:'assets',at:'2026-09-23T10:00:00Z',
         data:{label:'Image candidate',imageUrl:'http://localhost:8787/api/assets/tile.png',
               path:'http://localhost:8787/api/assets/tile.png'}}
      ]);
      assert.equal(duplicated.length,1,'the same artifact must appear once');

      // With fewer than two observed runs of the action there is no expectation to report.
      vm.runInContext(`active = {id:'draft-one',events:[
        {seq:1,operationId:'op-a',kind:'operation_started',at:'2026-09-23T10:00:00Z',data:{action:'generate'}},
        {seq:2,operationId:'op-a',kind:'operation_completed',at:'2026-09-23T10:01:00Z',data:{action:'generate'}}
      ]};`,context);
      assert.equal(vm.runInContext('typicalRun',context)('generate'),null);

      vm.runInContext(`active.events.push(
        {seq:3,operationId:'op-b',kind:'operation_started',at:'2026-09-23T10:02:00Z',data:{action:'generate'}},
        {seq:4,operationId:'op-b',kind:'operation_completed',at:'2026-09-23T10:04:00Z',data:{action:'generate'}},
        {seq:5,operationId:'op-c',kind:'operation_started',at:'2026-09-23T10:05:00Z',data:{action:'research'}},
        {seq:6,operationId:'op-c',kind:'operation_failed',at:'2026-09-23T10:09:00Z',data:{action:'research'}}
      );`,context);
      const typical = vm.runInContext('typicalRun',context)('generate');
      assert.equal(typical.count,2,'only completed runs of the same action count');
      assert.equal(vm.runInContext('humanMs',context)(typical.median),'2m 0s');
      assert.equal(vm.runInContext('typicalRun',context)('research'),null,'a failed run is not an estimate');
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_step_change_summaries_render_as_text_not_objects():
    """A step revision summarises as {kind, label, detail} while a landing revision is a plain
    string. Both must read as a sentence; the object form once rendered as [object Object]."""
    run_frontend(r"""
    (async () => {
      const changeText = vm.runInContext('changeText',context);
      assert.equal(changeText({kind:'step',label:'Your plan',detail:'Name the billing period in the CTA.'}),
                   'Your plan — Name the billing period in the CTA.');
      assert.equal(changeText('Rewrote the hero headline.'),'Rewrote the hero headline.');
      assert.equal(changeText({kind:'step',label:'Your plan'}),'Your plan');
      assert.equal(changeText({summary:'Fallback wording'}),'Fallback wording');
      for (const empty of [null,undefined,{},42]) assert.equal(changeText(empty),'');
      assert.ok(!String(changeText({kind:'step',label:'Your plan',detail:'x'})).includes('object Object'));
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)


def test_research_captures_nested_in_a_saved_record_still_reach_the_strip():
    """Research attaches its screenshots under the saved record, not at the top of the event.
    Those captures are the 'what has it actually looked at' the feed is there to show."""
    run_frontend(r"""
    (async () => {
      const shots = vm.runInContext('eventVisuals',context)([
        {seq:1,operationId:'op-1',kind:'research_result',stage:'research',at:'2026-09-23T10:00:00Z',
         data:{stage:'research',record:{
           url:'https://competitor.example.com/pricing',
           label:'Reference pricing page',
           screenshots:['http://localhost:8787/api/assets/desktop.png',
                        'http://localhost:8787/api/assets/phone.png'],
           journey:[{kind:'landing',url:'https://competitor.example.com/pricing'}]}}},
        {seq:2,operationId:'op-1',kind:'tile_judged',stage:'assets',at:'2026-09-23T10:01:00Z',
         data:{stem:'hero',chosenPath:'http://localhost:8787/api/assets/hero.webp',
               candidates:['http://localhost:8787/api/assets/cand-1.webp']}},
        {seq:3,operationId:'op-1',kind:'step_completed',stage:'understand',at:'2026-09-23T10:02:00Z',
         data:{label:'Creative clip',videoPath:'http://localhost:8787/api/assets/clip.mp4'}}
      ]);
      const urls = shots.map(s => s.url.split('/').pop());
      assert.deepEqual([...urls].sort(),
        ['cand-1.webp','clip.mp4','desktop.png','hero.webp','phone.png']);
      assert.equal(shots.find(s => s.url.endsWith('desktop.png')).caption,'Reference pricing page',
                   'the nested record label captions its own captures');
      assert.equal(shots.find(s => s.url.endsWith('clip.mp4')).kind,'video');
      assert.ok(shots.find(s => s.url.endsWith('cand-1.webp')).caption.includes('candidate'));

      // A deeply self-referential payload must terminate rather than hang the feed.
      const loop = {label:'Deep'}; loop.self = loop;
      assert.equal(vm.runInContext('eventVisuals',context)(
        [{seq:1,operationId:'op-1',kind:'x',stage:'research',at:'2026-09-23T10:00:00Z',data:loop}]
      ).length,0);
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """)
