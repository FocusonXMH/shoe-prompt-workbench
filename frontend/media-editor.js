/* Plain text + owned media anchors. Never persist pasted HTML. */
(function(){
  'use strict';
  const fields=new WeakMap(),inputs=new WeakMap(),live=new Set();
  const marker=/\[\[media:([0-9a-f-]{36})\]\]/gi;
  const allowed=['image/png','image/jpeg','video/mp4','video/webm'];
  const escape=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const key=f=>[f.name,f.size,f.type,f.lastModified].join('|');
  function media(id,type,url){
    return (type||'').startsWith('video/')?'<video controls preload="none" src="'+escape(url)+'" aria-label="参考视频"></video>':'<a href="'+escape(url)+'" target="_blank" rel="noopener"><img src="'+escape(url)+'" loading="lazy" alt="参考图片，点击查看原图"></a>';
  }
  function render(text,ids,types,urlFor,tail=true){
    const known=new Set(ids||[]),used=new Set();let last=0,result='';
    for(const match of String(text||'').matchAll(marker)){
      result+=escape(String(text).slice(last,match.index));const id=match[1];
      result+=known.has(id)?'<figure class="document-media">'+media(id,(types||{})[id],urlFor(id))+'</figure>':'<span class="muted">[附件未上传或已不可用]</span>';
      used.add(id);last=match.index+match[0].length;
    }
    result+=escape(String(text||'').slice(last));
    if(tail)result+='<div class="document-media-tail">'+Array.from(known).filter(id=>!used.has(id)).map(id=>'<figure class="document-media">'+media(id,(types||{})[id],urlFor(id))+'</figure>').join('')+'</div>';
    return '<div class="document-content">'+result+'</div>';
  }
  function attach(field,input,options={}){
    if(fields.has(field))return fields.get(field);
    const native=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value'),disabled=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'disabled');
    const initial=native.get.call(field),box=document.createElement('div'),error=document.createElement('p');
    box.className='textarea media-document';box.contentEditable=!field.disabled;box.setAttribute('role','textbox');box.setAttribute('aria-multiline','true');
    box.setAttribute('aria-label',field.labels&&field.labels[0]?field.labels[0].textContent.trim():field.placeholder||'正文');
    box.dataset.placeholder=field.placeholder||'输入文字，可在当前位置粘贴图片';
    error.className='reply-error media-editor-error';error.setAttribute('role','alert');
    field.style.display='none';field.required=false;field.after(box,error);
    const state={field,input,box,error,items:options.pending?options.pending.items.slice():[],cache:new Map(),urls:[],assets:options.assets||[],types:options.types||{},urlFor:options.urlFor||((id)=>(window.ShoeCloud?.base||'/shoe-api')+'/attachments/'+encodeURIComponent(id))};
    fields.set(field,state);if(input)inputs.set(input,state);live.add(state);
    const revoke=()=>{state.urls.forEach(URL.revokeObjectURL);state.urls=[];};
    function figure(id){
      const item=state.items.find(x=>x.token===id),known=state.assets.includes(id);if(!item&&!known){const missing=document.createElement('span');missing.dataset.media=id;missing.contentEditable='false';missing.textContent='[附件未上传或不可用]';return missing;}
      const node=document.createElement('figure');node.className='document-media editing-media';node.contentEditable='false';node.dataset.media=id;
      const src=item?URL.createObjectURL(item.file):state.urlFor(id);if(item)state.urls.push(src);
      const child=document.createElement((item?item.file.type:state.types[id]||'').startsWith('video/')?'video':'img');child.src=src;
      if(child.tagName==='VIDEO'){child.controls=true;child.preload='none';}else child.alt='粘贴图片';
      const remove=document.createElement('button');remove.type='button';remove.className='media-remove';remove.setAttribute('aria-label','移除正文中的附件');remove.textContent='×';remove.onclick=()=>{node.remove();sync();};node.append(child,remove);return node;
    }
    function load(value,reset){
      revoke();if(reset){state.items=[];state.cache.clear();if(input)input.value='';}
      box.replaceChildren();let last=0;for(const match of String(value||'').matchAll(marker)){box.append(document.createTextNode(value.slice(last,match.index)),figure(match[1]));last=match.index+match[0].length;}box.append(document.createTextNode(String(value||'').slice(last)));native.set.call(field,value||'');
    }
    function serialize(node){
      if(node.nodeType===Node.TEXT_NODE)return node.textContent;
      if(node.nodeType!==Node.ELEMENT_NODE)return '';
      if(node.dataset.media)return '\n[[media:'+node.dataset.media+']]\n';
      if(node.tagName==='BR')return '\n';
      let text='';node.childNodes.forEach(child=>{const block=child.nodeType===1&&['DIV','P'].includes(child.tagName);if(block&&text&&!text.endsWith('\n'))text+='\n';text+=serialize(child);});return text;
    }
    function setFiles(files){if(!input)return;const dt=new DataTransfer();files.forEach(f=>dt.items.add(f));input.files=dt.files;}
    function sync(){
      const value=serialize(box);native.set.call(field,value);error.textContent='';
      if(input){const removed=state.items.filter(x=>x.token&&!value.includes('[[media:'+x.token+']]'));if(removed.length){const gone=new Set(removed.map(x=>key(x.file)));setFiles(Array.from(input.files).filter(f=>!gone.has(key(f))));state.items=state.items.filter(x=>!removed.includes(x));input.dispatchEvent(new Event('change',{bubbles:true}));}}
      field.dispatchEvent(new Event('input',{bubbles:true}));
    }
    function insert(node,after){
      const selection=window.getSelection();let range=selection.rangeCount?selection.getRangeAt(0):null;
      if(!range||!box.contains(range.commonAncestorContainer)){range=document.createRange();range.selectNodeContents(box);range.collapse(false);}
      range.deleteContents();const fragment=document.createDocumentFragment();fragment.append(node);if(after)fragment.append(after);range.insertNode(fragment);range.setStart(after||node,after?0:node.textContent.length);range.collapse(true);selection.removeAllRanges();selection.addRange(range);sync();
    }
    box.addEventListener('input',sync);
    box.addEventListener('keydown',e=>{if(e.key==='Tab'){e.preventDefault();insert(document.createTextNode('  '));}});
    box.addEventListener('paste',e=>{
      if(field.disabled)return;e.preventDefault();e.stopPropagation();
      const files=Array.from(e.clipboardData.items||[]).filter(x=>x.kind==='file').map(x=>x.getAsFile()).filter(Boolean);
      if(files.length){
        if(!input){error.textContent='请通过附件上传区域添加新素材';return;}
        if(files.some(f=>!allowed.includes(f.type)||f.size>10*1024*1024)){error.textContent='支持 PNG、JPEG、MP4、WebM，每个不超过 10 MB';return;}
        if(input.files.length+files.length>(options.limit||6)){error.textContent='附件数量超过本次上限';return;}
        files.forEach(file=>{const token=crypto.randomUUID(),copy=new File([file],'paste-'+token+(file.type==='image/png'?'.png':file.type==='image/jpeg'?'.jpg':'.mp4'),{type:file.type,lastModified:Date.now()});state.items.push({token,file:copy});setFiles(Array.from(input.files).concat(copy));const paragraph=document.createElement('p');paragraph.append(document.createElement('br'));insert(figure(token),paragraph);});
        input.dispatchEvent(new Event('change',{bubbles:true}));
      }else insert(document.createTextNode(e.clipboardData.getData('text/plain')));
    });
    if(input)input.addEventListener('change',()=>{
      const current=Array.from(input.files),keys=new Set(current.map(key));state.items.filter(x=>native.get.call(field).includes('[[media:'+x.token+']]')).forEach(x=>{if(!keys.has(key(x.file))){current.unshift(x.file);keys.add(key(x.file));}});setFiles(current);
    });
    Object.defineProperty(field,'value',{configurable:true,get(){return native.get.call(this);},set(value){load(String(value||''),true);}});
    Object.defineProperty(field,'disabled',{configurable:true,get(){return disabled.get.call(this);},set(value){disabled.set.call(this,value);box.contentEditable=!value;box.setAttribute('aria-disabled',String(!!value));}});
    const placeholder=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'placeholder');
    Object.defineProperty(field,'placeholder',{configurable:true,get(){return placeholder.get.call(this);},set(value){placeholder.set.call(this,value);box.dataset.placeholder=value;}});
    field.focus=()=>box.focus();
    if(field.form)field.form.addEventListener('reset',()=>queueMicrotask(()=>load(native.get.call(field),true)));
    state.configure=(ids,types,urlFor)=>{state.assets=ids||[];state.types=types||{};if(urlFor)state.urlFor=urlFor;load(native.get.call(field),false);};
    state.snapshot=()=>({items:state.items.map(x=>({token:x.token,file:x.file}))});
    state.tokenFor=file=>(state.items.find(x=>key(x.file)===key(file))||{}).token;
    state.removeSaved=()=>{box.querySelectorAll('[data-media]').forEach(node=>{if(state.assets.includes(node.dataset.media))node.remove();});sync();};
    load(initial,false);return state;
  }
  async function upload(input,file,fn){const state=inputs.get(input),k=key(file);if(state&&state.cache.has(k))return state.cache.get(k);const result=await fn();if(state)state.cache.set(k,result.id);return result.id;}
  function links(input,files,ids){const state=inputs.get(input),result={};files.forEach((f,i)=>{const token=state&&state.tokenFor(f);if(token)result[token]=ids[i];});return result;}
  function resolve(text,links){return String(text||'').replace(marker,(all,id)=>links[id]?'[[media:'+links[id]+']]':all);}
  new MutationObserver(()=>{for(const state of live)if(!state.field.isConnected){state.urls.forEach(URL.revokeObjectURL);live.delete(state);}}).observe(document.documentElement,{childList:true,subtree:true});
  window.MediaEditor={attach,render,upload,links,resolve,isInline:(input,file)=>!!(inputs.has(input)&&inputs.get(input).tokenFor(file)),removeSaved:field=>{if(fields.has(field))fields.get(field).removeSaved();},preview:text=>String(text||'').replace(marker,'[附件]'),snapshot:field=>fields.has(field)?fields.get(field).snapshot():null,configure:(field,ids,types,url)=>{if(fields.has(field))fields.get(field).configure(ids,types,url);}};
})();
