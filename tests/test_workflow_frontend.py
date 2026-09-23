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
