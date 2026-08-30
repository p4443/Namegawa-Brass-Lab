import {
  positiveModulo,
  quantizeNotes,
  transcribeMonophonic,
  writtenTrumpetPitch,
} from './transcription-core.mjs';

const $ = (id) => document.getElementById(id);
const isFreeMode = location.protocol !== 'file:' || new URLSearchParams(location.search).get('mode') === 'free';
const state = {
  stream: null,
  audioContext: null,
  sourceNode: null,
  analyserNode: null,
  recorderNode: null,
  silentGain: null,
  monitorFrame: 0,
  microphoneTimer: 0,
  recording: false,
  recordingStartedAt: 0,
  chunks: [],
  sampleRate: 44100,
  audioBlob: null,
  audioUrl: '',
  notes: [],
  selectedId: null,
  history: [],
  userTranspose: 0,
  fitScore: false,
  tapTimes: [],
};

const NOTE_NAMES_SHARP = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
const NOTE_NAMES_FLAT = ['C', 'D♭', 'D', 'E♭', 'E', 'F', 'G♭', 'G', 'A♭', 'A', 'B♭', 'B'];
const MAJOR_SIGNATURES = [0, 7, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5];
const MAX_RECORDING_SECONDS = 10 * 60;

function settings() {
  const tempo = Math.max(30, Math.min(300, Number($('tempo').value) || 120));
  const numerator = Math.max(1, Math.min(32, Number($('meterNumerator').value) || 4));
  const denominator = Number($('meterDenominator').value) || 4;
  const division = Number($('division').value) || 8;
  const pickupBeats = Math.max(0, Number($('pickupBeats').value) || 0);
  return {
    tempo,
    numerator,
    denominator,
    division,
    pickupBeats,
    countInMeasures: Math.max(0, Number($('countIn').value) || 0),
    quarterMs: 60000 / tempo,
    gridQuarter: 4 / division,
    measureQuarter: (numerator * 4) / denominator,
    pickupQuarter: (pickupBeats * 4) / denominator,
  };
}

function setStatus(message, detail = '') {
  $('statusLine').textContent = message;
  if (detail) $('captureDetail').textContent = detail;
}

function lockSettings(locked) {
  $('recordingSettings').querySelectorAll('input, select, button').forEach((control) => {
    control.disabled = locked;
  });
  $('audioFile').disabled = locked;
}

function formatTime(seconds) {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  return `${Math.floor(safe / 60)}:${String(Math.floor(safe % 60)).padStart(2, '0')}`;
}

function initializeMode() {
  $('modeBadge').textContent = isFreeMode ? '無料Web版' : 'オフライン版';
  $('modeNotice').textContent = isFreeMode
    ? '無料Web版では録音・採譜・修正・移調・再生を利用できます。WAV・MIDI・MusicXMLの保存はオフライン版の機能です。画面を閉じると作業内容は消去されます。'
    : 'オフライン版です。録音・譜面データはこの端末内だけで処理され、サーバーへ送信されません。';
  $('exportMessage').textContent = isFreeMode
    ? '無料Web版では保存できません。'
    : '録音と修正済み譜面を端末へ保存します。';
  [$('wavExport'), $('midiExport'), $('xmlExport')].forEach((button) => {
    button.disabled = isFreeMode;
  });
}

function resetMeter() {
  $('inputMeter').style.transform = 'translateY(100%)';
}

function monitorInput() {
  if (!state.analyserNode) return;
  const values = new Uint8Array(state.analyserNode.fftSize);
  state.analyserNode.getByteTimeDomainData(values);
  let energy = 0;
  for (const value of values) {
    const centered = (value - 128) / 128;
    energy += centered * centered;
  }
  const level = Math.min(1, Math.sqrt(energy / values.length) * 5);
  $('inputMeter').style.transform = `translateY(${(1 - level) * 100}%)`;
  state.monitorFrame = requestAnimationFrame(monitorInput);
}

function recorderWorkletUrl() {
  const embeddedSource = $('recorderWorkletSource')?.textContent;
  if (!embeddedSource) return './recorder-worklet.js';
  return URL.createObjectURL(new Blob([embeddedSource], { type: 'text/javascript' }));
}

