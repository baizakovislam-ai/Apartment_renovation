const resendButton = document.querySelector('[data-resend-seconds]');
if (resendButton) {
  const seconds = Number(resendButton.dataset.resendSeconds);
  const availableAt = Date.now() + seconds * 1000;
  const update = () => {
    const remaining = Math.max(0, Math.ceil((availableAt - Date.now()) / 1000));
    resendButton.disabled = remaining > 0;
    resendButton.textContent = remaining > 0
      ? `Отправить повторно через ${remaining} сек.`
      : 'Отправить код повторно';
    return remaining;
  };
  if (update()) {
    const timer = setInterval(() => { if (!update()) clearInterval(timer); }, 1000);
  }
}
