// Pure UI-state probe; browser rendering is verified separately against the live app.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
class Element {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
        this.listeners = {}; this.value = ''; this.type = ''; this.classList = { add() {} };
    }
    append(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    addEventListener(event, callback) { this.listeners[event] = callback; }
    querySelectorAll() {
        return this.children.flatMap(child => [child, ...child.querySelectorAll()])
            .filter(child => ['INPUT', 'SELECT', 'TEXTAREA'].includes(child.tagName));
    }
}
const elements = Object.fromEntries(['preset-select', 'workflow-settings', 'btn-exec', 'canvas-guide']
    .map(id => [id, new Element()]));
function find(element, predicate) {
    if (predicate(element)) return element;
    return element.children.map(child => find(child, predicate)).find(Boolean);
}
const document = { createElement: tag => new Element(tag), getElementById(id) {
    return elements[id] || find(elements['workflow-settings'], el => el.id === id) || null;
} };
const calls = [], logs = [];
let submitted;
const editor = { nodes: [], clear() { this.nodes = []; }, addNode(...args) {
    this.nodes.push(args); return this.nodes.length;
}, addConnection() {} };
const fetch = async (url, options) => {
    calls.push({ url, body: options ? JSON.parse(options.body) : null });
    if (url === '/api/workflows') return { ok: true, json: async () => ({ workflows: fixtures.list }) };
    if (url === '/api/workflows/execute') return { ok: true, json: async () => ({
        status: 'SUBMITTED', dispatched: true, task_id: 'wf-probe' }) };
    assert.equal(url, '/api/workflows/compile');
    const body = JSON.parse(options.body), controls = body.extra_params;
    const match = fixtures.plans.find(item => item.workflow.workflow_id === body.workflow_id &&
        Object.entries(controls).every(([key, value]) => item.workflow.effective_parameters[key] === value));
    return { ok: true, json: async () => match || ({ status: 'ERROR', error: 'mode=check does not allow save_result=true' }) };
};
const window = {};
vm.runInNewContext(fs.readFileSync(process.argv[3], 'utf8'), { window, document, fetch, console });
const ui = window.createWorkflowPresets({ getEditor: () => editor, log: (...args) => logs.push(args),
    submitted: (...args) => { submitted = args; } });
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const input = name => find(elements['workflow-settings'], el => el.dataset.workflowInput === name);
async function choose(id) {
    elements['preset-select'].value = id; elements['preset-select'].listeners.change(); await tick();
}
(async () => {
    await ui.init(); assert.equal(elements['preset-select'].children.length, 2);
    await choose('blender_to_maya_full_build');
    assert.equal(editor.nodes.length, 5); assert.equal(editor.editor_mode, 'fixed');
    const pack = input('pack_textures'); assert.equal(pack.type, 'checkbox'); assert.equal(pack.checked, false);
    pack.checked = true; pack.listeners.change();
    assert.equal(ui.valid, false); assert.equal(elements['btn-exec'].disabled, true);
    assert.equal(editor.nodes.length, 0); await tick();
    assert.equal(editor.nodes.length, 6); assert.equal(ui.valid, true);
    input('project').value = 'ysj'; input('asset_name').value = 'xiangC';
    input('business_task_id').value = 'PL272'; input('workspace_id').value = 'existing-workspace';
    await ui.execute();
    const sent = calls.at(-1);
    assert.equal(sent.url, '/api/workflows/execute');
    assert.equal(sent.body.extra_params.pack_textures, true);
    assert.equal(sent.body.project, 'ysj'); assert.equal(sent.body.workspace_id, 'existing-workspace');
    // Source-resolution workflows consume asset_name at the submission level.
    assert.equal(sent.body.extra_params.asset_name, undefined);
    assert.equal(submitted[1].length, 6);
    assert.equal(submitted[1][0].step_id, fixtures.plans[1].workflow.steps[0].step_id);
    ui.setRunning(false);
    await choose('tex_to_rig_verify_and_sync'); assert.equal(editor.nodes.length, 3);
    const saved = input('save_result'); saved.checked = true; saved.listeners.change(); await tick();
    assert.equal(ui.valid, false); assert.equal(editor.nodes.length, 0);
    assert.equal(elements['btn-exec'].disabled, true);
    assert.match(document.getElementById('workflow-compile-status').textContent, /不可执行/);
    const count = calls.length; await ui.execute(); assert.equal(calls.length, count);
    const mode = input('mode'); mode.value = JSON.stringify('geometry'); mode.listeners.change(); await tick();
    assert.equal(ui.valid, true); assert.equal(editor.nodes.length, 10);
    for (const [value, save, count] of [['geometry', false, 9], ['geometry_materials', false, 11], ['geometry_materials', true, 12]]) {
        input('mode').value = JSON.stringify(value); input('mode').listeners.change(); await tick();
        input('save_result').checked = save; input('save_result').listeners.change(); await tick();
        assert.equal(editor.nodes.length, count);
    }
    ui.reset(); assert.equal(ui.active, false); assert.equal(editor.editor_mode, 'edit');
    assert.ok(!calls.some(call => ['/api/worker_status', '/api/execute_graph'].includes(call.url)));
    console.log('PASS: typed controls, all 7 legal plans, invalid-state clearing, formal submission and manual reset');
})().catch(error => { console.error(error); process.exitCode = 1; });