async function enableMicrophone() {
  if (state.stream) return;
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
    });
    state.audioContext = new AudioContext();
    const workletUrl = recorderWorkletUrl();
    try {
      await state.audioContext.audioWorklet.addModule(workletUrl);
    } finally {
      if (workletUrl.startsWith('blob:')) URL.revokeObjectURL(workletUrl);
    }
    await state.audioContext.resume();
    state.sampleRate = state.audioContext.sampleRate;
    state.sourceNode = state.audioContext.createMediaStreamSource(state.stream);
    state.analyserNode = state.audioContext.createAnalyser();
    state.analyserNode.fftSize = 1024;
    state.sourceNode.connect(state.analyserNode);
    state.monitorFrame = requestAnimationFrame(monitorInput);
    state.microphoneTimer = window.setTimeout(() => {
      if (state.recording) stopRecording();
      disableMicrophone();
      setStatus('安全のためマイクを自動でオフにしました。');
    }, MAX_RECORDING_SECONDS * 1000);
    $('microphoneButton').textContent = 'マイクをオフ';
    $('recordButton').disabled = false;
    $('captureState').textContent = 'マイク入力中';
    setStatus('マイクを有効にしました。', '入力メーターを確認して録音を開始してください。');
  } catch (error) {
    state.stream = null;
    $('captureState').textContent = 'マイクを使用できません';
    setStatus('マイクを使用できません。ブラウザーとmacOSのマイク許可を確認してください。');
  }
}

async function disableMicrophone() {
  if (state.recording) await stopRecording();
  cancelAnimationFrame(state.monitorFrame);
  clearTimeout(state.microphoneTimer);
  try { state.recorderNode?.disconnect(); } catch {}
  try { state.sourceNode?.disconnect(); } catch {}
  try { state.analyserNode?.disconnect(); } catch {}
  try { state.silentGain?.disconnect(); } catch {}
  state.stream?.getTracks().forEach((track) => track.stop());
  if (state.audioContext?.state !== 'closed') await state.audioContext?.close();
  state.stream = null;
  state.audioContext = null;
  state.sourceNode = null;
  state.analyserNode = null;
  state.recorderNode = null;
  state.silentGain = null;
  resetMeter();
  $('microphoneButton').textContent = 'マイクをオン';
  $('recordButton').disabled = true;
  $('captureState').textContent = '録音待機';
}

async function runSilentCountIn(recordingSettings) {
  const totalBeats = recordingSettings.numerator * recordingSettings.countInMeasures;
  if (!totalBeats) return true;
  const beatMs = recordingSettings.quarterMs * 4 / recordingSettings.denominator;
  lockSettings(true);
  $('recordButton').disabled = true;
  for (let beat = 0; beat < totalBeats; beat += 1) {
    const current = (beat % recordingSettings.numerator) + 1;
    $('captureState').textContent = `予備カウント ${current}`;
    $('captureDetail').textContent = `${Math.floor(beat / recordingSettings.numerator) + 1}小節目 / ${recordingSettings.countInMeasures}小節（無音）`;
    await new Promise((resolve) => setTimeout(resolve, beatMs));
  }
  return Boolean(state.stream);
}

async function startRecording() {
  if (!state.stream || !state.audioContext) return;
  const recordingSettings = settings();
  if (!await runSilentCountIn(recordingSettings)) return;
  state.chunks = [];
  state.recorderNode = new AudioWorkletNode(state.audioContext, 'pcm-recorder');
  state.silentGain = state.audioContext.createGain();
  state.silentGain.gain.value = 0;
  state.recorderNode.port.onmessage = (event) => {
    if (state.recording && event.data?.length) state.chunks.push(event.data);
  };
  state.sourceNode.connect(state.recorderNode);
  state.recorderNode.connect(state.silentGain);
  state.silentGain.connect(state.audioContext.destination);
  state.recording = true;
  state.recordingStartedAt = performance.now();
  $('recordButton').disabled = false;
  $('recordButton').textContent = '録音停止';
  $('recordButton').classList.add('is-recording');
  $('captureState').textContent = '録音中';
  $('captureDetail').textContent = 'クリック音は鳴りません。設定したテンポで演奏してください。';
  setStatus('録音しています。停止後に音声全体を解析します。');
}

