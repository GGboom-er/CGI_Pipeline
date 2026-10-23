const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = html.indexOf('async function loadTasks()');
const end = html.indexOf('// ════════════ 报告面板', start);
const code = html.slice(start, end);
let active = false, count = 0, resolve, reject;
const elements = {
    'panel-tasks': { classList: { contains: () => active } },
    'task-cards': { innerHTML: '' }, 'tasks-empty': { style: {} }
};
const context = vm.createContext({ console, document: { getElementById: id => elements[id] },
    fetch: () => { count++; return new Promise((yes, no) => { resolve = yes; reject = no; }); }
});
vm.runInContext('let tasksRefreshInFlight = false;\n' + code, context);
(async () => {
    await vm.runInContext('loadTasks()', context); assert.equal(count, 0);
    active = true;
    const first = vm.runInContext('loadTasks()', context);
    const second = vm.runInContext('loadTasks()', context);
    assert.equal(count, 1);
    resolve({ json: async () => ({ tasks: [] }) }); await Promise.all([first, second]);
    const third = vm.runInContext('loadTasks()', context); assert.equal(count, 2);
    reject(new Error('intentional offline probe')); await third;
    const fourth = vm.runInContext('loadTasks()', context); assert.equal(count, 3);
    resolve({ json: async () => ({ tasks: [] }) }); await fourth;
    active = false; await vm.runInContext('loadTasks()', context); assert.equal(count, 3);
    console.log('PASS: hidden panel makes no requests; one in-flight refresh; error releases guard');
})().catch(error => { console.error(error); process.exitCode = 1; });
