const fs=require('fs'),assert=require('assert/strict'),vm=require('vm');
const html=fs.readFileSync(__dirname+'/../frontend/index.html','utf8');
for(const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi))new vm.Script(m[1]);
const nodes={},$=id=>nodes[id]??={value:''},qsa=()=>[],escapeHtml=String;
const rows=[['A',1,5,2],['B',1,1,1],['A',2,4,0]].map(([user_id,template_version,copies,original_copies])=>({user_id,template_id:'t',title:'模板',template_version,copies,original_copies,opens:1}));
const usageCache={rows,words:[],days:7,totals:{copies:10,original_copies:3}};
eval(html.slice(html.indexOf('function renderUsageView()'),html.indexOf('function seedChips()'))+`
$('chartMode').value='recommended';renderUsageView();
assert.match($('dataVisual').innerHTML,/原文 3 · 完整提示词 3/);
assert.match($('dataVisual').innerHTML,/原文 0 · 完整提示词 4/);
assert.match($('dataVisual').innerHTML,/A<\\/td><td>2<\\/td><td>9<\\/td><td>2<\\/td><td>7/);
assert.match($('usageWords').innerHTML,/v1<\\/td><td>3<\\/td><td>2/);
$('usagePerson').value='A';renderUsageView();assert.match($('usageWords').innerHTML,/v1<\\/td><td>2<\\/td><td>1/);
$('usageWordSearch').value='无匹配';renderUsageWords();assert.match($('usageWords').innerHTML,/暂无原文复制记录/);
`);
console.log('render checks passed');
