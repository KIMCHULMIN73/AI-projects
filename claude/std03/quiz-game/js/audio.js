// [모듈3] 사운드 — BGM 로테이션 + 정답/오답 효과음.
//
// PRD §8은 assets/bgm, assets/sfx 에 파일이 미리 배치돼 있다고 전제한다.
// 파일이 있으면 그대로 재생하고, 없으면 Web Audio API로 합성해 대체한다
// (에셋을 나중에 넣으면 코드 수정 없이 파일 재생으로 바뀐다).
//
// 브라우저 자동재생 정책 때문에 첫 재생은 반드시 사용자 상호작용(시작 버튼) 이후여야 한다.

const BGM_FILES = [
  'assets/bgm/travel1.mp3',
  'assets/bgm/travel2.mp3',
  'assets/bgm/travel3.mp3',
  'assets/bgm/travel4.mp3',
];
const SFX_FILES = {
  correct: 'assets/sfx/correct.mp3',
  wrong: 'assets/sfx/wrong.mp3',
};

const BGM_VOLUME = 0.28;
const SFX_VOLUME = 0.6;

/* ── 에셋 존재 여부 조사 (한 번만) ─────────────────────────── */
const probeCache = new Map();

function probeFile(url) {
  if (probeCache.has(url)) return probeCache.get(url);
  const probe = fetch(url, { method: 'HEAD' })
    .then((response) => response.ok)
    .catch(() => false);
  probeCache.set(url, probe);
  return probe;
}

/* ── Web Audio 폴백 ────────────────────────────────────────── */
let context = null;
let masterGain = null;
let muted = false;

function ensureContext() {
  if (context) return context;
  const Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) return null;
  try {
    context = new Ctor();
    masterGain = context.createGain();
    masterGain.gain.value = muted ? 0 : 1;
    masterGain.connect(context.destination);
  } catch (error) {
    console.warn('[audio] AudioContext를 만들지 못했습니다. 소리 없이 진행합니다.', error);
    context = null;
  }
  return context;
}

/** 한 음을 예약 재생한다. */
function scheduleTone(startAt, { freq, duration, type = 'triangle', gain = 0.08 }) {
  if (!context) return;
  const oscillator = context.createOscillator();
  const envelope = context.createGain();
  oscillator.type = type;
  oscillator.frequency.setValueAtTime(freq, startAt);
  envelope.gain.setValueAtTime(0.0001, startAt);
  envelope.gain.exponentialRampToValueAtTime(gain, startAt + 0.015);
  envelope.gain.exponentialRampToValueAtTime(0.0001, startAt + duration);
  oscillator.connect(envelope).connect(masterGain);
  oscillator.start(startAt);
  oscillator.stop(startAt + duration + 0.05);
}

/* 합성 BGM: 세계여행 느낌의 경쾌한 장5음계 아르페지오.
   트랙마다 조성과 패턴이 달라서 nextBgm() 하면 분위기가 바뀐다. */
const SYNTH_TRACKS = [
  { root: 261.63, steps: [0, 4, 7, 9, 7, 4, 2, 0], tempo: 132 }, // C
  { root: 349.23, steps: [0, 2, 4, 7, 9, 7, 4, 2], tempo: 138 }, // F
  { root: 392.0, steps: [0, 7, 4, 9, 12, 9, 7, 4], tempo: 126 }, // G
  { root: 293.66, steps: [0, 4, 9, 7, 4, 9, 7, 2], tempo: 144 }, // D
];

const LOOKAHEAD_MS = 30;
const SCHEDULE_AHEAD = 0.25;

let synthTimer = null;
let synthStep = 0;
let synthNextTime = 0;

function semitone(root, offset) {
  return root * Math.pow(2, offset / 12);
}

function scheduleSynthLoop(trackIndex) {
  if (!context) return;
  const track = SYNTH_TRACKS[trackIndex % SYNTH_TRACKS.length];
  const stepDuration = 60 / track.tempo / 2; // 8분음표

  while (synthNextTime < context.currentTime + SCHEDULE_AHEAD) {
    const step = synthStep % track.steps.length;
    scheduleTone(synthNextTime, {
      freq: semitone(track.root, track.steps[step]),
      duration: stepDuration * 0.9,
      type: 'triangle',
      gain: 0.05,
    });
    if (step % 4 === 0) {
      scheduleTone(synthNextTime, {
        freq: semitone(track.root / 2, step === 0 ? 0 : 7),
        duration: stepDuration * 1.8,
        type: 'sine',
        gain: 0.07,
      });
    }
    synthNextTime += stepDuration;
    synthStep += 1;
  }
}

function startSynthBgm(trackIndex) {
  if (!ensureContext()) return;
  stopSynthBgm();
  synthStep = 0;
  synthNextTime = context.currentTime + 0.08;
  synthTimer = setInterval(() => scheduleSynthLoop(trackIndex), LOOKAHEAD_MS);
}

function stopSynthBgm() {
  if (synthTimer !== null) {
    clearInterval(synthTimer);
    synthTimer = null;
  }
}

