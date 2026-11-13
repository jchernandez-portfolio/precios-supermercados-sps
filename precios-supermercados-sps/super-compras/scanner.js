// Escáner de códigos de barras de Súper Compras. Se carga sólo al tocar "Escanear":
// pide la cámara trasera en ese momento y la apaga al cerrar, al navegar o al dejar
// la pestaña. Usa BarcodeDetector del navegador (Chrome/Android) y, donde no existe
// (iPhone/Safari, Firefox), el lector EAN/UPC de vendor/ servido desde este mismo
// sitio (sin red, sin eval, sin WebAssembly). Siempre se puede escribir el código.
import { BARCODE_FORMATS, barcodeCandidates } from "./data.js?v=20261113-4";

const LIBRARY = "./vendor/zxing-upcean.js?v=20261113-4";
// Recorte del cuadro que se analiza (el lector propio corre en el hilo principal).
const MAX_FRAME_WIDTH = 800;

export function cameraErrorMessage(error) {
  const name = error?.name || "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
    return "No hay permiso para usar la cámara. Puedes darlo en los ajustes del navegador o escribir el código abajo.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
    return "No encontramos una cámara en este dispositivo. Escribe el código abajo.";
  }
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
    return "Otra app está usando la cámara. Ciérrala e intenta de nuevo, o escribe el código abajo.";
  }
  if (name === "Unsupported") return "Este navegador no deja usar la cámara aquí. Escribe el código abajo.";
  if (name === "ReaderUnavailable") return "No pudimos preparar el lector. Revisa tu conexión o escribe el código abajo.";
  return "No pudimos abrir la cámara. Escribe el código abajo.";
}

// RGBA del canvas → luminancia (un byte por píxel).
export function lumaFromRgba(data, width, height) {
  const luma = new Uint8ClampedArray(width * height);
  for (let pixel = 0, offset = 0; pixel < luma.length; pixel += 1, offset += 4) {
    luma[pixel] = (data[offset] * 77 + data[offset + 1] * 150 + data[offset + 2] * 29) >> 8;
  }
  return luma;
}

// Gira 90°: el lector 1D recorre filas y el código puede estar de pie.
export function transposeLuma(luma, width, height) {
  const turned = new Uint8ClampedArray(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) turned[x * height + (height - 1 - y)] = luma[y * width + x];
  }
  return turned;
}

// Promedio de 5 filas: quita el grano de la cámara sin borrar las barras (que son
// verticales en el cuadro que se lee).
export function smoothRows(luma, width, height) {
  const smooth = new Uint8ClampedArray(width * height);
  for (let y = 0; y < height; y += 1) {
    const top = Math.max(0, y - 2);
    const bottom = Math.min(height - 1, y + 2);
    const count = bottom - top + 1;
    for (let x = 0; x < width; x += 1) {
      let sum = 0;
      for (let row = top; row <= bottom; row += 1) sum += luma[row * width + x];
      smooth[y * width + x] = sum / count;
    }
  }
  return smooth;
}

export function zxingReader(lib) {
  const formats = { EAN_13: "ean_13", EAN_8: "ean_8", UPC_A: "upc_a", UPC_E: "upc_e" };
  const names = new Map(Object.entries(formats).map(([key, name]) => [lib.BarcodeFormat[key], name]));
  const hints = new Map([
    [lib.DecodeHintType.POSSIBLE_FORMATS, Object.keys(formats).map((key) => lib.BarcodeFormat[key])],
    [lib.DecodeHintType.TRY_HARDER, true],
  ]);
  const reader = new lib.MultiFormatUPCEANReader(hints);
  const attempt = (luma, width, height) => {
    try {
      const source = new lib.RGBLuminanceSource(luma, width, height);
      const result = reader.decode(new lib.BinaryBitmap(new lib.GlobalHistogramBinarizer(source)), hints);
      return { text: result.getText(), format: names.get(result.getBarcodeFormat()) || "" };
    } catch {
      return null;
    } finally {
      reader.reset();
    }
  };
  return {
    decode(luma, width, height) {
      return attempt(smoothRows(luma, width, height), width, height)
        || attempt(smoothRows(transposeLuma(luma, width, height), height, width), height, width);
    },
  };
}

