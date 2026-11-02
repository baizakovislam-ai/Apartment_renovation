// Заставка RenoTrack: счётчик прогресса, кнопка «Пропустить» и удаление заставки после показа.
// Показывать ли её, решает строчка в <head> (шаблон workspace/_intro_head.html).
(function () {
  const root = document.documentElement;
  const intro = document.querySelector(".rt-intro");
  if (!intro || !root.classList.contains("rt-intro-pending")) {
    if (intro) intro.remove();
    return;
  }
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const percent = intro.querySelector(".rt-percent");
  const started = performance.now();
  let frame;

  // Процент растёт вместе с полосой прогресса (с 1.0 до 2.3 секунды).
  function tick(now) {
    const t = Math.min(Math.max((now - started - 1000) / 1300, 0), 1);
    const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    percent.textContent = Math.round(eased * 100) + "%";
    if (t < 1) frame = requestAnimationFrame(tick);
  }
  if (!reduceMotion) frame = requestAnimationFrame(tick);

  function close() {
    cancelAnimationFrame(frame);
    intro.remove();
    root.classList.remove("rt-intro-pending");
  }
  function skip() {
    clearTimeout(timer);
    intro.classList.add("rt-done");
    setTimeout(close, 350);
  }

  const timer = setTimeout(close, reduceMotion ? 1500 : 6000);
  intro.querySelector(".rt-skip").addEventListener("click", skip);
  document.addEventListener("keydown", function onKey(event) {
    if (event.key === "Escape") { document.removeEventListener("keydown", onKey); skip(); }
  });
})();
