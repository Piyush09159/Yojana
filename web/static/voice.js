'use strict';
(() => {
 const $=id=>document.getElementById(id), create=(tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e};
 let handoff=new URLSearchParams(location.hash.slice(1)).get('handoff');
 if(location.hash)history.replaceState(null,'',location.pathname); // Never retain a capability in history/referrers.
 let cid=null,sid=null,busy=false,muted=false,ended=false,recognition=null,recorder=null,stream=null,audio=null,audioURL=null,recordTimer=null,lastRetry=null,capabilities={},pollTimer=null;
 let speechEpoch=0,speechAbort=null,serverSpeechDisabled=false,creditsExhausted=false;
 const unsupportedSpeech=new Set();
 let lastSpoken=null,playbackTimer=null,deviceTimer=null,sessionVersion=0;
 let captureEpoch=0,sttAbort=null,speechActive=false;
 const replay=create('button','Replay last reply');replay.id='voice-replay';replay.disabled=true;$('voice-retry').after(replay);
 const continueButton=create('button','Continue saved case');continueButton.id='voice-continue';$('voice-start').after(continueButton);
 replay.onclick=()=>{if(lastSpoken&&!busy){stopCapture();void speak(lastSpoken.result,lastSpoken.final)}};
 continueButton.onclick=()=>start(true);
 function speechMetric(code,started){console.info('speech_status',{code,latency_ms:Math.round(performance.now()-started)})}
 function unlockAudio(){try{const Context=window.AudioContext||window.webkitAudioContext;if(Context){const context=new Context();void context.resume().then(()=>context.close()).catch(()=>context.close())}}catch{}}
 const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
 const language=()=>$('voice-language').value;
 function state(name,help){speechActive=name==='Speaking';const label=name==='Session ended'?'Completed':name;document.body.dataset.state=label.toLowerCase();$('voice-state').textContent=label;if(help)$('voice-help').textContent=help;controls()}
 function retry(fn){lastRetry=fn;$('voice-retry').disabled=!fn}
 async function api(path,body,method='POST',signal){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),70000);
  const abort=()=>controller.abort();if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
  try{const r=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});
   const data=await r.json().catch(()=>({}));if(!r.ok){const error=new Error(typeof data.detail==='string'?data.detail:data.detail?.code?.replaceAll('_',' ')||'Request failed. Please retry.');error.status=r.status;throw error}return data
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort)}
 }
 function controls(){for(const id of ['voice-listen','voice-send'])$(id).disabled=!cid||busy||ended;$('voice-end').disabled=!cid||ended;$('voice-mute').disabled=!cid;const button=$('voice-interrupt');if(button)button.disabled=!cid||busy||ended||muted||!speechActive;replay.disabled=!lastSpoken||busy}
 function append(role,text){$('voice-transcript').append(create('div',text,'turn '+role));$('voice-transcript').scrollTop=$('voice-transcript').scrollHeight}
 function cancelPlayback(){clearTimeout(playbackTimer);clearTimeout(deviceTimer);speechEpoch++;speechActive=false;speechAbort?.abort();speechAbort=null;if(audio){audio.onended=null;audio.onerror=null;audio.onplaying=null;audio.pause();audio=null}if(audioURL){URL.revokeObjectURL(audioURL);audioURL=null}window.speechSynthesis?.cancel()}
 function stopCapture(){
  captureEpoch++;if(sttAbort){sttAbort.abort();sttAbort=null;busy=false}clearTimeout(recordTimer);
  if(recognition){const previous=recognition;recognition=null;previous.onstart=previous.onresult=previous.onerror=previous.onend=null;try{previous.abort()}catch{}}
  if(recorder){const previous=recorder;recorder=null;previous.ondataavailable=previous.onstop=null;if(previous.state==='recording')previous.stop()}
  stream?.getTracks().forEach(t=>t.stop());stream=null;
  $('voice-listen').textContent='Listen';
 }
 function stopMedia(){stopCapture();cancelPlayback()}
 function interrupt(){
  if(!cid||busy||ended||muted)return;
  // Synchronous within the tap: invalidate pending TTS, stop both players, then
  // request listening. This is tap interruption, not acoustic/full-duplex VAD.
  cancelPlayback();state('Interrupted','Saathi stopped. Opening your microphone for the next turn…');
  $('voice-interruption').textContent='Interrupted. The old reply will not resume. Speak now, or type below.';
  unlockAudio();listen();
 }
 async function start(continueSaved=false){
  if(busy)return;sessionVersion++;lastSpoken=null;replay.disabled=true;$('voice-interruption').textContent='';unlockAudio();
  stopMedia();cid=null;ended=false;controls();$('voice-summary').replaceChildren(create('h2','Your next steps'),create('p','A new assessment has no saved recommendations yet.'));$('summary-status').textContent='';$('summary-refresh').hidden=true;$('summary-cancel').hidden=true;serverSpeechDisabled=creditsExhausted;unsupportedSpeech.clear();$('summary-consent').checked=false;
  $('voice-start').disabled=true;state('Connecting','Opening your authorized case…');
  try{
   if(handoff){const linked=await api('/api/voice/redeem',{token:handoff});sid=linked.session_id;handoff=null}
   else{sid=null;if(continueSaved){try{sid=sessionStorage.getItem('yojana-case-id')}catch{}}}
   const data=await api('/api/voice/start',{session_id:sid});cid=data.call_id;sid=data.session_id;ended=false;
   try{sessionStorage.setItem('yojana-case-id',sid)}catch{}
   const history=await api('/api/voice/'+cid,undefined,'GET');$('voice-transcript').replaceChildren();for(const m of history.transcript)append(m.role,m.text);
   controls();retry(null);state('Ready',Recognition?'Choose Listen, or type. Your microphone stays off until you choose Listen.':capabilities.stt_configured?'Browser recognition is unavailable. Listen records up to 15 seconds for server transcription, or type.':'Speech recognition is unavailable in this browser. Type below to use the same session.');
  }catch(e){state('Unable to connect',e.message);$('voice-start').disabled=false;if(e.status===403||e.status===404){try{sessionStorage.removeItem('yojana-case-id')}catch{}sid=null}}
 }
 async function speak(result,final=false){
  cancelPlayback();const epoch=speechEpoch;
  const current=()=>epoch===speechEpoch&&!muted&&(!ended||final);
  lastSpoken={result,final};replay.disabled=false;const started=performance.now();let deviceStarted=false;
  if(muted){state(final?'Session ended':'Muted','Audio is muted. Unmute and choose Replay last reply.');return}
  if(!current())return;
  const text=(result.spoken_text||result.message).slice(0,360),lang=result.speech_language?result.speech_language+'-IN':result.text_fallback?'en-IN':(result.language||'en')+'-IN';
  const done=help=>{if(current())state(final?'Session ended':'Ready',help)};
  function device(code='device_fallback'){if(!current()||deviceStarted)return;deviceStarted=true;
   clearTimeout(playbackTimer);if(audio){audio.onended=null;audio.onerror=null;audio.onplaying=null;audio.pause();audio=null}if(audioURL){URL.revokeObjectURL(audioURL);audioURL=null}
   speechMetric(code,started);
   if(window.speechSynthesis&&window.SpeechSynthesisUtterance){const utterance=new SpeechSynthesisUtterance(text);utterance.lang=lang;
    utterance.onend=()=>{if(!current())return;clearTimeout(deviceTimer);done('Device speech finished. Your text and saved results remain available.')};
    utterance.onerror=()=>{if(!current())return;clearTimeout(deviceTimer);speechMetric('device_denied_or_unavailable',started);done('Device audio could not play. Tap Replay last reply to allow audio, or read the transcript.')};
    state(final?'Session ended':'Speaking','Using device voice. If you cannot hear it, tap Replay last reply.');
    deviceTimer=setTimeout(()=>{if(current()){window.speechSynthesis.cancel();speechMetric('device_timeout',started);done('No device audio was confirmed. Tap Replay last reply, check device volume, or read the transcript.')}},20000);
    try{window.speechSynthesis.speak(utterance)}catch{clearTimeout(deviceTimer);done('Device voice is unavailable. Tap Replay or read the transcript.')}
   }else done('No device voice is available. Read the transcript; your session and results are saved.');
  }
  if(!capabilities.tts_configured||serverSpeechDisabled||unsupportedSpeech.has(lang)){device();return}
  state(final?'Session ended':'Speaking','Your reply is ready. Preparing a short spoken response…');
  speechAbort=new AbortController();const controller=speechAbort,timer=setTimeout(()=>controller.abort(),12000);
  try{const response=await fetch('/api/voice/tts',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({call_id:cid,text,lang,request_id:result.request_id}),signal:controller.signal});
   if(!current())return;
   if(!response.ok){const error=await response.json().catch(()=>({}));
    if(response.status===409){done('A newer reply replaced this audio. Use Replay for the latest reply.');return;} // A newer reply superseded this audio.
    if(response.status===402){creditsExhausted=true;serverSpeechDisabled=true}
    else if(response.status===422)unsupportedSpeech.add(lang);else serverSpeechDisabled=true;
    $('voice-help').textContent=error.detail?.code?.replaceAll('_',' ')||'Server speech unavailable; using device voice or text.';device();return
   }
   const blob=await response.blob();if(!current())return;
   audioURL=URL.createObjectURL(blob);audio=new Audio(audioURL);
   audio.onended=()=>{if(!current())return;clearTimeout(playbackTimer);speechMetric('server_audio_ended',started);if(audioURL)URL.revokeObjectURL(audioURL);audioURL=null;done('Choose Listen or type to continue, or review your final results.')};
   audio.onerror=()=>{if(current()){serverSpeechDisabled=true;device('audio_decode_error')}};
   const player=audio;
   playbackTimer=setTimeout(()=>{if(current()&&audio===player){serverSpeechDisabled=true;device('playback_timeout')}},6000);
   player.onplaying=()=>{if(!current())return;clearTimeout(playbackTimer);playbackTimer=setTimeout(()=>{if(current()&&audio===player)device('playback_stalled')},30000);speechMetric('server_audio_started',started)};
   await player.play();retry(()=>speak(result,final));
  }catch(error){if(current()){serverSpeechDisabled=true;device(error.name==='NotAllowedError'?'autoplay_denied':error.name==='AbortError'?'tts_timeout':'tts_unavailable')}}finally{clearTimeout(timer)}
 }
 async function send(text,requestId=crypto.randomUUID()){
  if(busy||!cid||ended||!text.trim())return;const version=sessionVersion,turnCall=cid;cancelPlayback();stopCapture();busy=true;controls();state('Thinking','Reading your words and checking the scheme rules…');
  try{const data=await api('/api/voice/'+cid+'/turn',{text,lang:language(),language_override:language()!=='auto',request_id:requestId});
   if(ended||version!==sessionVersion)return;
   append('user',text);append('assistant',data.message+(data.question?.reason?'\nWhy I ask: '+data.question.reason:''));$('voice-text').value='';$('live-transcript').textContent='';retry(null);
   data.request_id=requestId;
   if(data.assessment_complete){ended=true;stopMedia();renderSummary(data.summary||{schemes:data.recommendations,caveat:'Guidance only; verify official sources and missing conditions.'});delivery({whatsapp:{status:'not_requested'}});controls();state('Session ended',data.assessment?.provisional?'Provisional results saved. More details or official verification are useful. Refine in your dashboard.':'Assessment complete. Your results are saved; you can refine them in the dashboard.');$('voice-start').disabled=false;$('voice-start').textContent='Fresh start';
    if($('summary-consent').checked)api('/api/voice/'+turnCall+'/end',{consent_whatsapp:true}).then(data=>settleDelivery(data,turnCall,version)).catch(e=>{if(version===sessionVersion)$('summary-status').textContent='Summary saved, but WhatsApp send was not requested successfully. '+e.message});
    void speak(data,true);
   }else void speak(data)
  }catch(e){if(!ended){state('Message not completed',e.name==='AbortError'?'The request timed out. Retry safely or type again.':e.message);$('voice-text').value=text;retry(()=>send(text,requestId))}}
  finally{busy=false;controls()}
 }
 function wav(buffer){
  const length=Math.min(Math.floor(buffer.duration*16000),240000), data=new ArrayBuffer(44+length*2),view=new DataView(data);
  const word=(offset,text)=>{for(let i=0;i<text.length;i++)view.setUint8(offset+i,text.charCodeAt(i))};
  word(0,'RIFF');view.setUint32(4,36+length*2,true);word(8,'WAVE');word(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);word(36,'data');view.setUint32(40,length*2,true);
  for(let i=0;i<length;i++){let sample=0;for(let c=0;c<buffer.numberOfChannels;c++)sample+=buffer.getChannelData(c)[Math.min(buffer.length-1,Math.floor(i*buffer.sampleRate/16000))]/buffer.numberOfChannels;sample=Math.max(-1,Math.min(1,sample));view.setInt16(44+2*i,sample<0?sample*32768:sample*32767,true)}
  return new Uint8Array(data)
 }
 async function serverRecord(){
  const epoch=captureEpoch,callId=cid;
  const current=()=>epoch===captureEpoch&&callId===cid&&!ended&&!muted;
  state('Microphone permission','Allow the microphone to record up to 15 seconds, or use typed input.');
  let microphone;
  try{
   microphone=await navigator.mediaDevices.getUserMedia({audio:true});
   if(!current()){microphone.getTracks().forEach(t=>t.stop());return}
   stream=microphone;const chunks=[],capture=new MediaRecorder(microphone);recorder=capture;
   capture.ondataavailable=e=>{if(current())chunks.push(e.data)};
   capture.onstop=async()=>{
    microphone.getTracks().forEach(t=>t.stop());
    if(stream===microphone)stream=null;if(recorder===capture)recorder=null;
    if(!current())return;
    clearTimeout(recordTimer);$('voice-listen').textContent='Listen';
    state('Transcribing','Sending this short audio clip to the configured speech service…');busy=true;controls();
    const controller=new AbortController();sttAbort=controller;
    try{
     const context=new (window.AudioContext||window.webkitAudioContext)();let decoded;
     try{decoded=await context.decodeAudioData(await new Blob(chunks).arrayBuffer())}finally{await context.close()}
     if(!current())return;
     const bytes=wav(decoded);let binary='';for(const b of bytes)binary+=String.fromCharCode(b);
     const data=await api('/api/voice/stt',{call_id:callId,audio:btoa(binary),lang:language()},'POST',controller.signal);
     if(!current())return;
     busy=false;controls();$('voice-text').value=data.text;await send(data.text);
    }catch(e){if(current()){state('Transcription unavailable','Please type your message. '+e.message);retry(listen)}}
    finally{if(sttAbort===controller)sttAbort=null;if(epoch===captureEpoch){busy=false;controls()}}
   };
   capture.start();$('voice-listen').textContent='Stop recording';state('Listening','Recording up to 15 seconds. Tap Stop recording when finished.');
   recordTimer=setTimeout(()=>{if(current()&&capture.state==='recording')capture.stop()},15000);
  }catch(e){microphone?.getTracks().forEach(t=>t.stop());if(current()){stream=null;state('Microphone unavailable','Permission was denied or recording is unsupported. You can type instead.');retry(listen)}}
 }
 function listen(){
  if(!cid||busy||muted||ended)return;
  if(recorder?.state==='recording'){clearTimeout(recordTimer);recorder.stop();return}
  stopMedia();
  if(!Recognition){if(capabilities.stt_configured&&navigator.mediaDevices&&window.MediaRecorder)return serverRecord();state('Type your message','Speech recognition is not available here. The typed session has the same reasoning and history.');$('voice-text').focus();return}
  const epoch=captureEpoch,capture=new Recognition();recognition=capture;let submitted=false;
  const current=()=>epoch===captureEpoch&&recognition===capture&&!muted&&!ended;
  capture.lang=language()==='auto'?(navigator.language||'en-IN'):language();capture.interimResults=true;capture.continuous=false;
  capture.onstart=()=>{if(current())state('Listening','Speak naturally. Tap interruption stops the old reply; typed input remains available.')};
  capture.onresult=e=>{
   if(!current()||submitted)return;
   const text=Array.from(e.results).map(r=>r[0].transcript).join(' ');$('live-transcript').textContent=text;$('voice-text').value=text;
   if(e.results[e.results.length-1].isFinal){submitted=true;state('Transcribing','Using the recognized text…');void send(text)}
  };
  capture.onerror=e=>{if(!current()||e.error==='aborted')return;state('Recognition unavailable','Microphone permission or language support failed. Type below or retry.');retry(listen)};
  capture.onend=()=>{if(current()&&!busy&&!submitted)state('Ready','Choose Listen to speak again or use typed input.')};
  try{state('Microphone permission','Waiting for browser microphone access…');capture.start()}catch{if(current()){state('Recognition unavailable','Please type or try Listen again.');retry(listen)}}
 }
 function renderSummary(snapshot){const dest=$('voice-summary');dest.replaceChildren(create('h2','Your ranked shortlist and next steps'));
  if(!snapshot.schemes.length)dest.append(create('p','There is not enough verified information to recommend a scheme yet. Continue your case from the dashboard.'));
  snapshot.schemes.forEach((item,i)=>{const card=create('article','','summary-card');card.append(create('h3',`${i+1}. ${item.name}`),create('p',item.benefit||'Benefit details need official verification.'),create('p',item.why||item.status));
   card.append(create('p','Missing: '+(item.missing_requirements?.map(f=>f.replaceAll('_',' ')).join(', ')||'Review any manual conditions at the source.')));
   for(const step of item.next_steps||[])card.append(create('p','Next: '+step));
   for(const condition of item.check_yourself||[])card.append(create('p','Check manually: '+condition));
   try{const url=new URL(item.source_url);if(url.protocol==='https:'){const a=create('a','Read the source and verify requirements ↗');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';card.append(a)}}catch{}
   dest.append(card)
  });dest.append(create('p',snapshot.caveat));
 }
 function delivery(data){const status=data.whatsapp.status;const labels={not_requested:'No WhatsApp summary requested.',pending:'Summary queued; not yet accepted by WAHA.',retrying:'Send failed; retry is scheduled. Delivery is not confirmed.',failed:'Summary send failed. Use Retry summary to request another attempt.',cancelled:'Summary cancelled. No new send will be attempted without renewed consent.',accepted_by_waha:'WAHA accepted the summary. Recipient delivery has not been verified.'};$('summary-status').textContent=labels[status]||'Summary status unavailable.';$('summary-refresh').hidden=false;$('summary-refresh').textContent=['not_requested','cancelled'].includes(status)?'Send summary with consent':status==='failed'?'Retry summary':'Check WhatsApp status';$('summary-refresh').dataset.status=status;$('summary-cancel').hidden=!['pending','retrying','failed'].includes(status)}
 async function settleDelivery(data,callId=cid,version=sessionVersion){
  if(callId!==cid||version!==sessionVersion)return;
  // A revocation while End was in flight must also cancel the resulting queue.
  if(!$('summary-consent').checked&&['pending','retrying','failed'].includes(data.whatsapp.status)){
   await api('/api/voice/'+callId+'/summary/cancel',{});data=await api('/api/voice/'+callId,undefined,'GET');
  }
  if(callId===cid&&version===sessionVersion)delivery(data);
 }
 async function cancelSummary(){
  const callId=cid,version=sessionVersion;if(!callId)return;
  try{await api('/api/voice/'+callId+'/summary/cancel',{});if(version!==sessionVersion)return;$('summary-consent').checked=false;await settleDelivery(await api('/api/voice/'+callId,undefined,'GET'),callId,version)}
  catch(e){if(version===sessionVersion)$('summary-status').textContent='Cancellation not confirmed. '+e.message}
 }
 async function refresh(){try{let data;const status=$('summary-refresh').dataset.status;
  if(['not_requested','cancelled'].includes(status)){if(!$('summary-consent').checked)throw new Error('Check the consent box before requesting a WhatsApp summary.');data=await api('/api/voice/'+cid+'/end',{consent_whatsapp:true})}
  else{if(status==='failed')await api('/api/voice/'+cid+'/summary/retry',{});data=await api('/api/voice/'+cid,undefined,'GET')}await settleDelivery(data)
 }catch(e){$('summary-status').textContent='Summary not confirmed. '+e.message}}
 async function end(){if(!cid||ended)return;ended=true;stopMedia();controls();state('Preparing summary','Finishing any in-flight turn, saving your minimized transcript and ranked next steps…');
   try{const data=await api('/api/voice/'+cid+'/end',{consent_whatsapp:$('summary-consent').checked});renderSummary(data.summary);await settleDelivery(data);state('Session ended','Your saved case remains available in the dashboard.');$('voice-start').disabled=false;$('voice-start').textContent='Fresh start';retry(null)}
  catch(e){ended=false;controls();state('Summary not saved',e.message);retry(end)}
 }
 $('voice-interrupt').onclick=interrupt;
 $('voice-start').textContent='Fresh start';$('voice-start').onclick=()=>start(false);$('voice-listen').onclick=listen;$('voice-end').onclick=end;$('voice-retry').onclick=()=>lastRetry?.();$('summary-refresh').onclick=refresh;
 $('summary-cancel').onclick=cancelSummary;
 $('summary-consent').onchange=()=>{if(!$('summary-consent').checked&&['pending','retrying','failed'].includes($('summary-refresh').dataset.status))void cancelSummary()};
 $('voice-mute').onclick=()=>{muted=!muted;$('voice-mute').setAttribute('aria-pressed',String(muted));$('voice-mute').textContent=muted?'Unmute':'Mute';if(muted)stopMedia();state(ended?'Session ended':muted?'Muted':'Ready',muted?'Microphone and playback are off. Typed input still works.':ended?'Tap Replay last reply to hear the final announcement, or review your saved results.':'Choose Listen to speak.')};
 $('voice-form').onsubmit=e=>{e.preventDefault();send($('voice-text').value)};
 fetch('/api/voice/capabilities').then(r=>r.json()).then(data=>{capabilities=data;for(const lang of data.languages){const o=create('option',lang.name+' · '+lang.code);o.value=lang.code;$('voice-language').append(o)}}).catch(()=>{$('coverage').textContent='Language coverage could not be loaded; typed input remains available.'});
 $('voice-language').onchange=()=>{const lang=capabilities.languages?.find(l=>l.code===language());$('coverage').textContent=lang?`Text: ${lang.text}; browser recognition: device dependent; Sarvam TTS: ${lang.sarvam_tts}. Live coverage is unverified.`:'Automatic text detection; browser listening follows the device language. Choose a language for more reliable recognition.'};
 window.addEventListener('pagehide',()=>{ended=true;stopMedia();clearInterval(pollTimer)});
 window.addEventListener('pageshow',event=>{if(event.persisted){cid=null;ended=false;busy=false;controls();$('voice-start').disabled=false;$('voice-start').textContent='Fresh start';state('Ready','Your accepted turns are saved. Resume to continue.')}});
})();
