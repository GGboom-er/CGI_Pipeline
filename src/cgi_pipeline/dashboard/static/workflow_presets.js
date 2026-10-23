/* Registered presets are server-compiled, read-only plans, never literal batches. */
window.createWorkflowPresets = function ({ getEditor, log, submitted }) {
    const select = document.getElementById('preset-select');
    const panel = document.getElementById('workflow-settings');
    let workflowId = '', plan = null, controls = {}, requestId = 0, running = false;
    let fields = {}, bindings = {}, nodeIds = [], inputContract = {};
    const escape = value => String(value).replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const post = async (url, body) => {
        const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
    };
    function setEnabled() {
        document.getElementById('btn-exec').disabled = running || (!!workflowId && !plan);
        select.disabled = running;
        panel.querySelectorAll('input,select,textarea').forEach(el => { el.disabled = running; });
    }
    function reset() {
        if (running) return;
        requestId++;
        workflowId = ''; plan = null; controls = {}; fields = {}; bindings = {}; nodeIds = []; inputContract = {};
        select.value = ''; panel.hidden = true; panel.replaceChildren();
        getEditor().editor_mode = 'edit'; getEditor().clear();
        setEnabled();
    }
    function field(container, name, schema, value) {
        const label = document.createElement('label');
        label.textContent = name + (schema.required ? ' *' : '');
        label.title = schema.description || '';
        let input;
        const choices = schema.enum || schema.choices;
        if (Array.isArray(choices)) {
            input = document.createElement('select');
            choices.forEach(choice => {
                const opt = document.createElement('option');
                opt.value = JSON.stringify(choice); opt.textContent = String(choice); input.append(opt);
            });
            input.value = JSON.stringify(value ?? schema.default ?? choices[0]);
        } else if (schema.type === 'boolean') {
            input = document.createElement('input'); input.type = 'checkbox';
            input.checked = value ?? schema.default ?? false;
        } else {
            input = document.createElement(['object', 'array'].includes(schema.type) ? 'textarea' : 'input');
            const initial = value ?? schema.default ?? '';
            input.value = typeof initial === 'object' ? JSON.stringify(initial) : initial;
            input.placeholder = schema.alternatives?.join(' / ') || schema.description || name;
        }
        input.dataset.workflowInput = name;
        label.append(input); container.append(label);
        return { input, schema };
    }
    function valueOf({ input, schema }) {
        if (input.type === 'checkbox') return input.checked;
        if (input.tagName === 'SELECT') return JSON.parse(input.value);
        if (input.value === '') return undefined;
        if (['array', 'object'].includes(schema.type)) return JSON.parse(input.value);
        if (['integer', 'number'].includes(schema.type)) {
            const value = Number(input.value);
            if (!Number.isFinite(value) || (schema.type === 'integer' && !Number.isInteger(value)))
                throw new Error(`${input.dataset.workflowInput} 需要 ${schema.type}`);
            return value;
        }
        return input.value;
    }
    function remember(group) {
        return Object.fromEntries(Object.entries(group).map(([name, item]) => {
            try { return [name, valueOf(item)]; }
            catch (_) { return [name, item.input.value]; }
        }));
    }
    function showForm(data) {
        const previous = remember(fields), previousBindings = remember(bindings);
        inputContract = data.input_contract;
        panel.replaceChildren(); fields = {}; bindings = {};
        const title = document.createElement('strong');
        title.textContent = `${plan.name || workflowId} · 编译结果只读 · ${plan.steps.length} 步`;
        panel.append(title);
        const message = document.createElement('div'); message.id = 'workflow-compile-status';
        message.textContent = '调整开关后由服务器重新编译；业务输入在执行前由正式入口验证。';
        panel.append(message);
        const controlBox = document.createElement('div'); controlBox.className = 'workflow-fields'; panel.append(controlBox);
        Object.entries(data.parameters).forEach(([name, schema]) => {
            const item = field(controlBox, name, schema, controls[name]);
            item.input.addEventListener('change', () => {
                controls[name] = valueOf(item); compile();
            });
        });
        const bindingBox = document.createElement('div'); bindingBox.className = 'workflow-fields'; panel.append(bindingBox);
        ['project', 'asset_name', 'source_path', 'business_task_id', 'workspace_id'].forEach(name => {
            bindings[name] = field(bindingBox, name, { type: 'string', ...inputContract[name] },
                previousBindings[name] ?? (name === 'project' ? 'default' : ''));
        });
        const inputBox = document.createElement('div'); inputBox.className = 'workflow-fields'; panel.append(inputBox);
        Object.entries(data.input_contract).forEach(([name, schema]) => {
            if (!(name in data.parameters) && !(name in bindings))
                fields[name] = field(inputBox, name, schema, previous[name]);
        });
    }
    function drawPlan() {
        const editor = getEditor(); editor.clear(); nodeIds = [];
        plan.steps.forEach((step, index) => {
            const html = `<div class="title-box">${index + 1}. ${escape(step.step_id)}</div>` +
                `<div class="box node-body"><div>${escape(step.api_id)}</div>` +
                `<details><summary>步骤契约</summary><pre>${escape(JSON.stringify(step, null, 2))}</pre></details></div>`;
            const id = editor.addNode(step.api_id, index ? 1 : 0,
                index < plan.steps.length - 1 ? 1 : 0, 60 + index * 310, 80,
                'workflow-step', { ...step }, html);
            nodeIds.push(id);
            if (index) editor.addConnection(nodeIds[index - 1], id, 'output_1', 'input_1');
        });
        editor.editor_mode = 'fixed';
        const guide = document.getElementById('canvas-guide');
        if (guide) guide.classList.add('hidden');
    }
    async function compile() {
        const mine = ++requestId;
        plan = null; getEditor().clear(); getEditor().editor_mode = 'fixed'; setEnabled();
        const status = document.getElementById('workflow-compile-status');
        if (status) status.textContent = '正在编译…';
        try {
            const data = await post('/api/workflows/compile', { workflow_id: workflowId, extra_params: controls });
            if (mine !== requestId) return;
            if (data.status !== 'SUCCESS') throw new Error(data.error || '工作流编译失败');
            plan = data.workflow; controls = { ...plan.effective_parameters };
            showForm(data); drawPlan();
            log('success', `预设 ${workflowId} 已编译：${plan.steps.length} 步`);
        } catch (error) {
            if (mine !== requestId) return;
            plan = null;
            const status = document.getElementById('workflow-compile-status');
            if (status) status.textContent = `不可执行：${error.message}`;
            else panel.textContent = `不可执行：${error.message}`;
            log('error', error.message);
        } finally { if (mine === requestId) setEnabled(); }
    }
    function setPath(root, name, value) {
        const keys = name.split('.'); let current = root;
        keys.forEach((key, index) => {
            if (index === keys.length - 1) current[key] = value;
            else current = current[key] ??= /^\d+$/.test(keys[index + 1]) ? [] : {};
        });
    }
    async function execute() {
        if (!plan || running) { log('error', '预设尚未有效编译'); return; }
        try {
            const extra = { ...controls }, payload = { workflow_id: workflowId, extra_params: extra };
            Object.entries(fields).forEach(([name, item]) => {
                const value = valueOf(item); if (value !== undefined) setPath(extra, name, value);
            });
            Object.entries(bindings).forEach(([name, item]) => {
                const value = valueOf(item); if (value !== undefined) payload[name] = value;
            });
            // Asset resolver inputs share their existing input owner with API callers.
            if (payload.asset_name && inputContract.asset_name) extra.asset_name = payload.asset_name;
            running = true; setEnabled();
            const result = await post('/api/workflows/execute', payload);
            if (!result.dispatched) throw new Error(result.error || result.status || '提交失败');
            submitted(result, plan.steps.map((step, index) => ({ ...step, _dfId: nodeIds[index] })), payload);
        } catch (error) { running = false; setEnabled(); log('error', error.message); }
    }
    async function init() {
        try {
            const response = await fetch('/api/workflows'); const data = await response.json();
            data.workflows.forEach(workflow => {
                const option = document.createElement('option'); option.value = workflow.workflow_id;
                option.textContent = '📌 ' + (workflow.name || workflow.workflow_id); select.append(option);
            });
            select.addEventListener('change', () => {
                if (!select.value) { reset(); return; }
                workflowId = select.value; controls = {}; fields = {}; bindings = {};
                panel.hidden = false; panel.textContent = '正在加载工作流…'; compile();
            });
        } catch (error) { log('error', `预设目录加载失败：${error.message}`); }
    }
    return { init, execute, reset, get active() { return !!workflowId; },
        get valid() { return !!plan; }, setRunning(value) { running = value; setEnabled(); } };
};
