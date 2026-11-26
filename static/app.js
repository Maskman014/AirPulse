'use strict';

/* ---------- Constants ---------- */
const AQI_CACHE_TTL_MS = 10 * 60 * 1000; // matches the server-side cache
const AQI_CACHE_MAX = 30;
const AQI_RING_MAX = 500;
const AQI_COLORS = [[50, '#54a96f'], [100, '#c7b24d'], [150, '#e19a44'], [200, '#cf664a'], [300, '#9c4d73'], [Infinity, '#77353d']];
const AQI_UNKNOWN_COLOR = '#8a8f98';
const ACH_RECOMMENDED = 4.8;
const ACH_MODERATE = 2;
const OFFLINE_CHAT_MSG = 'Connection error. If this involves breathing distress or a CO alarm, call emergency services now.';
const CHAT_CLEARED_MSG = 'Chat cleared. How can I help with air quality or indoor protection?';
const EMERGENCY_PROMPT = 'I need P0 emergency guidance for a CO alarm or severe breathing symptoms.';
const POLLUTANT_NAMES = {
  'PM2.5': ['PM2.5', 'Fine particles'], PM10: ['PM10', 'Coarse particles'], O3: ['O₃', 'Ozone'],
  NO2: ['NO₂', 'Nitrogen dioxide'], SO2: ['SO₂', 'Sulfur dioxide'], CO: ['CO', 'Carbon monoxide'],
};
const PAGE_TITLES = { dashboard: 'Air quality overview', calculator: 'Purifier sizing calculator', chat: 'Air quality assistant' };
const ACH_STATUS = {
  recommended: ['●', 'Recommended for wildfire/heavy smog (≥4.8 ACH)'],
  moderate: ['◐', 'Moderate filtration — consider higher CADR if needed'],
  low: ['○', 'Low filtration — additional clean-air capacity may help'],
};

/* ---------- Helpers ---------- */
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);
const setText = (selector, text) => { $(selector).textContent = text; };
const formatTime = (iso) => (iso ? new Date(iso).toLocaleString() : 'Unavailable');

