export const DEFAULT_REFERENCE_HZ = 442;

export function frequencyToMidi(frequency, referenceHz = DEFAULT_REFERENCE_HZ) {
  return 69 + 12 * Math.log2(frequency / referenceHz);
}

export function midiToFrequency(midi, referenceHz = DEFAULT_REFERENCE_HZ) {
  return referenceHz * 2 ** ((midi - 69) / 12);
}

export function detectPitchYin(samples, sampleRate, options = {}) {
  const minimumFrequency = options.minimumFrequency ?? 100;
  const maximumFrequency = options.maximumFrequency ?? 1600;
  const threshold = options.threshold ?? 0.17;
  const minimumRms = options.minimumRms ?? 0.004;
  const length = samples.length;
  let mean = 0;
  let energy = 0;

  for (let index = 0; index < length; index += 1) {
    mean += samples[index];
    energy += samples[index] * samples[index];
  }

  const rms = Math.sqrt(energy / Math.max(1, length));
  if (rms < minimumRms) return null;
  mean /= Math.max(1, length);

  const minimumLag = Math.max(2, Math.floor(sampleRate / maximumFrequency));
  const maximumLag = Math.min(Math.floor(length / 2), Math.ceil(sampleRate / minimumFrequency));
  const difference = new Float64Array(maximumLag + 1);
  const normalized = new Float64Array(maximumLag + 1);

  for (let lag = 1; lag <= maximumLag; lag += 1) {
    let sum = 0;
    for (let index = 0; index < length - lag; index += 1) {
      const delta = (samples[index] - mean) - (samples[index + lag] - mean);
      sum += delta * delta;
    }
    difference[lag] = sum;
  }

  normalized[0] = 1;
  let cumulative = 0;
  for (let lag = 1; lag <= maximumLag; lag += 1) {
    cumulative += difference[lag];
    normalized[lag] = cumulative ? (difference[lag] * lag) / cumulative : 1;
  }

  let bestLag = -1;
  for (let lag = minimumLag + 1; lag < maximumLag; lag += 1) {
    if (
      normalized[lag] < threshold
      && normalized[lag] <= normalized[lag - 1]
      && normalized[lag] < normalized[lag + 1]
    ) {
      bestLag = lag;
      break;
    }
  }

  if (bestLag < 0) {
    bestLag = minimumLag;
    for (let lag = minimumLag + 1; lag <= maximumLag; lag += 1) {
      if (normalized[lag] < normalized[bestLag]) bestLag = lag;
    }
  }

  const confidence = 1 - normalized[bestLag];
  if (confidence < (options.minimumConfidence ?? 0.72)) return null;

  const left = normalized[Math.max(minimumLag, bestLag - 1)];
  const center = normalized[bestLag];
  const right = normalized[Math.min(maximumLag, bestLag + 1)];
  const denominator = left - 2 * center + right;
  const refinedLag = denominator
    ? bestLag + (0.5 * (left - right)) / denominator
    : bestLag;
  const frequency = sampleRate / refinedLag;

  if (frequency < minimumFrequency || frequency > maximumFrequency) return null;
  return { frequency, confidence, rms };
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)];
}

function frameRms(samples, start, size) {
  let energy = 0;
  for (let index = 0; index < size; index += 1) {
    const sample = samples[start + index] ?? 0;
    energy += sample * sample;
  }
  return Math.sqrt(energy / size);
}

