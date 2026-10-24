// Export only built-in templates and selling points, never browser-local data.
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const appDir = path.resolve(process.argv[2] || path.join(__dirname, 'frontend'));
const html = fs.readFileSync(path.join(appDir, 'index.html'), 'utf8');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(appDir, 'prompt-data.js'), 'utf8'), context, {timeout: 1000});
const start = html.indexOf('const defaultData = {');
const end = html.indexOf('      let data = clone(defaultData);', start);
if (start < 0 || end < 0) throw new Error('prototype data block not found');
const expression = html.slice(start, end).replace('const defaultData =', 'result =');
context.source = context.window.__PROMPT_SOURCE__;
vm.runInNewContext(expression, context, {timeout: 1000});
const defaults = context.result;
const templates = defaults.templates.map(t => ({...t, body: t.sourceKey ? context.source[t.sourceKey] : (t.body || '')}));
fs.writeFileSync(path.join(__dirname, 'seed.json'), JSON.stringify({points: defaults.points, templates}, null, 2));
console.log('Exported ' + defaults.points.length + ' points and ' + templates.length + ' templates; browser data excluded.');