function playSynthCorrect() {
  if (!ensureContext()) return;
  const now = context.currentTime;
  [0, 4, 7, 12].forEach((offset, index) => {
    scheduleTone(now + index * 0.075, {
      freq: semitone(523.25, offset),
      duration: 0.22,
      type: 'triangle',
      gain: 0.16,
    });
  });
}

function playSynthWrong() {
  if (!ensureContext()) return;
  const now = context.currentTime;
  scheduleTone(now, { freq: 207.65, duration: 0.28, type: 'sawtooth', gain: 0.12 });
  scheduleTone(now + 0.14, { freq: 155.56, duration: 0.42, type: 'sawtooth', gain: 0.12 });
}

/* ── 파일 재생 경로 ────────────────────────────────────────── */
let bgmElement = null;
let bgmIndex = 0;
/** null = 아직 조사 전, 'file' = mp3 사용, 'synth' = 합성음 사용 */
let bgmMode = null;
let bgmRunning = false;

const sfxElements = {};
const sfxMode = {}; // 'file' | 'synth'

function makeAudioElement(src, { loop = false, volume = 1 } = {}) {
  const node = document.createElement('audio');
  node.src = src;
  node.loop = loop;
  node.volume = muted ? 0 : volume;
  node.preload = 'auto';
  return node;
}

async function resolveBgmMode() {
  if (bgmMode) return bgmMode;
  const results = await Promise.all(BGM_FILES.map(probeFile));
  bgmMode = results.some(Boolean) ? 'file' : 'synth';
  return bgmMode;
}

function playBgmFile(index) {
  const src = BGM_FILES[index % BGM_FILES.length];
  if (!bgmElement) {
    bgmElement = makeAudioElement(src, { loop: true, volume: BGM_VOLUME });
  } else {
    bgmElement.src = src;
  }
  bgmElement.volume = muted ? 0 : BGM_VOLUME;
  // 자동재생이 막히면 조용히 넘어간다(게임 진행에는 영향 없음).
  bgmElement.play().catch(() => {});
}

async function resolveSfxMode(kind) {
  if (sfxMode[kind]) return sfxMode[kind];
  const exists = await probeFile(SFX_FILES[kind]);
  sfxMode[kind] = exists ? 'file' : 'synth';
  if (exists) {
    sfxElements[kind] = makeAudioElement(SFX_FILES[kind], { volume: SFX_VOLUME });
  }
  return sfxMode[kind];
}

function playSfx(kind, synthFallback) {
  if (muted) return;
  const known = sfxMode[kind];
  if (known === 'file') {
    const node = sfxElements[kind];
    if (node) {
      node.currentTime = 0;
      node.play().catch(() => {});
      return;
    }
  }
  if (known === 'synth') {
    synthFallback();
    return;
  }
  // 아직 조사 전이면 합성음으로 즉시 반응하고, 조사 결과는 다음 호출부터 반영한다.
  synthFallback();
  resolveSfxMode(kind);
}

/* ── 공개 API ─────────────────────────────────────────────── */
export const Audio = {
  /** 시작 버튼 클릭 시점에 호출한다(자동재생 정책 대응). */
  async startBgm() {
    bgmRunning = true;
    bgmIndex = 0;
    ensureContext();
    if (context && context.state === 'suspended') {
      context.resume().catch(() => {});
    }
    // 효과음 경로도 미리 조사해 첫 정답에서 파일이 있으면 바로 쓰이게 한다.
    resolveSfxMode('correct');
    resolveSfxMode('wrong');

    const mode = await resolveBgmMode();
    if (!bgmRunning) return; // 조사 중에 stopBgm이 불렸다면 재생하지 않는다
    if (mode === 'file') playBgmFile(bgmIndex);
    else startSynthBgm(bgmIndex);
  },

  /** 다음 트랙으로 순환. 문제/분야 전환 때 호출한다. */
  async nextBgm() {
    if (!bgmRunning) return;
    bgmIndex = (bgmIndex + 1) % BGM_FILES.length;
    const mode = await resolveBgmMode();
    if (!bgmRunning) return;
    if (mode === 'file') playBgmFile(bgmIndex);
    else startSynthBgm(bgmIndex);
  },

  stopBgm() {
    bgmRunning = false;
    stopSynthBgm();
    if (bgmElement) {
      bgmElement.pause();
      bgmElement.currentTime = 0;
    }
  },

  playCorrect() {
    playSfx('correct', playSynthCorrect);
  },

  playWrong() {
    playSfx('wrong', playSynthWrong);
  },

  /** 음소거 토글. main.js에서 M 키에 연결돼 있다. */
  setMuted(value) {
    muted = Boolean(value);
    if (masterGain) masterGain.gain.value = muted ? 0 : 1;
    if (bgmElement) bgmElement.volume = muted ? 0 : BGM_VOLUME;
    Object.values(sfxElements).forEach((node) => {
      node.volume = muted ? 0 : SFX_VOLUME;
    });
    return muted;
  },

  isMuted() {
    return muted;
  },
};