// BarcodeDetector nativo si lee estos formatos; si no, el lector propio.
export async function createDetector({ Native = globalThis.BarcodeDetector, importLibrary = () => import(LIBRARY) } = {}) {
  if (typeof Native === "function") {
    try {
      const supported = typeof Native.getSupportedFormats === "function" ? await Native.getSupportedFormats() : BARCODE_FORMATS;
      const formats = BARCODE_FORMATS.filter((format) => supported.includes(format));
      if (formats.length) {
        const detector = new Native({ formats });
        return {
          kind: "native", interval: 120, confirmations: 1,
          async detect(source) {
            const found = await detector.detect(source);
            const hit = (found || []).find((item) => item?.rawValue && formats.includes(item.format));
            return hit ? { text: String(hit.rawValue), format: hit.format } : null;
          },
        };
      }
    } catch { /* sin detector nativo útil: lector propio */ }
  }
  const reader = zxingReader(await importLibrary());
  let canvas = null;
  let context = null;
  return {
    // Una lectura del lector propio se confirma con una segunda igual.
    kind: "zxing", interval: 220, confirmations: 2,
    async detect(video) {
      const width = video.videoWidth;
      const height = video.videoHeight;
      if (!width || !height) return null;
      const cropHeight = Math.min(height, Math.round(width * 0.75));
      const scale = Math.min(1, MAX_FRAME_WIDTH / width);
      const frameWidth = Math.round(width * scale);
      const frameHeight = Math.round(cropHeight * scale);
      if (!canvas) {
        canvas = document.createElement("canvas");
        context = canvas.getContext("2d", { willReadFrequently: true });
      }
      canvas.width = frameWidth;
      canvas.height = frameHeight;
      context.drawImage(video, 0, Math.round((height - cropHeight) / 2), width, cropHeight, 0, 0, frameWidth, frameHeight);
      const { data } = context.getImageData(0, 0, frameWidth, frameHeight);
      return reader.decode(lumaFromRgba(data, frameWidth, frameHeight), frameWidth, frameHeight);
    },
  };
}

let session = null;