/** fetch + JSON parse; non-JSON bodies become `{}` and HTTP errors throw with the server's message. */
async function requestJSON(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

const postJSON = (url, body) => requestJSON(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* ---------- App state ---------- */
const state = { country: '', location: '' };
const aqiCache = new Map(); // normalized place -> { data, expires }
let aqiController = null;   // aborts the previous in-flight lookup

async function checkHealth() {
  try {
    const response = await fetch('/api/health');
    setText('#systemStatus', response.ok ? 'Operational' : 'Unavailable');
  } catch {
    setText('#systemStatus', 'Unavailable');
  }
}

function showEmergency(text) {
  const banner = $('#emergencyBanner');
  banner.textContent = text;
  banner.hidden = false;
}

/* ---------- Dashboard ---------- */
const aqiColor = (aqi) => (aqi == null ? AQI_UNKNOWN_COLOR : AQI_COLORS.find(([max]) => aqi <= max)[1]);

function protectionTips(aqi) {
  const first = aqi != null && aqi > 100
    ? 'Reduce prolonged outdoor exertion and consider a certified, well-fitted particulate respirator outside.'
    : 'Keep activity proportional to the live pollutant profile and any local authority notice.';
  const tips = [
    first,
    'Close infiltration briefly during elevated outdoor particles; run a properly sized HEPA purifier indoors.',
    'HEPA does not remove CO or toxic gases. Use outdoor-vented exhaust and functioning CO alarms.',
  ];
  return tips.map((tip, i) => `<div><i>0${i + 1}</i><p>${tip}</p></div>`).join('');
}

function renderPollutants(pollutants) {
  return Object.entries(pollutants).map(([key, value]) => {
    const [symbol, label] = POLLUTANT_NAMES[key] || [key, ''];
    const reading = value == null ? '—' : Number(value).toFixed(1);
    return `<div class="pollutant"><b>${reading}</b><span>${symbol} · ${label}</span></div>`;
  }).join('');
}

function renderAQI(d, place) {
  state.country = d.country_code || '';
  state.location = d.location || place;
  setText('#brandName', d.identity);
  setText('#chatName', d.identity);

  setText('#aqiValue', d.aqi ?? '—');
  setText('#aqStatus', d.category || 'Unavailable');
  setText('#aqAdvice', d.advice || '');
  setText('#sourceName', d.source);
  setText('#updatedTime', formatTime(d.timestamp));
  setText('#scaleLabel', d.scale);
  setText('#scaleDetail', d.scale);
  setText('#stationName', d.station);
  setText('#coverage', d.location);

  $('#verificationBanner')?.classList.add('verified');
  const verification = $('#verificationText');
  if (verification) {
    const when = d.timestamp ? formatTime(d.timestamp) : 'Timestamp unavailable';
    verification.textContent = `Live request successful · ${d.source} · ${d.station} · ${when}`;
  }

  const ring = $('#aqRing');
  ring.style.setProperty('--pct', `${d.aqi == null ? 0 : Math.min((d.aqi / AQI_RING_MAX) * 360, 360)}deg`);
  ring.style.setProperty('--ring', aqiColor(d.aqi));

  $('#pollutantGrid').innerHTML = renderPollutants(d.pollutants);
  $('#protectionList').innerHTML = protectionTips(d.aqi);
}

async function fetchAQI(place, signal) {
  const key = place.toLowerCase();
  const hit = aqiCache.get(key);
  if (hit && hit.expires > Date.now()) return hit.data;

  const data = await requestJSON(`/api/aqi?${new URLSearchParams({ place })}`, { signal });
  aqiCache.delete(key);
  aqiCache.set(key, { data, expires: Date.now() + AQI_CACHE_TTL_MS });
  if (aqiCache.size > AQI_CACHE_MAX) aqiCache.delete(aqiCache.keys().next().value);
  return data;
}

async function loadAQI(place) {
  aqiController?.abort(); // a newer search supersedes any request still in flight
  aqiController = new AbortController();
  const { signal } = aqiController;

  setText('#aqStatus', 'Loading');
  setText('#aqiValue', '…');
  try {
    renderAQI(await fetchAQI(place, signal), place);
  } catch (error) {
    if (error.name === 'AbortError') return;
    setText('#aqStatus', 'Unavailable');
    setText('#aqiValue', '—');
    setText('#aqAdvice', error.message);
  }
}

function submitLocation() {
  const place = $('#locationInput').value.trim();
  if (place) loadAQI(place);
}

/* ---------- Navigation ---------- */
$$('.nav').forEach((button) => button.addEventListener('click', () => {
  $$('.nav, .view').forEach((node) => node.classList.remove('active'));
  button.classList.add('active');
  $(`#${button.dataset.section}`).classList.add('active');
  setText('#pageTitle', PAGE_TITLES[button.dataset.section]);
}));

$('#locationSearch').addEventListener('click', submitLocation);
$('#locationInput').addEventListener('keydown', (event) => { if (event.key === 'Enter') submitLocation(); });

/* ---------- Purifier calculator ---------- */
function renderAchStatus(ach) {
  const level = ach >= ACH_RECOMMENDED ? 'recommended' : ach >= ACH_MODERATE ? 'moderate' : 'low';
  const [icon, label] = ACH_STATUS[level];
  const status = $('#achStatus');
  status.className = `ach-status ${level}`;
  status.innerHTML = `<span aria-hidden="true">${icon}</span><b>${label}</b>`;
}

$('#calcForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const number = (id) => Number($(id).value);
  try {
    const d = await postJSON('/api/calculator', {
      length: number('#length'), width: number('#width'), height: number('#height'), cadr: number('#cadr'),
      length_unit: $('#lengthUnit').value, cadr_unit: $('#cadrUnit').value,
    });
    setText('#achValue', d.ach.toFixed(2));
    setText('#volumeValue', `${d.volume_m3} m³`);
    setText('#cadrValue', `${d.cadr_m3h} m³/h`);
    setText('#cleanTime', d.approx_time_to_63_percent_clean ? `${d.approx_time_to_63_percent_clean} minutes` : '—');
    setText('#calcWarning', d.warning);
    renderAchStatus(d.ach);
  } catch (error) {
    setText('#calcWarning', error.message);
  }
});

/* ---------- Chat ---------- */
function addMessage(role, text) {
  const box = $('#chatMessages');
  const message = document.createElement('div');
  message.className = `message ${role}`;
  const label = document.createElement('span');
  label.className = 'message-label';
  label.textContent = role === 'user' ? 'You' : 'Assistant';
  message.append(label, document.createTextNode(text));
  box.appendChild(message);
  box.scrollTop = box.scrollHeight;
  return message;
}

function setReply(message, text) {
  message.lastChild.textContent = text;
  const box = $('#chatMessages');
  box.scrollTop = box.scrollHeight;
}

async function sendMessage(text) {
  if (!$('#chatMessages')) return;
  addMessage('user', text);
  const waiting = addMessage('bot', 'Thinking…');
  try {
    const d = await postJSON('/api/chat', { message: text, location: state.location, country_code: state.country });
    setReply(waiting, d.reply || 'Unable to respond.');
    if (d.emergency) showEmergency(d.reply);
  } catch (error) {
    // Server-side validation errors are shown as-is; network failures get the safety message.
    setReply(waiting, error instanceof TypeError ? OFFLINE_CHAT_MSG : error.message);
  }
}

$('#chatForm')?.addEventListener('submit', (event) => {
  event.preventDefault();
  const input = $('#chatInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  sendMessage(text);
});

$('#clearChat')?.addEventListener('click', () => {
  $('#chatMessages').replaceChildren();
  addMessage('bot', CHAT_CLEARED_MSG);
  fetch('/api/chat/reset', { method: 'POST' }).catch(() => {}); // also drop the server-side history
});

$$('.quick-prompts button').forEach((button) => button.addEventListener('click', () => sendMessage(button.dataset.prompt)));

$('#emergencyQuick')?.addEventListener('click', () => {
  $('.nav[data-section="chat"]')?.click();
  sendMessage(EMERGENCY_PROMPT);
});

checkHealth();
