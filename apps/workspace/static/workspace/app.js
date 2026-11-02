const sidebar = document.querySelector(".sidebar");
const overlay = document.querySelector(".mobile-overlay");
const menuButton = document.querySelector("[data-menu-open]");
let menuOrigin;
function setMenu(open) {
  sidebar?.classList.toggle("sidebar--open", open);
  if (overlay) overlay.hidden = !open;
  menuButton?.setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("menu-is-open", open);
  if (open) { menuOrigin = document.activeElement; sidebar?.querySelector("a")?.focus(); }
  else menuOrigin?.focus();
}
menuButton?.addEventListener("click", () => setMenu(true));
document.querySelectorAll("[data-menu-close]").forEach(el => el.addEventListener("click", () => setMenu(false)));
document.addEventListener("keydown", event => {
  if (event.key === "Escape") { setMenu(false); document.querySelectorAll("details[open]").forEach(el => el.open = false); }
  if (event.key === "Tab" && sidebar?.classList.contains("sidebar--open")) {
    const items = [...sidebar.querySelectorAll("a, button, summary")].filter(el => el.getClientRects().length);
    if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
    else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
  }
});
document.querySelectorAll("form[data-confirm]").forEach(form => form.addEventListener("submit", event => {
  if (!window.confirm(form.dataset.confirm)) event.preventDefault();
}));
document.querySelectorAll("[data-copy]").forEach(button => button.addEventListener("click", async () => {
  const input = document.getElementById(button.dataset.copy);
  try { await navigator.clipboard.writeText(input.value); button.textContent = "Скопировано"; }
  catch { input.focus(); input.select(); button.textContent = "Нажмите Ctrl+C"; }
}));
// Блокируем повторную отправку формы (ТЗ 4.13): вторая отправка игнорируется.
document.querySelectorAll("form[data-once]").forEach(form => form.addEventListener("submit", event => {
  if (event.defaultPrevented) return;
  if (form.dataset.sent) { event.preventDefault(); return; }
  form.dataset.sent = "1";
  const submitter = event.submitter;
  if (submitter?.name) {
    // A disabled button is not sent, so keep its value in a hidden field.
    const hidden = document.createElement("input");
    hidden.type = "hidden"; hidden.name = submitter.name; hidden.value = submitter.value;
    form.appendChild(hidden);
  }
  setTimeout(() => form.querySelectorAll("button[type=submit]").forEach(button => button.disabled = true), 0);
}));
// «Подставить площадь в объём» в предварительном расчёте.
document.querySelectorAll("[data-fill-volume]").forEach(button => button.addEventListener("click", () => {
  const input = button.closest("form")?.querySelector("input[name=volume]");
  if (!input) return;
  input.value = button.dataset.fillVolume;
  const unit = button.closest("form").querySelector("input[name=unit]");
  if (unit && !unit.value) unit.value = "м²";
  input.focus();
}));
// «Отправить фотоотчёт» в мессенджеры: WhatsApp, Telegram, Viber, почта, системное меню «Поделиться» с фото.
document.querySelectorAll("[data-share]").forEach(bar => {
  const url = `${location.origin}${location.pathname}#${bar.dataset.shareAnchor}`;
  const title = bar.dataset.shareTitle;
  const text = bar.dataset.shareText;
  const full = `${text}\n${url}`;
  const enc = encodeURIComponent;
  const links = {
    whatsapp: `https://wa.me/?text=${enc(full)}`,
    telegram: `https://t.me/share/url?url=${enc(url)}&text=${enc(text)}`,
    viber: `viber://forward?text=${enc(full)}`,
    email: `mailto:?subject=${enc(title)}&body=${enc(full)}`,
  };
  bar.querySelectorAll("[data-share-to]").forEach(link => { link.href = links[link.dataset.shareTo]; });

  const copy = bar.querySelector("[data-share-copy]");
  copy?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(full); copy.lastChild.textContent = " Скопировано"; }
    catch { window.prompt("Скопируйте текст:", full); }
  });

  // Системное меню «Поделиться» умеет передавать сами фотографии (Android, iOS, Windows).
  const native = bar.querySelector("[data-share-native]");
  if (!native || !navigator.share) return;
  native.hidden = false;
  native.addEventListener("click", async () => {
    native.disabled = true;
    try {
      const photos = (bar.dataset.sharePhotos || "").split(" ").filter(Boolean);
      const files = await Promise.all(photos.map(async (src, i) => {
        const blob = await (await fetch(src, { credentials: "same-origin" })).blob();
        const ext = (blob.type.split("/")[1] || "jpg").replace("jpeg", "jpg");
        return new File([blob], `foto-${i + 1}.${ext}`, { type: blob.type });
      }));
      const data = { title, text: full };
      if (files.length && navigator.canShare?.({ files })) data.files = files;
      await navigator.share(data);
    } catch (error) {
      if (error?.name !== "AbortError") console.warn("Не удалось поделиться фотоотчётом", error);
    } finally { native.disabled = false; }
  });
});
