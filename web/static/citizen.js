(() => {
 const actions=[['Find Schemes','/app#schemes'],['Talk to Saathi','/voice'],['Check Eligibility','/app#assistant'],['Fix a Blocked Benefit','/app#rescue']];
 const make=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e};
 const bar=make('nav','','citizen-actions');bar.setAttribute('aria-label','Get help with government schemes');
 for(const [label,href] of actions){const a=make('a',label);a.href=href;bar.append(a)}
 const hero=document.querySelector('.hero-copy');
 if(hero){hero.insertBefore(bar,hero.querySelector('.hero-note'));const refresh=()=>{
   const lang=document.querySelector('#site-language')?.value||'en';
   const titles={en:['Find government schemes','you may qualify for.'],hi:['सरकारी योजनाएँ खोजें','जिनके लिए आप पात्र हो सकते हैं।'],mr:['सरकारी योजना शोधा','ज्यांसाठी तुम्ही पात्र असू शकता.']};
   const lines=titles[lang]||titles.en;document.querySelector('[data-i18n="heroLine1"]').textContent=lines[0];document.querySelector('[data-i18n="heroLine2"]').textContent=lines[1];
 };document.querySelector('#site-language').addEventListener('change',refresh);refresh()}
 else document.querySelector('.content')?.prepend(bar);
 function source(url){try{const u=new URL(url);if(u.protocol!=='https:')return null;const a=make('a','Read the source and verify requirements ↗');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';return a}catch{return null}}
 function render(data){const dest=document.querySelector('#scheme-list');if(!dest)return;dest.replaceChildren();
  const seen=new Set();const cards=[data.results?.start_with,...(data.results?.others||[]),...(data.results?.needs_information||[])].filter(Boolean);
  for(const [index,c] of cards.entries()){if(seen.has(c.id))continue;seen.add(c.id);const card=make('article','','citizen-result');
   card.append(make('h3',`${seen.size}. ${c.name}`),make('span',c.status==='needs_info'?'More information needed':c.status.replaceAll('_',' '),'citizen-status'),make('p',c.benefit||'Benefit details need verification at the source.'));
   card.append(make('p',c.why||'Eligibility has not been confirmed.'));
   if(c.missing_requirements?.length)card.append(make('p','Missing information: '+c.missing_requirements.map(x=>x.replaceAll('_',' ')).join(', ')));
   for(const text of c.next_steps||[])card.append(make('p','Next: '+text,'citizen-next'));
   const evidence=make('details','','citizen-evidence');evidence.append(make('summary','Evidence and caveats'));
   for(const e of c.evidence||[])evidence.append(make('p',`${e.rule||'Requirement'}: ${e.outcome}`));
   for(const text of c.check_yourself||[])evidence.append(make('p','Check manually: '+text));
   evidence.append(make('p',c.verified?'Catalog reviewed; final approval is decided by the government.':'Source information has not been manually verified.'));
   card.append(evidence);const link=source(c.source_url);if(link)card.append(link);dest.append(card)
  }
  if(!seen.size)dest.append(make('p','Tell Saathi about your situation to find relevant schemes. Missing information is never treated as confirmed eligibility.'));
 }
 function history(data){if(!data.voice_sessions?.length)return;let panel=document.querySelector('#citizen-history');
  if(!panel){panel=make('section','','card');panel.id='citizen-history';document.querySelector('.content')?.append(panel)}panel.replaceChildren(make('h2','Your saved voice sessions'));
  for(const session of data.voice_sessions){const button=make('button',new Date(session.created_at*1000).toLocaleString()+' · '+session.status,'light');
   button.onclick=async()=>{try{const r=await fetch('/api/voice/'+encodeURIComponent(session.id));if(!r.ok)throw Error();const saved=await r.json();let view=panel.querySelector('.citizen-history-detail');if(!view){view=make('div','','citizen-history-detail');panel.append(view)}view.replaceChildren();
    for(const c of saved.summary?.schemes||[]){view.append(make('h3',c.name),make('p',c.why||c.status));const link=source(c.source_url);if(link)view.append(link)}
    const transcript=make('details');transcript.append(make('summary','Read saved transcript'));for(const m of saved.transcript)transcript.append(make('p',m.role+': '+m.text));view.append(transcript,make('p',saved.recipient_delivery_verified?'Delivery verified':'Recipient delivery has not been verified.'));
   }catch{button.textContent='History unavailable or expired. Retry later.'}};panel.append(button)
  }
 }
 let manual=false;const select=document.querySelector('#lang');select?.addEventListener('change',()=>manual=select.value!=='auto');
 const previous=window.fetch.bind(window);window.fetch=async(...args)=>{
  if(String(args[0])==='/api/v2/conversation'&&args[1]?.body){const body=JSON.parse(args[1].body);body.language_override=manual;args[1]={...args[1],body:JSON.stringify(body)}}
  const response=await previous(...args);
  if(response.ok){const path=String(args[0]);if(path==='/api/v2/conversation'){const data=await response.clone().json();setTimeout(()=>render(data),0)}
   else if(path.startsWith('/api/cases/')){const data=await response.clone().json();setTimeout(()=>{if(data.recommendations)render({results:data.recommendations});history(data)},0)}}return response};
 if(select)previous('/api/voice/capabilities').then(r=>r.json()).then(data=>{const automatic=make('option','Automatic / mixed language');automatic.value='auto';select.prepend(automatic);
  for(const lang of data.languages){if([...select.options].some(o=>o.value===lang.short))continue;const option=make('option',lang.name+' · English question fallback');option.value=lang.short;select.append(option)}
  try{const saved=localStorage.getItem('yojana-language');if([...select.options].some(o=>o.value===saved)){select.value=saved;manual=saved!=='auto'}}catch{}
 }).catch(()=>{});
})();