function concatenateChunks(chunks) {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const samples = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    samples.set(chunk, offset);
    offset += chunk.length;
  }
  return samples;
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  write(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (const sample of samples) {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

async function stopRecording() {
  if (!state.recording) return;
  state.recording = false;
  try { state.recorderNode?.disconnect(); } catch {}
  try { state.silentGain?.disconnect(); } catch {}
  state.recorderNode = null;
  state.silentGain = null;
  $('recordButton').textContent = '録音開始';
  $('recordButton').classList.remove('is-recording');
  lockSettings(false);
  $('recordButton').disabled = !state.stream;
  const samples = concatenateChunks(state.chunks);
  state.chunks = [];
  if (samples.length < state.sampleRate / 4) {
    $('captureState').textContent = '録音待機';
    setStatus('録音が短すぎます。1音を少し長めに演奏してください。');
    return;
  }
  const blob = encodeWav(samples, state.sampleRate);
  $('captureState').textContent = '解析中';
  await processAudio(blob, samples, state.sampleRate);
}

function strongestChannel(buffer) {
  let selected = buffer.getChannelData(0);
  let selectedEnergy = -1;
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    const samples = buffer.getChannelData(channel);
    let energy = 0;
    for (let index = 0; index < samples.length; index += 1) energy += samples[index] * samples[index];
    if (energy > selectedEnergy) {
      selected = samples;
      selectedEnergy = energy;
    }
  }
  let peak = 0;
  for (const sample of selected) peak = Math.max(peak, Math.abs(sample));
  if (!peak || peak >= 0.2) return selected;
  const gain = Math.min(8, 0.35 / peak);
  return Float32Array.from(selected, (sample) => Math.max(-1, Math.min(1, sample * gain)));
}

async function decodeBlob(blob) {
  const context = new AudioContext();
  try {
    const buffer = await context.decodeAudioData(await blob.arrayBuffer());
    return { samples: strongestChannel(buffer), sampleRate: buffer.sampleRate };
  } finally {
    await context.close();
  }
}

async function processAudio(blob, suppliedSamples = null, suppliedRate = null) {
  setStatus('音高・開始位置・音価を解析しています。');
  await new Promise((resolve) => requestAnimationFrame(resolve));
  try {
    const decoded = suppliedSamples
      ? { samples: suppliedSamples, sampleRate: suppliedRate }
      : await decodeBlob(blob);
    if (decoded.samples.length / decoded.sampleRate > MAX_RECORDING_SECONDS) {
      throw new Error('TOO_LONG');
    }
    const detected = transcribeMonophonic(decoded.samples, decoded.sampleRate);
    if (!detected.length) throw new Error('NO_NOTES');
    state.notes = quantizeNotes(detected, settings()).map((note, index) => ({
      ...note,
      id: `note-${Date.now()}-${index}`,
    }));
    state.history = [];
    state.selectedId = null;
    state.userTranspose = 0;
    setAudioBlob(encodeWav(decoded.samples, decoded.sampleRate));
    renderScore();
    updateToolState();
    $('captureState').textContent = '採譜完了';
    $('captureDetail').textContent = `${state.notes.length}音を検出しました。譜面を選択して必要な箇所だけ修正してください。`;
    setStatus(`${state.notes.length}音をB♭トランペット譜へ変換しました。`);
    location.hash = 'score';
  } catch (error) {
    $('captureState').textContent = '解析できませんでした';
    const message = error.message === 'TOO_LONG'
      ? '録音は10分以内にしてください。'
      : error.message === 'NO_NOTES'
        ? '演奏音を検出できませんでした。音量とマイク位置を確認してください。'
        : '音声を読み込めませんでした。WAV、MP3、M4Aを使用してください。';
    setStatus(message, message);
  }
}

function setAudioBlob(blob) {
  if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
  state.audioBlob = blob;
  state.audioUrl = URL.createObjectURL(blob);
  $('audioPlayback').src = state.audioUrl;
  $('audioPlayback').load();
  $('playbackButton').disabled = false;
  $('playbackProgress').disabled = false;
  $('playbackRate').disabled = false;
}

function inferredKeySignature() {
  const selected = $('keySignature').value;
  if (selected !== 'auto') return Number(selected);
  if (!state.notes.length) return 0;
  const weights = Array(12).fill(0);
  for (const note of state.notes) weights[positiveModulo(note.pitch, 12)] += note.durationQuarter;
  const majorProfile = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
  const candidates = Array.from({ length: 12 }, (_, root) => ({
    root,
    score: weights.reduce((sum, weight, pitchClass) => sum + weight * majorProfile[positiveModulo(pitchClass - root, 12)], 0),
  }));
  candidates.sort((left, right) => right.score - left.score || Math.abs(MAJOR_SIGNATURES[left.root]) - Math.abs(MAJOR_SIGNATURES[right.root]));
  return MAJOR_SIGNATURES[positiveModulo(candidates[0].root + 2 + state.userTranspose, 12)];
}

function scorePitchPosition(midi, preferFlats) {
  const sharpOffsets = [0, 0, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6];
  const flatOffsets = [0, 1, 1, 2, 2, 3, 4, 4, 5, 5, 6, 6];
  const octave = Math.floor(midi / 12) - 1;
  return (octave - 4) * 7 + (preferFlats ? flatOffsets : sharpOffsets)[positiveModulo(midi, 12)] - 4;
}

function renderScore() {
  const svg = $('scoreSvg');
  $('emptyScore').hidden = state.notes.length > 0;
  if (!state.notes.length) {
    svg.replaceChildren();
    $('scoreSummary').textContent = 'まだ採譜されていません';
    return;
  }
  const recordingSettings = settings();
  const lastQuarter = Math.max(...state.notes.map((note) => note.startQuarter + note.durationQuarter));
  const firstMeasure = recordingSettings.pickupQuarter || recordingSettings.measureQuarter;
  const totalQuarter = firstMeasure + Math.ceil(Math.max(0, lastQuarter - firstMeasure) / recordingSettings.measureQuarter) * recordingSettings.measureQuarter;
  const fifths = inferredKeySignature();
  const preferFlats = fifths < 0;
  const signatureCount = Math.abs(fifths);
  const scoreStart = 142 + signatureCount * 13;
  const quarterWidth = 72;
  const width = Math.max(900, scoreStart + totalQuarter * quarterWidth + 70);
  const staffTop = 92;
  const staffGap = 12;
  svg.setAttribute('viewBox', `0 0 ${width} 240`);
  svg.setAttribute('width', String(width));
  const namespace = 'http://www.w3.org/2000/svg';
  const element = (name, attributes = {}, text = '') => {
    const node = document.createElementNS(namespace, name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text) node.textContent = text;
    return node;
  };
  svg.replaceChildren(element('rect', { width, height: 240, fill: '#fffef9' }));
  svg.append(element('text', { x: 30, y: 28, fill: '#4f5752', 'font-size': 12, 'font-weight': 700 }, `B♭ TRUMPET  ${recordingSettings.numerator}/${recordingSettings.denominator}  ♩=${recordingSettings.tempo}`));
  for (let line = 0; line < 5; line += 1) {
    const y = staffTop + line * staffGap;
    svg.append(element('line', { x1: 28, y1: y, x2: width - 24, y2: y, stroke: '#252b28', 'stroke-width': 1 }));
  }
  svg.append(element('text', { x: 36, y: 139, fill: '#18201d', 'font-size': 72, 'font-family': 'Apple Symbols, Noto Music, serif' }, '𝄞'));
  const signaturePositions = fifths > 0 ? [92, 110, 86, 104, 122, 98, 116] : [116, 98, 122, 104, 128, 110, 134];
  for (let index = 0; index < signatureCount; index += 1) {
    svg.append(element('text', {
      x: 84 + index * 13,
      y: signaturePositions[index],
      fill: '#18201d',
      'font-size': 30,
      'text-anchor': 'middle',
      'dominant-baseline': 'central',
      'font-family': 'Apple Symbols, Noto Music, serif',
    }, fifths > 0 ? '♯' : '♭'));
  }
  const timeX = 102 + signatureCount * 13;
  const timeAttributes = {
    x: timeX,
    'text-anchor': 'middle',
    'dominant-baseline': 'central',
    fill: '#18201d',
    'font-family': 'Georgia, Times New Roman, serif',
    'font-size': 24,
    'font-weight': 700,
  };
  svg.append(element('text', { ...timeAttributes, y: staffTop + staffGap }, recordingSettings.numerator));
  svg.append(element('text', { ...timeAttributes, y: staffTop + staffGap * 3 }, recordingSettings.denominator));

  for (let boundary = firstMeasure; boundary < totalQuarter + 0.001; boundary += recordingSettings.measureQuarter) {
    const x = scoreStart + boundary * quarterWidth;
    svg.append(element('line', { x1: x, y1: staffTop, x2: x, y2: staffTop + staffGap * 4, stroke: '#555d58', 'stroke-width': boundary >= totalQuarter - 0.001 ? 3 : 1 }));
  }

  for (const note of state.notes) {
    const written = writtenTrumpetPitch(note.pitch, state.userTranspose);
    const x = scoreStart + note.startQuarter * quarterWidth + 12;
    const y = staffTop + staffGap * 3 - scorePitchPosition(written, preferFlats) * 6;
    const group = element('g', { class: `score-note${note.id === state.selectedId ? ' selected' : ''}`, 'data-note-id': note.id, tabindex: 0, role: 'button', 'aria-label': `${(preferFlats ? NOTE_NAMES_FLAT : NOTE_NAMES_SHARP)[positiveModulo(written, 12)]}の音符` });
    const staffBottom = staffTop + staffGap * 4;
    for (let ledgerY = staffTop - staffGap; ledgerY >= y - 3; ledgerY -= staffGap) group.append(element('line', { x1: x - 13, y1: ledgerY, x2: x + 13, y2: ledgerY, stroke: '#18201d' }));
    for (let ledgerY = staffBottom + staffGap; ledgerY <= y + 3; ledgerY += staffGap) group.append(element('line', { x1: x - 13, y1: ledgerY, x2: x + 13, y2: ledgerY, stroke: '#18201d' }));
    group.append(element('ellipse', { cx: x, cy: y, rx: 8.5, ry: 5.8, transform: `rotate(-18 ${x} ${y})`, fill: note.durationQuarter >= 2 ? '#fffef9' : '#18201d', stroke: '#18201d', 'stroke-width': 1.8 }));
    if (note.durationQuarter < 4) {
      const stemDown = y <= staffTop + staffGap * 2;
      const stemX = stemDown ? x - 7 : x + 7;
      group.append(element('line', { x1: stemX, y1: y, x2: stemX, y2: stemDown ? y + 35 : y - 35, stroke: '#18201d', 'stroke-width': 2 }));
    }
    svg.append(group);
  }
  $('scoreSummary').textContent = `${state.notes.length}音 / 移調 ${state.userTranspose >= 0 ? '+' : ''}${state.userTranspose}半音`;
  $('scoreViewport').classList.toggle('fit', state.fitScore);
  $('fitScoreButton').textContent = state.fitScore ? '原寸表示' : '全体表示';
}

function snapshot() {
  state.history.push({ notes: state.notes.map((note) => ({ ...note })), selectedId: state.selectedId, userTranspose: state.userTranspose });
  if (state.history.length > 40) state.history.shift();
  $('undoButton').disabled = false;
}

function selectedNote() {
  return state.notes.find((note) => note.id === state.selectedId) ?? null;
}

function updateToolState() {
  $('noteTools').querySelectorAll('[data-edit], #noteDuration').forEach((control) => {
    control.disabled = !selectedNote();
  });
  $('transposeTools').disabled = !state.notes.length;
  $('wavExport').disabled = isFreeMode || !state.audioBlob;
  $('midiExport').disabled = isFreeMode || !state.notes.length;
  $('xmlExport').disabled = isFreeMode || !state.notes.length;
  const note = selectedNote();
  if (!note) {
    $('selectedNoteLabel').textContent = '未選択';
    return;
  }
  const written = writtenTrumpetPitch(note.pitch, state.userTranspose);
  const names = inferredKeySignature() < 0 ? NOTE_NAMES_FLAT : NOTE_NAMES_SHARP;
  $('selectedNoteLabel').textContent = names[positiveModulo(written, 12)];
  const options = [...$('noteDuration').options].map((option) => Number(option.value));
  $('noteDuration').value = String(options.reduce((closest, value) => Math.abs(value - note.durationQuarter) < Math.abs(closest - note.durationQuarter) ? value : closest, options[0]));
}

function editSelected(action) {
  const note = selectedNote();
  if (!note) return;
  snapshot();
  const grid = settings().gridQuarter;
  if (action === 'pitch-down') note.pitch = Math.max(0, note.pitch - 1);
  if (action === 'pitch-up') note.pitch = Math.min(127, note.pitch + 1);
  if (action === 'time-left') note.startQuarter = Math.max(0, note.startQuarter - grid);
  if (action === 'time-right') note.startQuarter += grid;
  if (action === 'delete') {
    state.notes = state.notes.filter((candidate) => candidate.id !== note.id);
    state.selectedId = null;
  }
  state.notes.sort((left, right) => left.startQuarter - right.startQuarter);
  renderScore();
  updateToolState();
  setStatus('音符を修正しました。元に戻すこともできます。');
}

function undo() {
  const previous = state.history.pop();
  if (!previous) return;
  state.notes = previous.notes;
  state.selectedId = previous.selectedId;
  state.userTranspose = previous.userTranspose;
  $('undoButton').disabled = state.history.length === 0;
  updateTransposeLabel();
  renderScore();
  updateToolState();
  setStatus('直前の修正を元に戻しました。');
}

function updateTransposeLabel() {
  const total = 2 + state.userTranspose;
  $('transposeValue').textContent = `B♭管 ${total >= 0 ? '+' : ''}${total}`;
}

function changeTranspose(amount) {
  if (!state.notes.length) return;
  snapshot();
  state.userTranspose = Math.max(-12, Math.min(12, state.userTranspose + amount));
  updateTransposeLabel();
  renderScore();
  updateToolState();
}

function suggestComfortableKey() {
  if (!state.notes.length) return;
  const currentWritten = state.notes.map((note) => writtenTrumpetPitch(note.pitch, state.userTranspose));
  const candidates = [];
  for (let shift = -6; shift <= 6; shift += 1) {
    const pitches = currentWritten.map((pitch) => pitch + shift);
    const sorted = [...pitches].sort((left, right) => left - right);
    const median = sorted[Math.floor(sorted.length / 2)];
    const rangePenalty = pitches.reduce((sum, pitch) => sum + Math.max(0, 55 - pitch) * 8 + Math.max(0, pitch - 82) * 10, 0);
    candidates.push({ shift, cost: rangePenalty + Math.abs(median - 68) });
  }
  candidates.sort((left, right) => left.cost - right.cost || Math.abs(left.shift) - Math.abs(right.shift));
  const shift = candidates[0].shift;
  if (!shift) {
    setStatus('現在の音域はB♭トランペットで演奏しやすい範囲です。');
    return;
  }
  if (state.userTranspose && !confirm(`現在の手動移調を変更し、さらに${shift > 0 ? '+' : ''}${shift}半音移調しますか？`)) return;
  snapshot();
  state.userTranspose = Math.max(-12, Math.min(12, state.userTranspose + shift));
  updateTransposeLabel();
  renderScore();
  updateToolState();
  setStatus(`音域を基準に${shift > 0 ? '+' : ''}${shift}半音の移調を適用しました。`);
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function variableLength(value) {
  let buffer = value & 0x7f;
  const bytes = [];
  while ((value >>= 7)) buffer = (buffer << 8) | ((value & 0x7f) | 0x80);
  while (true) {
    bytes.push(buffer & 0xff);
    if (buffer & 0x80) buffer >>= 8;
    else break;
  }
  return bytes;
}

function midiChunk(type, bytes) {
  const length = bytes.length;
  return [...type].map((character) => character.charCodeAt(0)).concat([
    (length >>> 24) & 255, (length >>> 16) & 255, (length >>> 8) & 255, length & 255,
  ], bytes);
}

function exportMidi() {
  if (isFreeMode || !state.notes.length) return;
  const ticks = 480;
  const events = [];
  for (const note of state.notes) {
    const start = Math.round(note.startQuarter * ticks);
    const end = Math.max(start + 1, Math.round((note.startQuarter + note.durationQuarter) * ticks));
    const pitch = writtenTrumpetPitch(note.pitch, state.userTranspose);
    events.push({ tick: start, priority: 1, bytes: [0x90, pitch, 88] });
    events.push({ tick: end, priority: 0, bytes: [0x80, pitch, 0] });
  }
  events.sort((left, right) => left.tick - right.tick || left.priority - right.priority);
  const track = [0, 0xff, 0x03, 7, ...new TextEncoder().encode('Trumpet'), 0, 0xc0, 56];
  let previous = 0;
  for (const event of events) {
    track.push(...variableLength(event.tick - previous), ...event.bytes);
    previous = event.tick;
  }
  track.push(0, 0xff, 0x2f, 0);
  const header = midiChunk('MThd', [0, 0, 0, 1, (ticks >>> 8) & 255, ticks & 255]);
  download(new Blob([new Uint8Array([...header, ...midiChunk('MTrk', track)])], { type: 'audio/midi' }), 'trumpet-transpose-lab-v2.mid');
  setStatus('修正済みB♭トランペット譜をMIDIで保存しました。');
}

function xmlPitch(midi, preferFlats) {
  const spellings = preferFlats
    ? [['C', 0], ['D', -1], ['D', 0], ['E', -1], ['E', 0], ['F', 0], ['G', -1], ['G', 0], ['A', -1], ['A', 0], ['B', -1], ['B', 0]]
    : [['C', 0], ['C', 1], ['D', 0], ['D', 1], ['E', 0], ['F', 0], ['F', 1], ['G', 0], ['G', 1], ['A', 0], ['A', 1], ['B', 0]];
  const [step, alter] = spellings[positiveModulo(midi, 12)];
  return { step, alter, octave: Math.floor(midi / 12) - 1 };
}

function exportMusicXml() {
  if (isFreeMode || !state.notes.length) return;
  const fifths = inferredKeySignature();
  const preferFlats = fifths < 0;
  const divisions = 480;
  const recordingSettings = settings();
  const notes = state.notes.map((note) => {
    const pitch = xmlPitch(writtenTrumpetPitch(note.pitch, state.userTranspose), preferFlats);
    const alter = pitch.alter ? `<alter>${pitch.alter}</alter>` : '';
    return `<note><pitch><step>${pitch.step}</step>${alter}<octave>${pitch.octave}</octave></pitch><duration>${Math.round(note.durationQuarter * divisions)}</duration><voice>1</voice></note>`;
  }).join('');
  const xml = `<?xml version="1.0" encoding="UTF-8"?><score-partwise version="4.0"><work><work-title>Trumpet Transpose Lab V2</work-title></work><part-list><score-part id="P1"><part-name>B-flat Trumpet</part-name></score-part></part-list><part id="P1"><measure number="1"><attributes><divisions>${divisions}</divisions><key><fifths>${fifths}</fifths></key><time><beats>${recordingSettings.numerator}</beats><beat-type>${recordingSettings.denominator}</beat-type></time><clef><sign>G</sign><line>2</line></clef><transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose></attributes>${notes}</measure></part></score-partwise>`;
  download(new Blob([xml], { type: 'application/vnd.recordare.musicxml+xml;charset=utf-8' }), 'trumpet-transpose-lab-v2.musicxml');
  setStatus('修正済みB♭トランペット譜をMusicXMLで保存しました。');
}

$('microphoneButton').addEventListener('click', () => state.stream ? disableMicrophone() : enableMicrophone());
$('recordButton').addEventListener('click', () => state.recording ? stopRecording() : startRecording());
$('audioFile').addEventListener('change', async (event) => {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) return;
  if (state.notes.length && !confirm('現在の譜面を閉じて、選択した録音を解析しますか？')) return;
  if (file.size > 120 * 1024 * 1024) {
    setStatus('音声ファイルが大きすぎます。10分以内の録音を選択してください。');
    return;
  }
  $('captureState').textContent = '音声読込中';
  await processAudio(file);
});
$('tapTempo').addEventListener('click', () => {
  const now = performance.now();
  if (state.tapTimes.length && now - state.tapTimes.at(-1) > 2200) state.tapTimes = [];
  state.tapTimes.push(now);
  state.tapTimes = state.tapTimes.slice(-8);
  if (state.tapTimes.length < 2) return;
  const intervals = state.tapTimes.slice(1).map((time, index) => time - state.tapTimes[index]);
  intervals.sort((left, right) => left - right);
  $('tempo').value = Math.max(30, Math.min(300, Math.round(60000 / intervals[Math.floor(intervals.length / 2)])));
  $('tapTempo').textContent = `♩=${$('tempo').value}`;
});
$('scoreSvg').addEventListener('click', (event) => {
  const group = event.target.closest('[data-note-id]');
  if (!group) return;
  state.selectedId = group.dataset.noteId;
  renderScore();
  updateToolState();
  location.hash = 'edit';
});
$('scoreSvg').addEventListener('keydown', (event) => {
  if (!['Enter', ' '].includes(event.key)) return;
  const group = event.target.closest('[data-note-id]');
  if (group) group.dispatchEvent(new MouseEvent('click', { bubbles: true }));
});
document.querySelectorAll('[data-edit]').forEach((button) => button.addEventListener('click', () => editSelected(button.dataset.edit)));
$('noteDuration').addEventListener('change', () => {
  const note = selectedNote();
  if (!note) return;
  snapshot();
  note.durationQuarter = Number($('noteDuration').value);
  renderScore();
  setStatus('選択音符の音価を変更しました。');
});
$('undoButton').addEventListener('click', undo);
$('transposeDown').addEventListener('click', () => changeTranspose(-1));
$('transposeUp').addEventListener('click', () => changeTranspose(1));
$('comfortableKeyButton').addEventListener('click', suggestComfortableKey);
$('fitScoreButton').addEventListener('click', () => {
  state.fitScore = !state.fitScore;
  renderScore();
});
$('keySignature').addEventListener('change', renderScore);
$('playbackButton').addEventListener('click', async () => {
  const audio = $('audioPlayback');
  if (audio.paused) await audio.play();
  else audio.pause();
});
$('audioPlayback').addEventListener('play', () => { $('playbackButton').textContent = 'Ⅱ'; });
$('audioPlayback').addEventListener('pause', () => { $('playbackButton').textContent = '▶'; });
$('audioPlayback').addEventListener('loadedmetadata', () => {
  $('playbackProgress').max = String($('audioPlayback').duration || 1);
});
$('audioPlayback').addEventListener('timeupdate', () => {
  const audio = $('audioPlayback');
  $('playbackProgress').value = String(audio.currentTime);
  $('playbackTime').textContent = `${formatTime(audio.currentTime)} / ${formatTime(audio.duration)}`;
});
$('playbackProgress').addEventListener('input', () => { $('audioPlayback').currentTime = Number($('playbackProgress').value); });
$('playbackRate').addEventListener('change', () => { $('audioPlayback').playbackRate = Number($('playbackRate').value); });
$('wavExport').addEventListener('click', () => {
  if (!isFreeMode && state.audioBlob) download(state.audioBlob, 'trumpet-transpose-lab-recording.wav');
  else if (!state.audioBlob) setStatus('保存できる録音がありません。');
});
$('midiExport').addEventListener('click', exportMidi);
$('xmlExport').addEventListener('click', exportMusicXml);
window.addEventListener('beforeunload', (event) => {
  if (!state.notes.length) return;
  event.preventDefault();
});

initializeMode();
updateTransposeLabel();
renderScore();
updateToolState();
