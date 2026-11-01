// Exercise the copied site's real contact-form code with a minimal DOM fixture.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(response = { ok: true, json: async () => ({ ok: true }) }) {
    const handlers = {};
    const status = { textContent: '', className: '' };
    const field = value => ({ value, maxLength: 5000, style: {}, scrollHeight: 30,
        setAttribute() {}, removeAttribute() {}, addEventListener() {}, focus() {} });
    const fields = { name: field('Demo'), email: field('demo@example.com'), subject: field('Test'), message: field('Synthetic') };
    const button = { innerHTML: 'Save', disabled: false, classList: { add() {}, remove() {} } };
    const honeypot = field('');
    const form = { querySelector: selector => selector === '.btn-submit' ? button : null,
        addEventListener: (name, fn) => { handlers[name] = fn; },
        setAttribute() {}, removeAttribute() {}, reset() { Object.values(fields).forEach(f => f.value = ''); } };
    const calls = [];
    const fakeFetch = async (...args) => { calls.push(args); return response; };
    const context = { contactForm: form, window: { fetch: fakeFetch }, fetch: fakeFetch,
        $: selector => ({ '#formStatus': status, '#bot-field': honeypot,
            '#name': fields.name, '#email': fields.email, '#subject': fields.subject, '#message': fields.message })[selector],
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        setTimeout: () => 1, clearTimeout() {}, AbortController, console, Date };
    const source = fs.readFileSync('app/site/js/main.js', 'utf8');
    const start = source.indexOf('    function initContactForm()');
    const end = source.indexOf('    initContactForm();', start);
    assert.ok(start >= 0 && end > start);
    vm.runInNewContext(source.slice(start, end) + '\ninitContactForm();', context);
    return { handlers, status, fields, button, calls };
}

test('submits synthetic JSON and accurately reports a saved test record', async () => {
    const f = setup();
    await f.handlers.submit({ preventDefault() {} });
    assert.equal(f.calls.length, 1);
    assert.equal(f.calls[0][0], '/api/contact');
    assert.deepEqual(JSON.parse(f.calls[0][1].body), { name: 'Demo', email: 'demo@example.com', subject: 'Test', message: 'Synthetic', honeypot: '' });
    assert.match(f.status.textContent, /saved to the database.*No message was sent/);
});

test('rejects real email addresses without a network request', async () => {
    const f = setup();
    f.fields.email.value = 'person@real-domain.com';
    await f.handlers.submit({ preventDefault() {} });
    assert.equal(f.calls.length, 0);
    assert.match(f.status.textContent, /example\.com/);
});

test('reports a failure and keeps the draft when API fails', async () => {
    const f = setup({ ok: false, status: 503, json: async () => ({ status: 'error' }) });
    await f.handlers.submit({ preventDefault() {} });
    assert.match(f.status.textContent, /could not be saved/);
    assert.equal(f.fields.message.value, 'Synthetic');
    assert.equal(f.button.disabled, false);
});
