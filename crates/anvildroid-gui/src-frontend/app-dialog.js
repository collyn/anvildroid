// Serialize confirmations so each result belongs to the action that asked.
const AppDialog = (() => {
  let queue = Promise.resolve();
  function request(message, options, inputValue) {
    const task = queue.then(() => new Promise((resolve, reject) => {
      const dialog = document.getElementById('app-dialog');
      const form = document.getElementById('app-dialog-form');
      const input = document.getElementById('app-dialog-input');
      const cancel = document.getElementById('app-dialog-cancel');
      const isPrompt = inputValue !== undefined;
      document.getElementById('app-dialog-title').textContent = options.title || (isPrompt ? 'Enter a name' : 'Confirm action');
      document.getElementById('app-dialog-message').textContent = message;
      document.getElementById('app-dialog-input-field').hidden = !isPrompt;
      document.getElementById('app-dialog-input-label').textContent = options.inputLabel || 'Name';
      document.getElementById('app-dialog-submit').textContent = options.okLabel || (isPrompt ? 'Save' : 'Continue');
      cancel.textContent = options.cancelLabel || 'Cancel';
      input.value = isPrompt ? inputValue : '';
      input.required = isPrompt;
      dialog.returnValue = '';
      let result = isPrompt ? null : false;
      const submit = event => { event.preventDefault(); result = isPrompt ? input.value : true; dialog.close(); };
      const dismiss = () => dialog.close();
      const closed = () => {
        form.removeEventListener('submit', submit);
        cancel.removeEventListener('click', dismiss);
        dialog.removeEventListener('close', closed);
        resolve(result);
      };
      form.addEventListener('submit', submit);
      cancel.addEventListener('click', dismiss);
      dialog.addEventListener('close', closed);
      try {
        dialog.showModal();
        // Keep destructive confirmation explicit; Enter on initial focus cancels.
        if (isPrompt) { input.focus(); input.select(); } else cancel.focus();
      } catch (error) {
        form.removeEventListener('submit', submit);
        cancel.removeEventListener('click', dismiss);
        dialog.removeEventListener('close', closed);
        reject(error);
      }
    }));
    queue = task.catch(() => {});
    return task;
  }
  return {
    confirm: (message, options = {}) => request(message, options),
    prompt: (message, value = '', options = {}) => request(message, options, value),
  };
})();
if (typeof module !== 'undefined') module.exports = AppDialog;