// onCode(dígitos, formato) se llama una vez, con la cámara ya apagada.
export function openScanner({ h, onCode, opener = null, createDetectorFn = createDetector }) {
  if (session) return session;
  let closed = false;
  let stream = null;
  let timer = null;
  let detector = null;
  let last = { text: "", count: 0 };

  const title = h("h2", { id: "scanner-title" }, "Escanear código de barras");
  const video = h("video", { class: "scanner-video", playsinline: true, muted: true, autoplay: true, "aria-hidden": "true" });
  video.muted = true;
  video.playsInline = true;
  const status = h("p", { class: "scanner-status", role: "status", "aria-live": "polite" }, "Abriendo la cámara…");
  const input = h("input", {
    id: "scanner-code", type: "text", inputmode: "numeric", pattern: "[0-9 ]*", autocomplete: "off",
    maxlength: "20", enterkeyhint: "search", "aria-describedby": "scanner-help",
  });
  const help = h("p", { id: "scanner-help", class: "scanner-help" }, "Son los 8 a 13 números debajo de las barras.");
  const viewport = h("div", { class: "scanner-viewport" }, video, h("div", { class: "scanner-guide", "aria-hidden": "true" }));
  const dialog = h("dialog", { class: "scanner", "aria-labelledby": "scanner-title", "aria-modal": "true" },
    h("div", { class: "scanner-head" }, title,
      h("button", { type: "button", class: "scanner-close", "aria-label": "Cerrar escáner", onclick: () => close() }, "×")),
    viewport,
    status,
    h("form", {
      class: "scanner-manual", novalidate: true,
      onsubmit: (event) => { event.preventDefault(); submitManual(); },
    },
    h("label", { for: "scanner-code" }, "¿No lo lee? Escribe el código"),
    h("div", { class: "scanner-row" }, input, h("button", { type: "submit", class: "primary" }, "Buscar")),
    help));

  function say(text, { problem = false } = {}) {
    status.textContent = text;
    status.classList.toggle("problem", problem);
  }

  function stopCamera() {
    clearTimeout(timer);
    timer = null;
    for (const track of stream?.getTracks?.() || []) track.stop();
    stream = null;
    video.pause?.();
    video.srcObject = null;
  }

  function close({ restoreFocus = true } = {}) {
    if (closed) return;
    closed = true;
    stopCamera();
    window.removeEventListener("hashchange", onLeave);
    window.removeEventListener("pagehide", onLeave);
    document.removeEventListener("visibilitychange", onHidden);
    document.removeEventListener("keydown", onKey, true);
    if (dialog.open && typeof dialog.close === "function") dialog.close();
    dialog.remove();
    session = null;
    if (restoreFocus && opener?.isConnected) opener.focus();
  }

  function finish(text, format) {
    if (closed) return;
    navigator.vibrate?.(60);
    close({ restoreFocus: false });
    onCode(text, format);
  }

  function submitManual() {
    const digits = input.value.replace(/\D/g, "");
    if (!barcodeCandidates(digits).length) {
      input.setAttribute("aria-invalid", "true");
      help.textContent = digits
        ? "Ese código no cuadra: revisa los números (el último es de control)."
        : "Escribe los números que están debajo de las barras.";
      help.classList.add("problem");
      input.focus();
      return;
    }
    finish(digits, "");
  }

  function onHit(hit) {
    if (!hit?.text || !barcodeCandidates(hit.text, hit.format).length) return;
    last = hit.text === last.text ? { text: hit.text, count: last.count + 1 } : { text: hit.text, count: 1 };
    if (last.count >= detector.confirmations) finish(hit.text.replace(/\D/g, ""), hit.format || "");
  }

  async function tick() {
    if (closed) return;
    if (video.readyState >= 2) {
      try { onHit(await detector.detect(video)); } catch { /* cuadro ilegible: sigue */ }
    }
    if (!closed) timer = setTimeout(tick, detector.interval);
  }

  function cameraFailed(error) {
    stopCamera();
    viewport.hidden = true;
    say(cameraErrorMessage(error), { problem: true });
    input.focus();
  }

  async function start() {
    if (!navigator.mediaDevices?.getUserMedia) { cameraFailed({ name: "Unsupported" }); return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
      });
    } catch (error) {
      if (!closed) cameraFailed(error);
      return;
    }
    if (closed) { stopCamera(); return; }
    video.srcObject = stream;
    try { await video.play(); } catch { /* autoplay lo arranca igual */ }
    try {
      detector = await createDetectorFn();
    } catch {
      if (!closed) cameraFailed({ name: "ReaderUnavailable" });
      return;
    }
    if (closed) return;
    say("Apunta a las barras del producto; lo leemos solo.");
    tick();
  }

  function onLeave() { close({ restoreFocus: false }); }
  function onHidden() { if (document.visibilityState === "hidden") close({ restoreFocus: false }); }
  function onKey(event) {
    if (event.key !== "Escape") return;
    event.preventDefault();
    close();
  }

  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  window.addEventListener("hashchange", onLeave);
  window.addEventListener("pagehide", onLeave);
  document.addEventListener("visibilitychange", onHidden);
  document.addEventListener("keydown", onKey, true);
  document.body.append(dialog);
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
  dialog.querySelector(".scanner-close").focus();
  start();
  session = { close, dialog };
  return session;
}
