/* Explicit authorized restoration. Only the opaque case ID is kept in sessionStorage. */
(() => {
 const make=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e};
 const content=document.querySelector('.content');if(!content)return;
 const bar=make('nav','','citizen-actions');bar.setAttribute('aria-label','Get help with government schemes');
 for(const [label,href] of [['Find Schemes','/app#schemes'],['Talk to Saathi','/voice'],['Check Eligibility','/app#assistant'],['Fix a Blocked Benefit','/app#rescue']]){const a=make('a',label);a.href=href;bar.append(a)}content.prepend(bar);
 const previous=window.fetch.bind(window);let inflight=null,manual=false,refining=false,revision=0;
 const panel=make('section','','card');panel.id='your-scheme-matches';
 const date=make('p','','muted');date.id='assessment-date';
 const refreshButton=make('button','Refresh saved results','light');refreshButton.id='refresh-case';
 const refineButton=make('button','Refine results','light');refineButton.id='refine-results';
 const grid=make('div','','match-grid');grid.id='saved-matches';
 panel.append(make('h2','Your Scheme Matches'),date,refreshButton,refineButton,grid);bar.after(panel);
 const freshButton=make('button','Fresh start for another citizen','light');freshButton.id='fresh-assessment';
 const continueButton=make('button','Continue this saved case','light');continueButton.id='continue-assessment';
 panel.insertBefore(freshButton,grid);panel.insertBefore(continueButton,grid);let continuing=false,restoredCase=false;
 freshButton.onclick=()=>{if(busy)return;continuing=false;restoredCase=false;clear();document.querySelector('#chat-status').textContent='New citizen assessment. No previous facts will be reused.';document.querySelector('#message').focus()};
 continueButton.onclick=()=>{continuing=true;document.querySelector('#chat-status').textContent='Continuing the displayed authorized case. Use Refine to correct facts.'};
 const historyPanel=make('section','','card');historyPanel.id='citizen-history';content.append(historyPanel);
 function cards(value){if(Array.isArray(value))return value;if(value?.schemes)return value.schemes;return [value?.start_with,...(value?.others||[]),...(value?.needs_information||[])].filter(Boolean)}
 function source(url){try{const u=new URL(url);if(u.protocol!=='https:')return null;const a=make('a','Read the source and verify requirements ↗');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';return a}catch{return null}}
 function article(c,index){const box=make('article','','citizen-result');
  box.append(make('h3',`${index+1}. ${c.name}`),make('span',(c.status_label||(['eligible_check_manually','check_manually'].includes(c.status)?'Needs verification':(c.status||'Needs verification').replaceAll('_',' '))),'citizen-status'),make('p',c.benefit||'Confirm benefit details at the source.'));
  if(c.missing_requirements?.length)box.append(make('p','Missing requirements: '+c.missing_requirements.map(f=>f.replaceAll('_',' ')).join(', ')));
  for(const step of c.next_steps||[])box.append(make('p','Next: '+step,'citizen-next'));
  const detail=make('details');detail.append(make('summary','Evidence and caveats'),make('p',c.why||'Eligibility has not been confirmed.'));
  for(const e of c.evidence||[])detail.append(make('p',`${e.rule||'Requirement'}: ${e.outcome}`));
  for(const condition of c.check_yourself||[])detail.append(make('p','Check manually: '+condition));
  detail.append(make('p','Guidance only. The government makes the final eligibility and approval decision.'));box.append(detail);const link=source(c.source_url);if(link)box.append(link);return box
 }
 function render(value,stamp){const seen=new Set(),items=cards(value).filter(c=>c&&!seen.has(c.id)&&seen.add(c.id)).slice(0,5);
  grid.replaceChildren(...items.map(article));if(!items.length)grid.append(make('p','No saved scheme matches yet. Tell Saathi about your situation to begin.'));
  const explorer=document.querySelector('#scheme-list');if(explorer)explorer.replaceChildren(...items.map(article));
  date.textContent=stamp?'Assessment saved: '+new Date(stamp*1000).toLocaleString():'Your current results will appear here.';
 }
 function renderHistory(data){historyPanel.replaceChildren(make('h2','Saved session history'));
  for(const session of data.voice_sessions||[]){const button=make('button',new Date((session.ended_at||session.created_at)*1000).toLocaleString()+' · '+session.status,'light');
   button.onclick=async()=>{try{const r=await previous('/api/voice/'+encodeURIComponent(session.id),{cache:'no-store'});if(!r.ok)throw Error();const saved=await r.json();let detail=historyPanel.querySelector('.citizen-history-detail');if(!detail){detail=make('div','','citizen-history-detail');historyPanel.append(detail)}
    detail.replaceChildren(...cards(saved.summary).map(article));const transcript=make('details');transcript.append(make('summary','Read saved transcript'));
    for(const m of saved.transcript)transcript.append(make('p',m.role+': '+m.text));detail.append(transcript,make('p','Recipient delivery has not been verified.'));
   }catch{button.textContent='History expired or temporarily unavailable.'}};historyPanel.append(button)
  }
  if(!(data.voice_sessions||[]).length)historyPanel.append(make('p','No voice sessions saved yet.'));
  if(data.messages?.length){const transcript=make('details');transcript.append(make('summary','Read saved chat transcript'));for(const m of data.messages)transcript.append(make('p',m.role+': '+m.content));historyPanel.append(transcript)}
 }
 function clear(expired=false){revision++;try{sessionStorage.removeItem('yojana-case-id')}catch{}sid=null;lastResult=null;facts({});renderSchemes();document.querySelector('#chat').replaceChildren();
  render(null);renderHistory({});if(expired){date.textContent='Saved case expired or access is unavailable. Start a new conversation.';bubble('Saved conversation is unavailable or expired. Start a new conversation.');activity('Saved conversation is unavailable or expired. Start a new conversation.')}
 }
 async function restore(){if(busy)return;if(inflight)return inflight;let saved;try{saved=sessionStorage.getItem('yojana-case-id')}catch{}
  if(!saved){render(null);renderHistory({});return}const version=revision;refreshButton.disabled=true;
  inflight=(async()=>{try{const r=await previous('/api/cases/'+encodeURIComponent(saved)+'?optional=true',{cache:'no-store'});
   if(!r.ok){if([403,404].includes(r.status)){clear(true);return}throw Error()}
   const data=await r.json();if(version!==revision)return;if(!data.available){clear(true);return}
   if(sid!==data.id)continuing=false;sid=data.id;restoredCase=true;facts(data.profile);document.querySelector('#chat').replaceChildren();for(const m of data.messages||[])bubble(m.content,m.role==='user'?'user':'bot');
   render(data.recommendations||data.latest_voice_summary,data.assessment_date);renderHistory(data);activity('Saved conversation restored. Continue chatting or refine your results.');
  }catch{if(version===revision)date.textContent='Saved results are temporarily unavailable. Your case is retained; use Refresh.'}finally{refreshButton.disabled=false;inflight=null}})();return inflight
 }
 window.YojanaCase={refresh:restore};refreshButton.onclick=restore;
 refineButton.onclick=()=>{refining=true;continuing=true;go('assistant');document.querySelector('#message').focus();document.querySelector('#chat-status').textContent='Add or explicitly correct details in this case.'};
 const select=document.querySelector('#lang');select?.addEventListener('change',()=>manual=select.value!=='auto');
 window.fetch=async(url,options={})=>{const path=String(url);
  if(path==='/api/v2/conversation'&&options.body){const body=JSON.parse(options.body);if(restoredCase&&!continuing){body.session_id=null;restoredCase=false;sid=null;facts({});document.querySelector('#chat').replaceChildren();render(null)}body.language_override=manual;body.refine=refining;options={...options,body:JSON.stringify(body)}}
  const response=await previous(url,options);
  if(path==='/api/v2/conversation'&&response.ok){const data=await response.clone().json();revision++;refining=false;
   continuing=true;setTimeout(()=>{render(data.recommendations||data.results,Date.now()/1000);if(data.assessment_complete)document.querySelector('#chat-status').textContent=data.assessment?.provisional?'Provisional results saved. More details or official verification are useful.':'Assessment complete. Review your matches or refine your results.'},0)
  }else if(path.startsWith('/api/v2/conversation/')&&options.method==='DELETE'&&response.ok)setTimeout(()=>clear(),0);
  return response
 };
 if(select)previous('/api/voice/capabilities').then(r=>r.json()).then(data=>{const automatic=make('option','Automatic / mixed language');automatic.value='auto';select.prepend(automatic);
  for(const lang of data.languages){if([...select.options].some(o=>o.value===lang.short))continue;const option=make('option',lang.name+' · English question fallback');option.value=lang.short;select.append(option)}
  try{const saved=localStorage.getItem('yojana-language');if([...select.options].some(o=>o.value===saved)){select.value=saved;manual=saved!=='auto'}}catch{}
 }).catch(()=>{});
 window.addEventListener('pageshow',()=>restore());window.addEventListener('focus',()=>{if(document.readyState==='complete')restore()});
 if(document.readyState==='complete')restore();else window.addEventListener('load',restore,{once:true});
})();
