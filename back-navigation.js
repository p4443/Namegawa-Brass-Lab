(() => {
  function goBack(fallbackUrl) {
    if (window.history.length > 1) {
      window.history.back();
      return;
    }
    if (fallbackUrl) window.location.assign(fallbackUrl);
  }

  document.addEventListener('click', event => {
    const control = event.target.closest('[data-history-back]');
    if (!control) return;
    event.preventDefault();
    goBack(control.getAttribute('href') || control.dataset.fallback);
  });

  window.siteHistoryBack = goBack;
})();