export function transcribeMonophonic(samples, sampleRate, options = {}) {
  const windowSize = options.windowSize ?? 4096;
  const hopSize = options.hopSize ?? 512;
  const referenceHz = options.referenceHz ?? DEFAULT_REFERENCE_HZ;
  const minimumNoteMs = options.minimumNoteMs ?? 70;
  const articulationRatio = options.articulationRatio ?? 1.65;
  const articulationRise = options.articulationRise ?? 0.01;
  const frames = [];

  if (samples.length < windowSize) return [];

  for (let start = 0; start + windowSize <= samples.length; start += hopSize) {
    const frame = samples.subarray(start, start + windowSize);
    const analysis = detectPitchYin(frame, sampleRate, options);
    frames.push({
      midiFloat: analysis ? frequencyToMidi(analysis.frequency, referenceHz) : null,
      confidence: analysis?.confidence ?? 0,
      rms: analysis?.rms ?? frameRms(samples, start, windowSize),
    });
  }

  const pitches = frames.map((frame, index) => {
    if (!Number.isFinite(frame.midiFloat)) return null;
    const neighborhood = frames
      .slice(Math.max(0, index - 2), index + 3)
      .map((candidate) => candidate.midiFloat)
      .filter(Number.isFinite);
    const center = median(neighborhood);
    return Number.isFinite(center) ? Math.round(center) : null;
  });

  for (let index = 1; index < pitches.length - 1; index += 1) {
    if (pitches[index] === null && pitches[index - 1] === pitches[index + 1]) {
      pitches[index] = pitches[index - 1];
    }
  }

  const notes = [];
  let runStart = 0;
  const millisecondsPerFrame = (hopSize / sampleRate) * 1000;
  const minimumGapFrames = Math.max(3, Math.round(80 / millisecondsPerFrame));

  function appendRun(endFrame) {
    const pitch = pitches[runStart];
    if (!Number.isFinite(pitch)) return;
    const boundaries = [runStart];
    for (let index = runStart + minimumGapFrames; index < endFrame - 1; index += 1) {
      const previousRms = Math.max(frames[index - 2]?.rms ?? 0, frames[index - 1]?.rms ?? 0, 0.001);
      const currentRms = frames[index].rms;
      const farEnough = index - boundaries.at(-1) >= minimumGapFrames;
      if (farEnough && currentRms > previousRms * articulationRatio && currentRms - previousRms > articulationRise) {
        boundaries.push(index);
      }
    }
    boundaries.push(endFrame);

    for (let index = 0; index < boundaries.length - 1; index += 1) {
      const startFrame = boundaries[index];
      const stopFrame = boundaries[index + 1];
      const startMs = startFrame * millisecondsPerFrame;
      const endMs = Math.min(
        (samples.length / sampleRate) * 1000,
        ((stopFrame - 1) * hopSize + windowSize) / sampleRate * 1000,
      );
      if (endMs - startMs < minimumNoteMs) continue;
      const confidenceFrames = frames.slice(startFrame, stopFrame);
      notes.push({
        pitch,
        startMs,
        endMs,
        confidence: confidenceFrames.reduce((sum, frame) => sum + frame.confidence, 0)
          / Math.max(1, confidenceFrames.length),
        articulated: index > 0,
      });
    }
  }

  for (let index = 1; index <= pitches.length; index += 1) {
    if (index < pitches.length && pitches[index] === pitches[runStart]) continue;
    appendRun(index);
    runStart = index;
  }

  return notes;
}

export function quantizeNotes(notes, settings) {
  const tempo = Math.max(30, Math.min(300, Number(settings.tempo) || 120));
  const division = [4, 8, 16, 32].includes(Number(settings.division))
    ? Number(settings.division)
    : 8;
  const quarterMs = 60000 / tempo;
  const gridQuarter = 4 / division;

  return notes.map((note, index) => {
    const startQuarter = Math.max(0, Math.round((note.startMs / quarterMs) / gridQuarter) * gridQuarter);
    const rawDuration = Math.max(note.endMs - note.startMs, quarterMs * gridQuarter);
    const durationQuarter = Math.max(
      gridQuarter,
      Math.round((rawDuration / quarterMs) / gridQuarter) * gridQuarter,
    );
    return {
      id: note.id ?? `note-${index + 1}`,
      pitch: note.pitch,
      startQuarter,
      durationQuarter,
      confidence: note.confidence ?? 1,
      articulated: Boolean(note.articulated),
    };
  });
}

export function writtenTrumpetPitch(concertPitch, userTranspose = 0) {
  return Math.max(0, Math.min(127, Math.round(concertPitch + 2 + userTranspose)));
}

export function positiveModulo(value, divisor) {
  return ((value % divisor) + divisor) % divisor;
}
