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
