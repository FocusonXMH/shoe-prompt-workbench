const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync(__dirname+'/../frontend/index.html','utf8');
for(const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi))if(!/application\/json/.test(m[1]))new vm.Script(m[2]);
const src=html.slice(html.indexOf('async function writePromptClipboard'),html.indexOf('function fallbackCopy'));
const recorder=html.slice(html.indexOf('function recordEvent('),html.indexOf('function recordEvent(')+html.slice(html.indexOf('function recordEvent(')).indexOf('\n      }\n')+8);
(async()=>{for(const original of [true,false])for(const primary of [true,false])for(const fallback of [true,false]){
 const events=[],messages=[];const ctx={state:{activeTemplate:'test',activeTemplateData:{version:3,fields:['scene']},generatedValues:{scene:'sample'}},cloudReady:true,ShoeCloud:{event:async v=>events.push(v)},navigator:{clipboard:{writeText:async()=>{if(!primary)throw Error()}}},fallbackCopy:()=>fallback,templateBody:()=>'test',toast:v=>messages.push(v),$:()=>({querySelector:()=>false}),userList:()=>[],saveData(){},updatePersonalSummaries(){}};
 await vm.runInNewContext(recorder+'\n'+src+'\n'+(original?'copyTemplateOriginal()':'copyText("text")'),ctx);
 assert.equal(events.length,primary||fallback?1:0);if(events.length){assert.equal(events[0].original_copy,original);assert.equal(events[0].field_values.length,original?0:1);assert.equal(events[0].template_version,3)}
}console.log('8 clipboard scenarios passed');})().catch(e=>{console.error(e);process.exitCode=1});
