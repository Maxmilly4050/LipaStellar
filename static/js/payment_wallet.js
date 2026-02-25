/**
 * payment_wallet.js
 * Real-time Stellar public key validation and account status for the
 * wallet payment form (payment_wallet.html).
 */

(function () {
  'use strict';

  const pkInput      = document.getElementById('stellarPublicKey');
  const amountInput  = document.getElementById('amountTzs');
  const statusBox    = document.getElementById('accountStatusBox');
  const pkMsg        = document.getElementById('pkValidationMsg');
  const usdcPreview  = document.getElementById('usdcPreview');
  const submitBtn    = document.getElementById('submitBtn');

  const TZS_PER_USDC = 2500;

  // ── USDC preview ──────────────────────────────────────────────────────────
  if (amountInput && usdcPreview) {
    amountInput.addEventListener('input', () => {
      const tzs = parseFloat(amountInput.value);
      usdcPreview.textContent = (!isNaN(tzs) && tzs > 0)
        ? `≈ ${(tzs / TZS_PER_USDC).toFixed(4)} USDC`
        : '';
    });
  }

  // ── Public key format check (client-side, instant) ────────────────────────
  function isValidStellarKeyFormat(pk) {
    return typeof pk === 'string' && pk.startsWith('G') && pk.length === 56;
  }

  // ── AJAX account status check ─────────────────────────────────────────────
  let checkTimer = null;

  function showStatus(html, type) {
    if (!statusBox) return;
    statusBox.className = `alert alert-${type} py-2`;
    statusBox.innerHTML = html;
    statusBox.classList.remove('d-none');
  }

  function checkAccount() {
    if (!pkInput || !statusBox) return;

    const pk = pkInput.value.trim();
    if (!isValidStellarKeyFormat(pk)) return;

    const amount = amountInput ? parseFloat(amountInput.value) || 0 : 0;
    const amountUsdc = (amount / TZS_PER_USDC).toFixed(7);

    showStatus('<span class="spinner-border spinner-border-sm me-2"></span>Checking account…', 'info');

    const formData = new FormData();
    formData.append('public_key', pk);
    formData.append('amount_usdc', amountUsdc);
    // CSRF token
    const csrf = document.querySelector('[name=csrfmiddlewaretoken]');
    if (csrf) formData.append('csrfmiddlewaretoken', csrf.value);

    fetch('/api/validate-stellar/', {
      method: 'POST',
      body: formData,
    })
      .then(r => r.json())
      .then(data => {
        if (data.valid) {
          const d = data.details || {};
          showStatus(
            `✅ Account ready &nbsp;|&nbsp; USDC: <strong>${parseFloat(d.usdc_balance || 0).toFixed(2)}</strong> &nbsp;|&nbsp; XLM: ${parseFloat(d.xlm_balance || 0).toFixed(4)}`,
            'success'
          );
          if (submitBtn) submitBtn.disabled = false;
        } else {
          showStatus(`❌ ${data.error}`, 'danger');
          if (submitBtn) submitBtn.disabled = true;
        }
      })
      .catch(() => {
        showStatus('⚠️ Could not reach validation server.', 'warning');
        if (submitBtn) submitBtn.disabled = false; // allow submit anyway
      });
  }

  if (pkInput) {
    pkInput.addEventListener('input', () => {
      const pk = pkInput.value.trim();

      // Instant format feedback
      if (pk.length === 0) {
        pkMsg.textContent = '';
        pkMsg.className = 'form-text';
        statusBox && statusBox.classList.add('d-none');
        return;
      }
      if (!pk.startsWith('G')) {
        pkMsg.textContent = '⚠️ Stellar public keys start with "G".';
        pkMsg.className = 'form-text text-warning';
        return;
      }
      if (pk.length !== 56) {
        pkMsg.textContent = `${pk.length}/56 characters`;
        pkMsg.className = 'form-text text-muted';
        return;
      }

      pkMsg.textContent = '✅ Format OK';
      pkMsg.className = 'form-text text-success';

      // Debounce the network call
      clearTimeout(checkTimer);
      checkTimer = setTimeout(checkAccount, 600);
    });
  }

  // Re-check when amount changes (balance sufficiency may change)
  if (amountInput && pkInput) {
    amountInput.addEventListener('change', () => {
      if (pkInput.value.trim().length === 56) {
        clearTimeout(checkTimer);
        checkTimer = setTimeout(checkAccount, 400);
      }
    });
  }

})();
