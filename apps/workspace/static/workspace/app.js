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
// «Отправить фото» в WhatsApp / Telegram: передаём сами файлы фотографий.
// Браузер открывает системное окно «Поделиться» (Windows, Android, iOS) — в нём выбирают WhatsApp или Telegram.
// Если браузер не умеет передавать файлы, фото скачиваются, а мессенджер открывается с подписью — фото прикрепляют вручную.
document.querySelectorAll("[data-share]").forEach(bar => {
  const d = bar.dataset;
  const status = bar.querySelector("[data-share-status]");
  const say = message => { if (status) { status.textContent = message; status.hidden = !message; } };
  const caption = [
    `📷 Фотоотчёт: ${d.shareTask}`,
    `🔧 Этап: ${d.shareStage} · ${d.shareProject}`,
    `👤 ${d.shareAuthor}, ${d.shareDate}`,
    `📝 ${d.shareDescription}`,
  ].join("\n");
  const enc = encodeURIComponent;
  const chatLinks = {
    whatsapp: `https://wa.me/?text=${enc(caption)}`,
    telegram: `https://t.me/share/url?url=${enc(d.shareUrl)}&text=${enc(caption)}`,
  };
  let files;
  const loadFiles = async () => {
    if (files) return files;
    const photos = (d.sharePhotos || "").split(" ").filter(Boolean);
    files = await Promise.all(photos.map(async (src, i) => {
      const response = await fetch(src, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`Фото не загрузилось (${response.status})`);
      const blob = await response.blob();
      const ext = (blob.type.split("/")[1] || "jpg").replace("jpeg", "jpg");
      return new File([blob], `foto-${i + 1}.${ext}`, { type: blob.type });
    }));
    return files;
  };
  const download = list => list.forEach(file => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(file); a.download = file.name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  });

  bar.querySelectorAll("[data-share-to]").forEach(button => button.addEventListener("click", async () => {
    const app = button.dataset.shareTo;
    const name = app === "whatsapp" ? "WhatsApp" : "Telegram";
    // Окно мессенджера открываем сразу, пока действует нажатие (иначе браузер заблокирует всплывающее окно).
    const canShareFiles = !!(navigator.share && navigator.canShare);
    const chat = canShareFiles ? null : window.open("about:blank", "_blank");
    button.disabled = true; say("Готовлю фото…");
    try {
      const list = await loadFiles();
      if (canShareFiles && list.length && navigator.canShare({ files: list })) {
        say(`Выберите ${name} в окне «Поделиться».`);
        await navigator.share({ files: list, title: `Фотоотчёт: ${d.shareTask}`, text: caption });
        say("");
      } else {
        download(list);
        if (chat) chat.location.href = chatLinks[app]; else window.open(chatLinks[app], "_blank");
        say(`Фото скачаны (${list.length} шт.) — прикрепите их в открывшемся чате ${name}.`);
      }
    } catch (error) {
      chat?.close();
      if (error?.name === "AbortError") say("");
      else { console.warn("Не удалось отправить фотоотчёт", error); say("Не удалось отправить фото. Обновите страницу и попробуйте ещё раз."); }
    } finally { button.disabled = false; }
  }));
});
