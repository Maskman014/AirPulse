const $=s=>document.querySelector(s);
let state={country:'',location:''};

async function health(){try{const r=await fetch('/api/health');$('#systemStatus').textContent=r.ok?'Operational':'Unavailable'}catch{$('#systemStatus').textContent='Unavailable'}}

function showEmergency(text){const el=$('#emergencyBanner');el.textContent=text;el.hidden=false}

async function loadAQI(place){
  $('#aqStatus').textContent='Loading';$('#aqiValue').textContent='…';
  try{const r=await fetch('/api/aqi?'+new URLSearchParams({place}));const d=await r.json();if(!r.ok)throw new Error(d.error||'Live data unavailable');
    state.country=d.country_code||'';state.location=d.location||place;
    const isSouth=['IN','PK','BD','NP','LK'].includes(state.country);
    $('#brandName').textContent=isSouth?'VayuMitra':'CleanAir Companion';$('#chatName').textContent=$('#brandName').textContent;
    $('#aqiValue').textContent=d.aqi??'—';$('#aqStatus').textContent=d.category||'Unavailable';$('#aqAdvice').textContent=d.advice||'';
    $('#sourceName').textContent=d.source;$('#updatedTime').textContent=d.timestamp?new Date(d.timestamp).toLocaleString(): 'Unavailable';
    const banner=$('#verificationBanner');
    if(banner){banner.classList.add('verified');}
    const verification=$('#verificationText');
    if(verification){verification.textContent=`Live request successful · ${d.source} · ${d.station} · ${d.timestamp?new Date(d.timestamp).toLocaleString():'Timestamp unavailable'}`;}
    $('#scaleLabel').textContent=d.scale;$('#scaleDetail').textContent=d.scale;$('#stationName').textContent=d.station;$('#coverage').textContent=d.location;
    let pct=d.aqi==null?0:Math.min(d.aqi/500*360,360);let color=d.aqi<=50?'#54a96f':d.aqi<=100?'#c7b24d':d.aqi<=150?'#e19a44':d.aqi<=200?'#cf664a':d.aqi<=300?'#9c4d73':'#77353d';
    $('#aqRing').style.setProperty('--pct',pct+'deg');$('#aqRing').style.setProperty('--ring',color);
    const names={"PM2.5":['PM2.5','Fine particles'],PM10:['PM10','Coarse particles'],O3:['O₃','Ozone'],NO2:['NO₂','Nitrogen dioxide'],SO2:['SO₂','Sulfur dioxide'],CO:['CO','Carbon monoxide']};
    $('#pollutantGrid').innerHTML=Object.entries(d.pollutants).map(([k,v])=>`<div class="pollutant"><b>${v==null?'—':Number(v).toFixed(1)}</b><span>${names[k]?.[0]||k} · ${names[k]?.[1]||''}</span></div>`).join('');
    $('#protectionList').innerHTML=`<div><i>01</i><p>${d.aqi!=null&&d.aqi>100?'Reduce prolonged outdoor exertion and consider a certified, well-fitted particulate respirator outside.':'Keep activity proportional to the live pollutant profile and any local authority notice.'}</p></div><div><i>02</i><p>Close infiltration briefly during elevated outdoor particles; run a properly sized HEPA purifier indoors.</p></div><div><i>03</i><p>HEPA does not remove CO or toxic gases. Use outdoor-vented exhaust and functioning CO alarms.</p></div>`;
  }catch(e){$('#aqStatus').textContent='Unavailable';$('#aqiValue').textContent='—';$('#aqAdvice').textContent=e.message}
}

document.querySelectorAll('.nav').forEach(btn=>btn.addEventListener('click',()=>{document.querySelectorAll('.nav,.view').forEach(x=>x.classList.remove('active'));btn.classList.add('active');$('#'+btn.dataset.section).classList.add('active');$('#pageTitle').textContent=({dashboard:'Air quality overview',calculator:'Purifier sizing calculator',chat:'Air quality assistant'})[btn.dataset.section]}));

$('#locationSearch').addEventListener('click',()=>{const place=$('#locationInput').value.trim();if(place)loadAQI(place)});
$('#locationInput').addEventListener('keydown',e=>{if(e.key==='Enter'){const place=e.target.value.trim();if(place)loadAQI(place)}});

$('#calcForm').addEventListener('submit', async event => {
  event.preventDefault();
  const body={length:+$('#length').value,width:+$('#width').value,height:+$('#height').value,cadr:+$('#cadr').value,length_unit:$('#lengthUnit').value,cadr_unit:$('#cadrUnit').value};
  try {
    const r=await fetch('/api/calculator',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok) throw new Error(d.error);
    const ach=d.ach;
    $('#achValue').textContent=ach.toFixed(2);
    $('#volumeValue').textContent=d.volume_m3+' m³';
    $('#cadrValue').textContent=d.cadr_m3h+' m³/h';
    $('#cleanTime').textContent=d.approx_time_to_63_percent_clean?d.approx_time_to_63_percent_clean+' minutes':'—';
    $('#calcWarning').textContent=d.warning;
    const status=$('#achStatus');
    status.className='ach-status';
    if(ach>=4.8){
      status.classList.add('recommended');
      status.innerHTML='<span aria-hidden="true">●</span><b>Recommended for wildfire/heavy smog (≥4.8 ACH)</b>';
    } else if(ach>=2){
      status.classList.add('moderate');
      status.innerHTML='<span aria-hidden="true">◐</span><b>Moderate filtration — consider higher CADR if needed</b>';
    } else {
      status.classList.add('low');
      status.innerHTML='<span aria-hidden="true">○</span><b>Low filtration — additional clean-air capacity may help</b>';
    }
  } catch(error) { alert(error.message); }
});

async function sendMessage(text){
  const box=$('#chatMessages');
  if(!box) return;
  box.insertAdjacentHTML('beforeend','<div class="message user"><span class="message-label">You</span></div>');
  box.lastElementChild.append(document.createTextNode(text));
  const waiting=document.createElement('div');waiting.className='message bot';waiting.innerHTML='<span class="message-label">Assistant</span>Thinking…';box.appendChild(waiting);box.scrollTop=box.scrollHeight;
  try {
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,location:state.location,country_code:state.country})});
    const d=await r.json();
    const reply=d.reply||d.error||'Unable to respond.';
    waiting.innerHTML='<span class="message-label">Assistant</span>';
    waiting.append(document.createTextNode(reply));
    if(d.emergency) showEmergency(reply);
  } catch(error) {
    waiting.innerHTML='<span class="message-label">Assistant</span>';
    waiting.append(document.createTextNode('Connection error. If this involves breathing distress or a CO alarm, call emergency services now.'));
  }
}

const chatForm=$('#chatForm');
if(chatForm){chatForm.addEventListener('submit',e=>{e.preventDefault();const input=$('#chatInput');if(input&&input.value.trim()){sendMessage(input.value.trim());input.value=''}});}
const clearChat=$('#clearChat');
if(clearChat){clearChat.addEventListener('click',()=>{$('#chatMessages').innerHTML='<div class="message bot"><span class="message-label">Assistant</span>Chat cleared. How can I help with air quality or indoor protection?</div>'});}

document.querySelectorAll('.quick-prompts button').forEach(button=>button.addEventListener('click',()=>sendMessage(button.dataset.prompt)));
const emergencyQuick=$('#emergencyQuick');
if(emergencyQuick){emergencyQuick.addEventListener('click',()=>{const chatNav=document.querySelector('.nav[data-section="chat"]');if(chatNav)chatNav.click();sendMessage('I need P0 emergency guidance for a CO alarm or severe breathing symptoms.')});}

health();
