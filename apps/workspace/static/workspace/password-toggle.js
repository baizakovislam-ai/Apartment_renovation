/* Постоянная кнопка «показать/скрыть пароль».
   Встроенный глазок Edge исчезает после потери фокуса или повторного ввода,
   поэтому он скрыт в CSS, а вместо него — своя кнопка, которая видна всегда. */
(function () {
  var EYE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.6 5.1A10.8 10.8 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-2.4 3.4M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.8 9.8 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="M3 3l18 18"/></svg>';

  function enhance(input) {
    if (input.dataset.pwToggle) return;
    input.dataset.pwToggle = "1";
    var wrap = document.createElement("div");
    wrap.className = "pw-field";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-toggle";
    btn.tabIndex = 0;
    wrap.appendChild(btn);

    function render() {
      var shown = input.type === "text";
      btn.innerHTML = shown ? EYE_OFF : EYE;
      btn.setAttribute("aria-label", shown ? "Скрыть пароль" : "Показать пароль");
      btn.setAttribute("aria-pressed", shown ? "true" : "false");
      btn.title = shown ? "Скрыть пароль" : "Показать пароль";
    }
    btn.addEventListener("mousedown", function (e) { e.preventDefault(); }); // фокус остаётся в поле
    btn.addEventListener("click", function () {
      var start = input.selectionStart, end = input.selectionEnd;
      input.type = input.type === "password" ? "text" : "password";
      render();
      input.focus();
      try { input.setSelectionRange(start, end); } catch (err) {}
    });
    // перед отправкой формы возвращаем type=password, чтобы браузер корректно предложил сохранить пароль
    if (input.form) input.form.addEventListener("submit", function () { input.type = "password"; });
    render();
  }

  function init() {
    document.querySelectorAll('input[type="password"]').forEach(enhance);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
