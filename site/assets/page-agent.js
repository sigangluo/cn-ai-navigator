// Load Page Agent only after the visitor explicitly starts it.
const agentDialog = document.querySelector('#agentDialog');
const agentForm = document.querySelector('#agentForm');
const agentButton = document.querySelector('#agentButton');
const agentError = document.querySelector('#agentError');
const agentStart = document.querySelector('#agentStart');
let pageAgent = null;
let bundlePromise = null;

function loadPageAgent() {
  if (window.PageAgent) return Promise.resolve(window.PageAgent);
  if (bundlePromise) return bundlePromise;
  bundlePromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/page-agent@1.12.4/dist/iife/page-agent.demo.js?autoInit=false';
    script.crossOrigin = 'anonymous';
    script.integrity = 'sha384-n7nOYgQUo5B2wPwNoJlJ3A43gDT90WZaP927xZi34yuh6C+zOqEPQJ/B7qI1YUDf';
    script.onload = () => window.PageAgent ? resolve(window.PageAgent) : reject(new Error('Page Agent 未正确加载'));
    script.onerror = () => reject(new Error('无法加载 Page Agent，请检查网络后重试'));
    document.head.append(script);
  }).catch((error) => {
    bundlePromise = null;
    throw error;
  });
  return bundlePromise;
}

agentButton.addEventListener('click', () => {
  if (pageAgent && !pageAgent.disposed) {
    pageAgent.panel.show();
    return;
  }
  agentError.hidden = true;
  agentDialog.showModal();
});

agentDialog.querySelector('.agent-close').addEventListener('click', () => agentDialog.close());
agentDialog.querySelector('.agent-cancel').addEventListener('click', () => agentDialog.close());

agentForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  agentError.hidden = true;
  const baseURL = agentForm.querySelector('#agentBaseURL').value.trim();
  const model = agentForm.querySelector('#agentModel').value.trim();
  const apiKeyInput = agentForm.querySelector('#agentApiKey');
  const apiKey = apiKeyInput.value.trim();
  let url;
  try { url = new URL(baseURL); }
  catch {
    agentError.textContent = '请输入完整的接口地址。';
    agentError.hidden = false;
    return;
  }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) {
    agentError.textContent = '接口地址需使用 HTTPS；本机地址可使用 HTTP。';
    agentError.hidden = false;
    return;
  }
  if (url.hostname === 'dashscope.aliyuncs.com' && !apiKey) {
    agentError.textContent = '使用阿里云百炼接口时，请填写自己的 API Key。';
    agentError.hidden = false;
    return;
  }

  agentStart.disabled = true;
  agentStart.textContent = '正在加载…';
  try {
    const PageAgent = await loadPageAgent();
    pageAgent = new PageAgent({ baseURL, model, ...(apiKey ? { apiKey } : {}), language: 'zh-CN' });
    apiKeyInput.value = '';
    agentDialog.close();
    pageAgent.panel.show();
    agentButton.textContent = '✳ 打开 AI 助手';
  } catch (error) {
    agentError.textContent = error.message || '启动失败，请稍后重试。';
    agentError.hidden = false;
  } finally {
    agentStart.disabled = false;
    agentStart.textContent = '启动助手';
  }
});
