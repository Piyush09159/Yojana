'use strict';
/* Browser voice session for Yojana Saathi.
 * States (body[data-state]): idle, listening, transcribing, thinking, speaking, reconnecting, ended.
 * Half duplex on purpose: the microphone is closed while Saathi speaks, so it never transcribes itself.
 * Every turn carries a request id; a retry after a network failure or a page refresh reuses it and the
 * server answers from its idempotency cache. Responses for an older turn/epoch are ignored.
 */
(() => {
 const $=id=>document.getElementById(id), create=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e};
 const STORE='yojana-voice';
 let handoff=new URLSearchParams(location.hash.slice(1)).get('handoff');
 if(location.hash)history.replaceState(null,'',location.pathname); // Never retain a capability in history/referrers.
 let cid=null,sid=null,busy=false,muted=false,ended=false,capabilities={},lastRetry=null,lastSpoken=null;
 let recognition=null,recorder=null,stream=null,vad=null,recordTimer=null,captureEpoch=0,sttAbort=null;
 let audio=null,audioURL=null,speechEpoch=0,speechAbort=null,speechActive=false,playbackTimer=null,deviceTimer=null;
 let turnEpoch=0,turnAbort=null,sessionVersion=0,serverSpeechDisabled=false;
 const unsupportedSpeech=new Set();
 const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
 const language=()=>$('voice-language').value;
 const shortLang=()=>{const v=language();return v==='auto'?'auto':v.split('-')[0]};
 const uid=()=>{if(crypto.randomUUID)return crypto.randomUUID();const b=crypto.getRandomValues(new Uint8Array(16));return Array.from(b,x=>x.toString(16).padStart(2,'0')).join('')};
 const LABEL={idle:'Ready',listening:'Listening',transcribing:'Transcribing',thinking:'Thinking',speaking:'Speaking',reconnecting:'Reconnecting',ended:'Session ended'};

 // ---------- persistence for refresh recovery (ids and the unsent turn only; no audio) ----------
 function saved(){try{return JSON.parse(sessionStorage.getItem(STORE)||'{}')}catch{return {}}}
 function save(patch){try{sessionStorage.setItem(STORE,JSON.stringify({...saved(),...patch}))}catch{}}
 function forget(){try{sessionStorage.removeItem(STORE)}catch{}}

 // ---------- state ----------
 function state(name,help,label){speechActive=name==='speaking';document.body.dataset.state=name;$('voice-state').textContent=label||LABEL[name]||name;if(help)$('voice-help').textContent=help;controls()}
 function retry(fn){lastRetry=fn;$('voice-retry').disabled=!fn}
 function controls(){
  for(const id of ['voice-listen','voice-send'])$(id).disabled=!cid||busy||ended;
  $('voice-end').disabled=!cid||ended;$('voice-mute').disabled=!cid;
  $('voice-interrupt').disabled=!cid||ended||muted||!speechActive;
  $('voice-replay').disabled=!lastSpoken||busy;
  $('voice-continue').hidden=!!cid||!saved().sid;
 }
 function network(text){$('voice-network').hidden=!text;$('voice-network').textContent=text||''}
 function append(role,text){$('voice-transcript').append(create('div',text,'turn '+role));$('voice-transcript').scrollTop=$('voice-transcript').scrollHeight}

 // ---------- HTTP ----------
 async function api(path,body,method='POST',signal,timeout=70000){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
  const abort=()=>controller.abort();if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
  try{
   let r;
   try{r=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal})}
   catch(e){if(e.name==='AbortError')throw e;const n=new Error('Network unavailable');n.network=true;throw n}
   const data=await r.json().catch(()=>({}));
   if(!r.ok){const d=data.detail;const error=new Error(typeof d==='string'?d:d?.message||d?.code?.replaceAll('_',' ')||'Request failed. Please retry.');error.status=r.status;error.code=d?.code;throw error}
   return data
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort)}
 }

 // ---------- audio output ----------
 function unlockAudio(){try{const C=window.AudioContext||window.webkitAudioContext;if(C){const c=new C();void c.resume().then(()=>c.close()).catch(()=>c.close())}}catch{}}
 function cancelPlayback(){clearTimeout(playbackTimer);clearTimeout(deviceTimer);speechEpoch++;speechActive=false;speechAbort?.abort();speechAbort=null;
  if(audio){audio.onended=audio.onerror=audio.onplaying=null;audio.pause();audio=null}if(audioURL){URL.revokeObjectURL(audioURL);audioURL=null}window.speechSynthesis?.cancel()}
 async function speak(result,final=false){
  cancelPlayback();const epoch=speechEpoch;$('voice-replay').classList.remove('attention');
  const current=()=>epoch===speechEpoch&&!muted&&(!ended||final);
  lastSpoken={result,final};controls();
  const after=help=>{if(!current())return;state(final?'ended':'idle',help);if(!final&&$('voice-handsfree').checked&&!busy)setTimeout(()=>{if(current()&&!busy&&!ended)listen()},400)};
  if(muted){state(final?'ended':'idle','Audio is muted. Unmute and choose Replay last reply.');return}
  const text=(result.spoken_text||result.message||'').slice(0,360);if(!text){state(final?'ended':'idle');return}
  const lang=(result.speech_language||(result.text_fallback?'en':result.language)||'en')+'-IN';
  let deviceStarted=false;
  function device(reason){if(!current()||deviceStarted)return;deviceStarted=true;clearTimeout(playbackTimer);
   if(audio){audio.onended=audio.onerror=audio.onplaying=null;audio.pause();audio=null}
   console.info('speech_status',{code:reason});
   if(capabilities.tts_provider==='off'||!window.speechSynthesis||!window.SpeechSynthesisUtterance){after('Read the reply above. Choose Listen or type to continue.');return}
   const u=new SpeechSynthesisUtterance(text);u.lang=lang;
   u.onend=()=>{clearTimeout(deviceTimer);after('Choose Listen or type to continue.')};
   u.onerror=()=>{clearTimeout(deviceTimer);$('voice-replay').classList.add('attention');after('Device audio could not play. Tap Replay last reply, or read the transcript.')};
   state(final?'ended':'speaking','Using your device voice. If you cannot hear it, tap Replay last reply.','Speaking');
   deviceTimer=setTimeout(()=>{if(current()){window.speechSynthesis.cancel();after('No audio was confirmed. Check the volume, tap Replay, or read the transcript.')}},20000);
   try{window.speechSynthesis.speak(u)}catch{after('Device voice is unavailable. Read the transcript.')}
  }
  if(!capabilities.tts_configured||serverSpeechDisabled||unsupportedSpeech.has(lang)){device('device_voice');return}
  state(final?'ended':'speaking','Preparing the spoken reply…','Speaking');
  speechAbort=new AbortController();const controller=speechAbort,timer=setTimeout(()=>controller.abort(),12000);
  try{
   const response=await fetch('/api/voice/tts',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({call_id:cid,text,lang,request_id:result.request_id}),signal:controller.signal});
   if(!current())return;
   if(!response.ok){if(response.status===409){after('A newer reply replaced this audio.');return}
    if(response.status===422)unsupportedSpeech.add(lang);else serverSpeechDisabled=true;device('server_tts_'+response.status);return}
   const blob=await response.blob();if(!current())return;
   audioURL=URL.createObjectURL(blob);audio=new Audio(audioURL);audio.setAttribute('playsinline','');const player=audio;
   audio.onended=()=>{if(!current())return;clearTimeout(playbackTimer);after('Choose Listen or type to continue.')};
   audio.onerror=()=>{if(current()){serverSpeechDisabled=true;device('audio_decode_error')}};
   playbackTimer=setTimeout(()=>{if(current()&&audio===player)device('playback_timeout')},6000);
   audio.onplaying=()=>{if(!current())return;clearTimeout(playbackTimer);playbackTimer=setTimeout(()=>{if(current()&&audio===player)device('playback_stalled')},30000)};
   await player.play();
  }catch(error){if(!current())return;
   // Mobile browsers block audio that was not started by a tap: keep the reply and ask for one tap.
   if(error.name==='NotAllowedError'){$('voice-replay').classList.add('attention');state(final?'ended':'idle','Your browser blocked automatic audio. Tap Replay last reply to hear it.');return}
   serverSpeechDisabled=true;device(error.name==='AbortError'?'tts_timeout':'tts_unavailable')}
  finally{clearTimeout(timer)}
 }

 // ---------- turns ----------
 async function send(text,requestId=uid(),attempt=0){
  text=(text||'').trim();if(!text||!cid||ended)return;
  if(busy&&attempt===0)return;
  stopCapture();cancelPlayback();
  const epoch=++turnEpoch,version=sessionVersion,callId=cid;turnAbort?.abort();turnAbort=new AbortController();
  busy=true;save({pending:{text,request_id:requestId,cid:callId}});
  state('thinking','Reading your words and checking the scheme rules…');
  try{
   const lang=shortLang(),data=await api('/api/voice/'+callId+'/turn',{text,lang:language(),language_override:lang!=='auto',request_id:requestId},'POST',turnAbort.signal);
   if(epoch!==turnEpoch||version!==sessionVersion||ended)return;      // stale response
   save({pending:null});network('');
   append('user',text);append('assistant',data.message+(data.question?.reason?'\nWhy I ask: '+data.question.reason:''));
   $('voice-text').value='';$('live-transcript').textContent='';retry(null);
   data.request_id=requestId;
   if(data.language_switched)syncLanguage(data.language_switched);
   if(data.assessment_complete&&!data.language_switched){finish(data);return}
   busy=false;void speak(data)
  }catch(e){
   if(epoch!==turnEpoch||version!==sessionVersion)return;
   if(e.network&&attempt<3){state('reconnecting','Connection lost. Retrying your message safely…');network('You appear to be offline. Your message is kept and will be retried.');
    await new Promise(r=>setTimeout(r,1500*2**attempt));if(epoch===turnEpoch){busy=false;return send(text,requestId,attempt+1)}return}
   state('idle',e.name==='AbortError'?'The request timed out. Retry safely, or type again.':e.message,'Message not completed');
   $('voice-text').value=text;retry(()=>send(text,requestId))
  }finally{if(epoch===turnEpoch){busy=false;controls()}}
 }
 function finish(data){
  ended=true;busy=false;stopMedia();renderSummary(data.summary||{schemes:data.recommendations,caveat:'Guidance only; verify official sources and missing conditions.'});
  delivery({whatsapp:{status:'not_requested'}});save({cid:null});
  state('ended',data.assessment?.provisional?'Provisional results saved. Refine them in your dashboard.':'Assessment complete. Your results are saved.');
  $('voice-start').disabled=false;$('voice-start').textContent='Fresh start';
  const callId=cid,version=sessionVersion;
  if($('summary-consent').checked)api('/api/voice/'+callId+'/end',{consent_whatsapp:true}).then(d=>settleDelivery(d,callId,version)).catch(e=>{if(version===sessionVersion)$('summary-status').textContent='Summary saved, but the WhatsApp send was not requested. '+e.message});
  void speak(data,true)
 }

 // ---------- capture ----------
 function stopCapture(){
  captureEpoch++;if(sttAbort){sttAbort.abort();sttAbort=null}clearTimeout(recordTimer);
  if(recognition){const r=recognition;recognition=null;r.onstart=r.onresult=r.onerror=r.onend=null;try{r.abort()}catch{}}
  if(recorder){const r=recorder;recorder=null;r.ondataavailable=r.onstop=null;try{if(r.state==='recording')r.stop()}catch{}}
  if(vad){vad.stop();vad=null}
  stream?.getTracks().forEach(t=>t.stop());stream=null;$('voice-level').style.width='0';
  $('voice-listen').textContent='Listen';
 }
 function stopMedia(){stopCapture();cancelPlayback()}
 function micError(e){
  const name=e?.name||'';
  if(!window.isSecureContext)return 'The microphone needs a secure (https) page. Type instead.';
  if(name==='NotAllowedError'||name==='SecurityError'||name==='PermissionDeniedError')return 'Microphone permission was denied. Allow it in your browser’s site settings (the lock icon), then tap Listen — or type below.';
  if(name==='NotFoundError'||name==='OverconstrainedError')return 'No microphone was found on this device. Type below instead.';
  if(name==='NotReadableError'||name==='AbortError')return 'The microphone is busy in another app (a call?). Close it and tap Listen, or type.';
  return 'Recording is not available in this browser. Type below instead.';
 }
 async function permissionDenied(){try{const p=await navigator.permissions?.query({name:'microphone'});return p?.state==='denied'}catch{return false}}
 function useServer(){const mode=capabilities.browser_stt||'auto';if(!capabilities.stt_configured||!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)return false;return mode==='server'||!Recognition||mode==='auto'&&!Recognition}
 async function listen(){
  if(!cid||busy||muted||ended)return;
  if(recorder?.state==='recording'){clearTimeout(recordTimer);recorder.stop();return}
  stopMedia();unlockAudio();
  if(await permissionDenied()){state('idle',micError({name:'NotAllowedError'}),'Microphone blocked');retry(listen);return}
  if(useServer())return serverRecord();
  if(Recognition&&capabilities.browser_stt!=='server')return deviceRecognition();
  state('idle','Speech input is not available here. Type below — it is the same conversation.','Type your message');$('voice-text').focus();
 }
 function deviceRecognition(){
  const epoch=captureEpoch,capture=new Recognition();recognition=capture;let submitted=false;
  const current=()=>epoch===captureEpoch&&recognition===capture&&!muted&&!ended;
  capture.lang=shortLang()==='auto'?(navigator.language||'en-IN'):shortLang()+'-IN';capture.interimResults=true;capture.continuous=false;
  capture.onstart=()=>{if(current())state('listening','Speak naturally; I stop listening when you pause.')};
  capture.onresult=e=>{if(!current()||submitted)return;const text=Array.from(e.results).map(r=>r[0].transcript).join(' ');$('live-transcript').textContent=text;$('voice-text').value=text;
   if(e.results[e.results.length-1].isFinal){submitted=true;void send(text)}};
  capture.onerror=e=>{if(!current()||e.error==='aborted')return;state('idle',e.error==='not-allowed'?micError({name:'NotAllowedError'}):e.error==='no-speech'?'I did not hear anything. Tap Listen and speak, or type.':'Recognition failed. Tap Listen to retry, or type.','Not heard');retry(listen)};
  capture.onend=()=>{if(current()&&!busy&&!submitted)state('idle','Choose Listen to speak again, or type.')};
  try{state('listening','Waiting for microphone access…','Microphone permission');capture.start()}catch{state('idle','Please type, or try Listen again.');retry(listen)}
 }
 // Energy voice-activity detection: stop after a pause once speech was heard; give up on silence.
 function startVad(mic,onEnd){
  const C=window.AudioContext||window.webkitAudioContext;if(!C)return null;
  const ctx=new C(),source=ctx.createMediaStreamSource(mic),analyser=ctx.createAnalyser();analyser.fftSize=1024;source.connect(analyser);
  const data=new Float32Array(analyser.fftSize);let floor=0.005,heard=false,lastVoice=performance.now(),started=performance.now(),timer=null,stopped=false;
  const tick=()=>{if(stopped)return;analyser.getFloatTimeDomainData(data);let sum=0;for(const v of data)sum+=v*v;const rms=Math.sqrt(sum/data.length),now=performance.now();
   if(now-started<400)floor=Math.max(floor,rms*1.2);                 // calibrate on the first 400 ms
   const speaking=rms>Math.max(0.012,floor*2.5);$('voice-level').style.width=Math.min(100,Math.round(rms*900))+'%';
   if(speaking){heard=true;lastVoice=now}
   if(heard&&now-lastVoice>1300)return onEnd('pause');
   if(!heard&&now-started>8000)return onEnd('no_speech');
   timer=setTimeout(tick,60)};
  void ctx.resume().catch(()=>{});tick();
  return {stop(){stopped=true;clearTimeout(timer);try{source.disconnect()}catch{}void ctx.close().catch(()=>{})},heard:()=>heard};
 }
 function recorderType(){for(const t of ['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus','audio/webm'])if(window.MediaRecorder?.isTypeSupported?.(t))return t;return ''}
 async function serverRecord(){
  const epoch=captureEpoch,callId=cid;const current=()=>epoch===captureEpoch&&callId===cid&&!ended&&!muted;
  state('listening','Allow the microphone to record up to 15 seconds.','Microphone permission');
  let mic;
  try{mic=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}})}
  catch(e){if(current()){state('idle',micError(e),'Microphone unavailable');retry(listen)}return}
  if(!current()){mic.getTracks().forEach(t=>t.stop());return}
  stream=mic;const chunks=[],type=recorderType(),capture=new MediaRecorder(mic,type?{mimeType:type}:undefined);recorder=capture;let reason='manual';
  capture.ondataavailable=e=>{if(current()&&e.data.size)chunks.push(e.data)};
  capture.onstop=async()=>{
   const heard=vad?vad.heard():true;vad?.stop();vad=null;mic.getTracks().forEach(t=>t.stop());if(stream===mic)stream=null;if(recorder===capture)recorder=null;
   $('voice-listen').textContent='Listen';$('voice-level').style.width='0';clearTimeout(recordTimer);
   if(!current())return;
   if(reason==='no_speech'||!heard){state('idle','I did not hear anything. Tap Listen and speak closer to the phone, or type.','Not heard');retry(listen);return}
   busy=true;state('transcribing','Turning your words into text…');
   const controller=new AbortController();sttAbort=controller;
   try{
    const C=window.AudioContext||window.webkitAudioContext,ctx=new C();let decoded;
    try{decoded=await ctx.decodeAudioData(await new Blob(chunks,{type:capture.mimeType||type}).arrayBuffer())}finally{void ctx.close().catch(()=>{})}
    if(!current())return;
    const bytes=wav(decoded);let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    const data=await api('/api/voice/stt',{call_id:callId,audio:btoa(binary),lang:shortLang()},'POST',controller.signal,40000);
    if(!current())return;busy=false;
    if(data.needs_confirmation){
     // A number that may be misheard is shown first; the citizen sends or corrects it.
     $('voice-text').value=data.text;$('live-transcript').textContent='';state('idle','Please check the words — especially numbers — then tap Send, or correct them.','Check what I heard');$('voice-text').focus();return}
    $('live-transcript').textContent=data.text;await send(data.text);
   }catch(e){if(!current())return;busy=false;
    const msg=e.code==='no_speech'?'I did not hear any words. Tap Listen to try again, or type.':e.code==='speech_busy'?'Speech recognition is busy. Please try again in a moment, or type.':e.code==='speech_timeout'?'Speech recognition took too long. Please type, or try a shorter message.':e.network?'Network unavailable. Type your message; it will be sent when you are back online.':'Speech recognition is unavailable. Please type your message.';
    state('idle',msg,'Transcription unavailable');retry(listen)}
   finally{if(sttAbort===controller)sttAbort=null;if(epoch===captureEpoch){busy=false;controls()}}
  };
  vad=startVad(mic,why=>{reason=why;if(capture.state==='recording')capture.stop()});
  capture.start(250);$('voice-listen').textContent='Stop recording';state('listening','Speak now. I stop automatically when you pause (or tap Stop recording).');
  recordTimer=setTimeout(()=>{reason='limit';if(current()&&capture.state==='recording')capture.stop()},15000);
 }
 function wav(buffer){
  const length=Math.min(Math.floor(buffer.duration*16000),240000),data=new ArrayBuffer(44+length*2),view=new DataView(data);
  const word=(o,t)=>{for(let i=0;i<t.length;i++)view.setUint8(o+i,t.charCodeAt(i))};
  word(0,'RIFF');view.setUint32(4,36+length*2,true);word(8,'WAVE');word(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);word(36,'data');view.setUint32(40,length*2,true);
  for(let i=0;i<length;i++){let s=0;for(let c=0;c<buffer.numberOfChannels;c++)s+=buffer.getChannelData(c)[Math.min(buffer.length-1,Math.floor(i*buffer.sampleRate/16000))]/buffer.numberOfChannels;s=Math.max(-1,Math.min(1,s));view.setInt16(44+2*i,s<0?s*32768:s*32767,true)}
  return new Uint8Array(data)
 }
 function interrupt(){
  if(!cid||ended||muted)return;
  // Synchronous within the tap: drop the old reply for good, then open the microphone.
  cancelPlayback();$('voice-interruption').textContent='Interrupted. The old reply will not resume. Speak now, or type below.';
  unlockAudio();void listen();
 }

 // ---------- session ----------
 async function start(resume=false){
  if(busy)return;sessionVersion++;turnEpoch++;turnAbort?.abort();lastSpoken=null;$('voice-interruption').textContent='';unlockAudio();stopMedia();
  cid=null;ended=false;network('');controls();$('summary-status').textContent='';$('summary-refresh').hidden=true;$('summary-cancel').hidden=true;$('summary-consent').checked=false;serverSpeechDisabled=false;unsupportedSpeech.clear();
  $('voice-summary').replaceChildren(create('h2','Your next steps'),create('p','Your ranked shortlist will appear here after the session.'));
  $('voice-start').disabled=true;state('reconnecting','Opening your authorized case…','Connecting');
  try{
   let lang=null;
   if(handoff){const linked=await api('/api/voice/redeem',{token:handoff});handoff=null;sid=linked.session_id;lang=linked.language;forget()}
   else sid=resume?saved().sid||null:null;
   const data=await api('/api/voice/start',{session_id:sid});cid=data.call_id;sid=data.session_id;save({sid,cid});
   const history=await api('/api/voice/'+cid,undefined,'GET');
   $('voice-transcript').replaceChildren();for(const m of history.transcript)append(m.role,m.text);
   syncLanguage(lang||history.preferred_language||null);
   retry(null);state('idle',useServer()||Recognition?'Choose Listen and speak, or type. The microphone stays off until you choose Listen.':'Speech input is not available in this browser. Type below — it is the same conversation.');
   const pending=saved().pending;
   if(pending&&pending.cid===cid)void send(pending.text,pending.request_id)   // resend after a refresh: same request id
  }catch(e){state('idle',e.network?'You appear to be offline. Reconnect and try again.':e.message,'Unable to connect');if(e.status===403||e.status===404||e.status===410)forget();retry(()=>start(resume))}
  finally{$('voice-start').disabled=false;controls()}
 }
 function syncLanguage(code){if(!code)return;const option=[...$('voice-language').options].find(o=>o.value===code+'-IN'||o.value===code);if(option)$('voice-language').value=option.value;coverage()}
 async function changeLanguage(){
  coverage();const lang=shortLang();
  if(!cid||ended||!['auto','en','hi','mr'].includes(lang))return;
  stopMedia();const requestId=uid(),epoch=++turnEpoch;busy=true;state('thinking','Switching language; your answers are kept…');
  try{const data=await api('/api/voice/'+cid+'/language',{lang,request_id:requestId});if(epoch!==turnEpoch)return;
   busy=false;if(data.message){append('assistant',data.message);void speak({...data,request_id:requestId})}else state('idle','Automatic language: I will reply in the language you speak.')}
  catch(e){if(epoch===turnEpoch){busy=false;state('idle','The language was not changed. '+e.message);retry(changeLanguage)}}
  finally{if(epoch===turnEpoch){busy=false;controls()}}
 }
 function coverage(){const lang=capabilities.languages?.find(l=>l.code===language());$('coverage').textContent=lang?`${lang.name}: replies and questions in this language; scheme names stay as officially written. Your answers are kept when you switch.`:'Automatic: I reply in the language you speak. Choose a language for steadier recognition.'}

 // ---------- summary and WhatsApp consent ----------
 function renderSummary(snapshot){const dest=$('voice-summary');dest.replaceChildren(create('h2','Your ranked shortlist and next steps'));
  if(!snapshot.schemes?.length)dest.append(create('p','There is not enough verified information to recommend a scheme yet. Continue your case from the dashboard.'));
  (snapshot.schemes||[]).forEach((item,i)=>{const card=create('article','','summary-card');card.append(create('h3',`${i+1}. ${item.name}`),create('p',item.benefit||'Benefit details need official verification.'),create('p',item.why||item.status));
   card.append(create('p','Missing: '+(item.missing_requirements?.map(f=>f.replaceAll('_',' ')).join(', ')||'Review any manual conditions at the source.')));
   for(const step of item.next_steps||[])card.append(create('p','Next: '+step));
   for(const c of item.check_yourself||[])card.append(create('p','Check manually: '+c));
   try{const url=new URL(item.source_url);if(url.protocol==='https:'){const a=create('a','Read the source and verify requirements ↗');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';card.append(a)}}catch{}
   dest.append(card)});dest.append(create('p',snapshot.caveat||''));
 }
 function delivery(data){const status=data.whatsapp.status;const labels={not_requested:'No WhatsApp summary requested.',pending:'Summary queued; not yet accepted by WAHA.',retrying:'Send failed; a retry is scheduled. Delivery is not confirmed.',failed:'Summary send failed. Use Retry summary to request another attempt.',cancelled:'Summary cancelled. Nothing more will be sent without renewed consent.',accepted_by_waha:'WAHA accepted the summary. Delivery to the phone is not verified.'};
  $('summary-status').textContent=labels[status]||'Summary status unavailable.';$('summary-refresh').hidden=false;$('summary-refresh').textContent=['not_requested','cancelled'].includes(status)?'Send summary with consent':status==='failed'?'Retry summary':'Check WhatsApp status';$('summary-refresh').dataset.status=status;$('summary-cancel').hidden=!['pending','retrying','failed'].includes(status)}
 async function settleDelivery(data,callId=cid,version=sessionVersion){
  if(callId!==cid||version!==sessionVersion)return;
  if(!$('summary-consent').checked&&['pending','retrying','failed'].includes(data.whatsapp.status)){await api('/api/voice/'+callId+'/summary/cancel',{});data=await api('/api/voice/'+callId,undefined,'GET')}
  if(callId===cid&&version===sessionVersion)delivery(data);
 }
 async function cancelSummary(){const callId=cid,version=sessionVersion;if(!callId)return;
  try{await api('/api/voice/'+callId+'/summary/cancel',{});if(version!==sessionVersion)return;$('summary-consent').checked=false;await settleDelivery(await api('/api/voice/'+callId,undefined,'GET'),callId,version)}
  catch(e){if(version===sessionVersion)$('summary-status').textContent='Cancellation not confirmed. '+e.message}}
 async function refresh(){try{let data;const status=$('summary-refresh').dataset.status;
  if(['not_requested','cancelled'].includes(status)){if(!$('summary-consent').checked)throw new Error('Tick the consent box before requesting a WhatsApp summary.');data=await api('/api/voice/'+cid+'/end',{consent_whatsapp:true})}
  else{if(status==='failed')await api('/api/voice/'+cid+'/summary/retry',{});data=await api('/api/voice/'+cid,undefined,'GET')}await settleDelivery(data)
 }catch(e){$('summary-status').textContent='Summary not confirmed. '+e.message}}
 async function end(){if(!cid||ended)return;ended=true;turnEpoch++;turnAbort?.abort();stopMedia();controls();state('thinking','Saving your minimized transcript and ranked next steps…','Preparing summary');
  try{const data=await api('/api/voice/'+cid+'/end',{consent_whatsapp:$('summary-consent').checked});renderSummary(data.summary);await settleDelivery(data);save({cid:null,pending:null});state('ended','Your saved case remains available in the dashboard.');$('voice-start').textContent='Fresh start';retry(null)}
  catch(e){ended=false;controls();state('idle',e.message,'Summary not saved');retry(end)}}

 // ---------- wiring ----------
 $('voice-start').textContent='Fresh start';
 $('voice-start').onclick=()=>start(false);$('voice-continue').onclick=()=>start(true);
 $('voice-listen').onclick=()=>void listen();$('voice-interrupt').onclick=interrupt;$('voice-end').onclick=end;
 $('voice-retry').onclick=()=>lastRetry?.();$('summary-refresh').onclick=refresh;$('summary-cancel').onclick=cancelSummary;
 $('voice-replay').onclick=()=>{if(lastSpoken&&!busy){stopCapture();unlockAudio();void speak(lastSpoken.result,lastSpoken.final)}};
 $('summary-consent').onchange=()=>{if(!$('summary-consent').checked&&['pending','retrying','failed'].includes($('summary-refresh').dataset.status))void cancelSummary()};
 $('voice-mute').onclick=()=>{muted=!muted;$('voice-mute').setAttribute('aria-pressed',String(muted));$('voice-mute').textContent=muted?'Unmute':'Mute';if(muted)stopMedia();state(ended?'ended':'idle',muted?'Microphone and audio are off. Typing still works.':'Choose Listen to speak.')};
 $('voice-form').onsubmit=e=>{e.preventDefault();void send($('voice-text').value)};
 $('voice-language').onchange=()=>void changeLanguage();
 window.addEventListener('offline',()=>{network('You are offline. Your session is saved; typing and sending resume when you reconnect.');if(cid&&!ended)state('reconnecting','Waiting for the network…')});
 window.addEventListener('online',()=>{network('');if(!cid||ended)return;const p=saved().pending;if(p&&p.cid===cid&&!busy)void send(p.text,p.request_id);else state('idle','Back online. Choose Listen or type.')});
 document.addEventListener('visibilitychange',()=>{if(document.hidden&&(recorder||recognition)){stopCapture();if(cid&&!ended)state('idle','Listening stopped because the page was hidden. Tap Listen to continue.')}});
 window.addEventListener('pagehide',()=>{stopMedia()});
 window.addEventListener('pageshow',e=>{if(e.persisted){cid=null;ended=false;busy=false;controls();state('idle','Your accepted turns are saved. Tap Continue saved case.')}});
 fetch('/api/voice/capabilities',{credentials:'same-origin'}).then(r=>r.json()).then(data=>{capabilities=data;
  const select=$('voice-language'),main=document.createElement('optgroup'),more=document.createElement('optgroup');main.label='Supported';more.label='Other (text, English fallback)';
  for(const lang of data.languages){const o=create('option',lang.name+' · '+lang.code);o.value=lang.code;(['en','hi','mr'].includes(lang.short)?main:more).append(o)}
  select.append(main,more);
  $('voice-privacy').textContent='Do not speak identity or account numbers. '+(data.browser_stt==='server'&&data.stt_provider==='local'?'Your recording is transcribed on Yojana Saathi’s own server and is not kept. ':'Speech recognition may use your browser’s provider. ')+'Audio is not retained by this application; minimized text follows the case retention policy.';
  controls();if(handoff)void start(false);else if(saved().cid)$('voice-help').textContent='A session from this tab is saved. Tap Continue saved case to resume it.';
 }).catch(()=>{$('coverage').textContent='Language list could not be loaded; typing still works.'});
 controls();
})();
