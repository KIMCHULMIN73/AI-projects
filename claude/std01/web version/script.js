/*
 * Handwritten digit recognizer, running entirely in the browser.
 * Mirrors ../desktop version/digit_recognizer_gui.py: same canvas/brush
 * behavior, same preprocess() steps, same MLP forward pass (weights
 * exported from digit_model.joblib into weights.json).
 */

const CANVAS_SIZE = 280;
const BRUSH_RADIUS = 10; // matches desktop BRUSH_RADIUS

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d", { willReadFrequently: true });

const resultEl = document.getElementById("result");
const confidenceEl = document.getElementById("confidence");
const recognizeBtn = document.getElementById("recognizeBtn");
const clearBtn = document.getElementById("clearBtn");

let model = null;
let drawing = false;
let lastX = null;
let lastY = null;

function clearCanvas() {
  ctx.fillStyle = "black";
  ctx.fillRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);
  resultEl.textContent = "Result: -";
  confidenceEl.textContent = "Confidence: -";
}

function setupCanvasStyle() {
  ctx.strokeStyle = "white";
  ctx.lineWidth = BRUSH_RADIUS * 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
}

function getPos(evt) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const point = evt.touches && evt.touches.length ? evt.touches[0] : evt;
  return {
    x: (point.clientX - rect.left) * scaleX,
    y: (point.clientY - rect.top) * scaleY,
  };
}

function startDraw(evt) {
  evt.preventDefault();
  drawing = true;
  const { x, y } = getPos(evt);
  lastX = x;
  lastY = y;
}

function moveDraw(evt) {
  if (!drawing) return;
  evt.preventDefault();
  const { x, y } = getPos(evt);
  ctx.beginPath();
  ctx.moveTo(lastX, lastY);
  ctx.lineTo(x, y);
  ctx.stroke();
  lastX = x;
  lastY = y;
}

function endDraw() {
  drawing = false;
  lastX = null;
  lastY = null;
}

canvas.addEventListener("mousedown", startDraw);
canvas.addEventListener("mousemove", moveDraw);
window.addEventListener("mouseup", endDraw);

canvas.addEventListener("touchstart", startDraw, { passive: false });
canvas.addEventListener("touchmove", moveDraw, { passive: false });
canvas.addEventListener("touchend", endDraw);

clearBtn.addEventListener("click", clearCanvas);
recognizeBtn.addEventListener("click", () => {
  if (!model) {
    alert("Model is still loading, please try again in a moment.");
    return;
  }
  const vector = preprocess();
  if (vector === null) {
    alert("Please draw a digit first.");
    return;
  }
  const { predicted, confidence } = predict(vector);
  resultEl.textContent = `Result: ${predicted}`;
  confidenceEl.textContent = `Confidence: ${confidence.toFixed(1)}%`;
});

/**
 * Convert the drawn image into a 28x28 MNIST-style normalized vector.
 * Mirrors digit_recognizer_gui.py's preprocess(): crop to bounding box,
 * pad to a square, add a ~20% margin, then resize to 28x28.
 */
function preprocess() {
  const { data } = ctx.getImageData(0, 0, CANVAS_SIZE, CANVAS_SIZE);
  let minX = CANVAS_SIZE;
  let minY = CANVAS_SIZE;
  let maxX = -1;
  let maxY = -1;

  for (let y = 0; y < CANVAS_SIZE; y++) {
    for (let x = 0; x < CANVAS_SIZE; x++) {
      const idx = (y * CANVAS_SIZE + x) * 4;
      if (data[idx] > 0) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }

  if (maxX < 0) return null; // nothing drawn

  const w = maxX - minX + 1;
  const h = maxY - minY + 1;
  const side = Math.max(w, h);
  const margin = Math.floor(side * 0.2);
  const padded = side + margin * 2;

  const squareCanvas = document.createElement("canvas");
  squareCanvas.width = padded;
  squareCanvas.height = padded;
  const squareCtx = squareCanvas.getContext("2d");
  squareCtx.fillStyle = "black";
  squareCtx.fillRect(0, 0, padded, padded);
  const destX = margin + Math.floor((side - w) / 2);
  const destY = margin + Math.floor((side - h) / 2);
  squareCtx.drawImage(canvas, minX, minY, w, h, destX, destY, w, h);

  const small = document.createElement("canvas");
  small.width = 28;
  small.height = 28;
  const smallCtx = small.getContext("2d");
  smallCtx.imageSmoothingEnabled = true;
  smallCtx.imageSmoothingQuality = "high";
  smallCtx.drawImage(squareCanvas, 0, 0, padded, padded, 0, 0, 28, 28);

  const smallData = smallCtx.getImageData(0, 0, 28, 28).data;
  const vector = new Float64Array(28 * 28);
  for (let i = 0; i < 28 * 28; i++) {
    vector[i] = smallData[i * 4] / 255; // red channel == grayscale value
  }
  return vector;
}

async function loadModel() {
  try {
    const res = await fetch("weights.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    model = await res.json();
  } catch (err) {
    // Most common cause: the page was opened directly as a file:// URL,
    // where browsers block fetch() from reading sibling files.
    console.error("Failed to load weights.json:", err);
    resultEl.textContent = "Model failed to load";
    confidenceEl.textContent = "Serve this folder over http://, don't open the file directly";
  }
}

function applyActivation(vec, name) {
  switch (name) {
    case "relu":
      return vec.map((v) => (v < 0 ? 0 : v));
    case "logistic":
      return vec.map((v) => 1 / (1 + Math.exp(-v)));
    case "tanh":
      return vec.map((v) => Math.tanh(v));
    case "identity":
      return vec;
    default:
      throw new Error(`Unsupported activation: ${name}`);
  }
}

function softmax(vec) {
  const max = Math.max(...vec);
  const exps = vec.map((v) => Math.exp(v - max));
  const sum = exps.reduce((a, b) => a + b, 0);
  return exps.map((v) => v / sum);
}

function linearLayer(input, layer) {
  const { weights, biases } = layer; // weights: [inDim][outDim]
  const outDim = biases.length;
  const inDim = weights.length;
  const out = new Float64Array(outDim);
  for (let i = 0; i < inDim; i++) {
    const xi = input[i];
    if (xi === 0) continue;
    const row = weights[i];
    for (let j = 0; j < outDim; j++) {
      out[j] += xi * row[j];
    }
  }
  for (let j = 0; j < outDim; j++) out[j] += biases[j];
  return Array.from(out);
}

/** Replicates sklearn.neural_network.MLPClassifier's forward pass. */
function predict(vector) {
  let activations = Array.from(vector);
  const lastLayerIdx = model.layers.length - 1;

  for (let l = 0; l < model.layers.length; l++) {
    activations = linearLayer(activations, model.layers[l]);
    if (l !== lastLayerIdx) {
      activations = applyActivation(activations, model.hidden_activation);
    }
  }

  // softmax needs the full logit vector (not a pointwise activation), so it's
  // handled separately from applyActivation.
  const probs =
    model.out_activation === "softmax" ? softmax(activations) : applyActivation(activations, model.out_activation);

  let bestIdx = 0;
  for (let i = 1; i < probs.length; i++) {
    if (probs[i] > probs[bestIdx]) bestIdx = i;
  }

  return {
    predicted: model.classes[bestIdx],
    confidence: probs[bestIdx] * 100,
  };
}

clearCanvas();
setupCanvasStyle();
loadModel();
