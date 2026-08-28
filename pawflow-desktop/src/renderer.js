'use strict';

const api = window.pawflowDesktop;
const screen = document.getElementById('screen');
const tabsRoot = document.getElementById('tabs');
const profileTitle = document.getElementById('profileTitle');
const addTabButton = document.getElementById('addTabButton');
const backButton = document.getElementById('backButton');
const forwardButton = document.getElementById('forwardButton');
const reloadButton = document.getElementById('reloadButton');
const serversButton = document.getElementById('serversButton');
const toastRoot = document.getElementById('toast');

let profiles = [];
let currentProfile = null;
let currentProviders = [];
let currentTabs = { active_tab_id: '', tabs: [] };
let toastTimer = null;

function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  if (options.type) node.type = options.type;
  if (options.title) node.title = options.title;
  if (options.value !== undefined) node.value = options.value;
  if (options.placeholder) node.placeholder = options.placeholder;
  if (options.autocomplete) node.autocomplete = options.autocomplete;
  if (options.required) node.required = true;
  if (options.onClick) node.addEventListener('click', options.onClick);
  for (const child of children) node.appendChild(child);
  return node;
}

function toast(message, isError = false) {
  clearTimeout(toastTimer);
  toastRoot.textContent = String(message);
  toastRoot.className = `toast${isError ? ' error' : ''}`;
  toastTimer = setTimeout(() => { toastRoot.className = 'toast hidden'; }, 5000);
}

function errorText(error) {
  return error && error.message ? error.message : String(error);
}

function setChatControls(visible) {
  for (const button of [addTabButton, backButton, forwardButton, reloadButton]) {
    button.classList.toggle('hidden', !visible);
  }
  screen.classList.toggle('hidden', visible);
  if (!visible) tabsRoot.replaceChildren();
}

function busy(message) {
  setChatControls(false);
  screen.replaceChildren(element('div', { className: 'panel busy' }, [
    element('h2', { text: message }),
    element('p', { text: 'Veuillez patienter.' }),
  ]));
}

async function refreshProfiles() {
  profiles = await api.profiles.list();
  showServers();
}

function profileForm(existing = null) {
  setChatControls(false);
  const name = element('input', {
    value: existing ? existing.name : '',
    placeholder: 'Mon serveur PawFlow',
    autocomplete: 'off',
    required: true,
  });
  const baseUrl = element('input', {
    value: existing ? existing.base_url : '',
    placeholder: 'https://pawflow.example.org',
    autocomplete: 'url',
    required: true,
  });
  const gatewayKey = element('input', {
    placeholder: existing ? 'Nouvelle clé (laisser vide pour conserver)' : 'Clé Private Gateway',
    autocomplete: 'off',
    required: !existing,
  });
  gatewayKey.type = 'password';
  const form = element('form', { className: 'form' }, [
    element('label', { text: 'Nom' }, [name]),
    element('label', { text: 'URL HTTPS' }, [baseUrl]),
    element('label', { text: 'Clé Private Gateway' }, [gatewayKey]),
    element('div', { className: 'actions' }, [
      element('button', { className: 'primary', text: 'Enregistrer', type: 'submit' }),
      element('button', { text: 'Annuler', type: 'button', onClick: showServers }),
    ]),
  ]);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      await api.profiles.save({
        id: existing ? existing.id : '',
        name: name.value,
        baseUrl: baseUrl.value,
        gatewayKey: gatewayKey.value,
      });
      profiles = await api.profiles.list();
      showServers();
    } catch (error) {
      toast(errorText(error), true);
    }
  });
  screen.replaceChildren(element('section', { className: 'panel' }, [
    element('h1', { text: existing ? 'Modifier le serveur' : 'Ajouter un serveur' }),
    element('p', { text: 'La clé est chiffrée par le coffre de votre système et n’est jamais exposée au webchat.' }),
    form,
  ]));
}

function showServers() {
  currentProfile = null;
  profileTitle.textContent = 'PawFlow Desktop';
  api.chat.hide().catch(() => {});
  setChatControls(false);
  const list = element('div', { className: 'server-list' });
  for (const profile of profiles) {
    const details = element('div', {}, [
      element('span', { className: 'server-name', text: profile.name }),
      element('span', { className: 'server-url', text: profile.base_url }),
    ]);
    list.appendChild(element('div', { className: 'server' }, [
      details,
      element('button', {
        className: 'primary',
        text: 'Ouvrir',
        onClick: () => connect(profile),
      }),
      element('div', { className: 'actions' }, [
        element('button', { text: 'Modifier', onClick: () => profileForm(profile) }),
        element('button', {
          className: 'danger',
          text: 'Supprimer',
          onClick: () => removeProfile(profile),
        }),
      ]),
    ]));
  }
  screen.replaceChildren(element('section', { className: 'panel' }, [
    element('h1', { text: 'Serveurs PawFlow' }),
    element('p', { text: 'Choisissez un serveur ou ajoutez-en un nouveau.' }),
    list,
    element('button', { className: 'primary', text: 'Ajouter un serveur', onClick: () => profileForm() }),
  ]));
}

async function removeProfile(profile) {
  if (!window.confirm(`Supprimer ${profile.name} et ses identifiants locaux ?`)) return;
  try {
    await api.profiles.remove(profile.id);
    profiles = await api.profiles.list();
    showServers();
  } catch (error) {
    toast(errorText(error), true);
  }
}

async function connect(profile) {
  busy(`Connexion à ${profile.name}`);
  try {
    const result = await api.profiles.connect(profile.id);
    currentProfile = result.profile;
    currentProviders = result.providers;
    showChat(result.tabs);
  } catch (error) {
    toast(errorText(error), true);
    showServers();
  }
}

function showLogin(profile, providers) {
  currentProfile = profile;
  currentProviders = providers || [];
  profileTitle.textContent = profile.name;
  setChatControls(false);
  const providerList = element('div', { className: 'provider-list' });
  for (const provider of currentProviders) {
    if (provider.type === 'password') {
      const username = element('input', { placeholder: 'Nom d’utilisateur', autocomplete: 'username' });
      const password = element('input', { placeholder: 'Mot de passe', autocomplete: 'current-password' });
      password.type = 'password';
      const form = element('form', { className: 'form' }, [
        element('label', { text: 'Nom d’utilisateur' }, [username]),
        element('label', { text: 'Mot de passe' }, [password]),
        element('button', { className: 'primary', text: 'Se connecter avec PawFlow', type: 'submit' }),
      ]);
      form.addEventListener('submit', async event => {
        event.preventDefault();
        busy('Authentification');
        try {
          const result = await api.auth.builtin({
            profileId: profile.id,
            username: username.value,
            password: password.value,
          });
          showChat(result.tabs);
        } catch (error) {
          toast(errorText(error), true);
          showLogin(profile, providers);
        }
      });
      providerList.appendChild(form);
    } else if (provider.type === 'oauth2') {
      providerList.appendChild(element('button', {
        text: `Continuer avec ${provider.display_name || provider.name}`,
        onClick: async () => {
          try {
            await api.auth.oauth({ profileId: profile.id, provider: provider.name });
            toast('Poursuivez la connexion dans votre navigateur.');
          } catch (error) {
            toast(errorText(error), true);
          }
        },
      }));
    }
  }
  screen.replaceChildren(element('section', { className: 'panel' }, [
    element('h1', { text: `Connexion à ${profile.name}` }),
    element('p', { text: 'Choisissez une méthode configurée sur ce serveur.' }),
    providerList,
    element('div', { className: 'actions' }, [
      element('button', { text: 'Retour aux serveurs', onClick: showServers }),
    ]),
  ]));
}

function renderTabs() {
  tabsRoot.replaceChildren();
  for (const tab of currentTabs.tabs || []) {
    const select = element('button', {
      text: tab.title || 'Conversation',
      title: tab.url || 'Conversation PawFlow',
      onClick: async () => {
        currentTabs = await api.chat.activate(tab.id);
        renderTabs();
      },
    });
    if (tab.id === currentTabs.active_tab_id) select.classList.add('active');
    const close = element('button', {
      className: 'close',
      text: '×',
      title: 'Fermer la conversation',
      onClick: async event => {
        event.stopPropagation();
        currentTabs = await api.chat.close(tab.id);
        renderTabs();
        if (!(currentTabs.tabs || []).length) showServers();
      },
    });
    tabsRoot.appendChild(element('div', { className: 'tab' }, [select, close]));
  }
}

function showChat(tabs) {
  currentTabs = tabs || currentTabs;
  if (currentProfile) profileTitle.textContent = currentProfile.name;
  setChatControls(true);
  renderTabs();
  api.chat.show().catch(error => toast(errorText(error), true));
}

serversButton.addEventListener('click', showServers);
addTabButton.addEventListener('click', async () => {
  if (!currentProfile) return;
  try {
    currentTabs = await api.chat.add(currentProfile.id);
    showChat(currentTabs);
  } catch (error) {
    toast(errorText(error), true);
  }
});
backButton.addEventListener('click', () => api.chat.navigate('back'));
forwardButton.addEventListener('click', () => api.chat.navigate('forward'));
reloadButton.addEventListener('click', () => api.chat.navigate('reload'));

api.onTabs(value => {
  currentTabs = value;
  if (currentProfile) renderTabs();
});
api.onAuthRequired(value => showLogin(value.profile, value.providers));
api.onOAuthComplete(value => {
  currentProfile = value.profile;
  showChat(value.tabs);
});
api.onDownload(value => {
  if (value.error) toast(value.error, true);
  else if (value.state === 'completed') toast(`Téléchargement terminé : ${value.filename}`);
});
api.onError(message => toast(message, true));

refreshProfiles().catch(error => {
  screen.replaceChildren(element('section', { className: 'panel' }, [
    element('h1', { text: 'PawFlow Desktop ne peut pas démarrer' }),
    element('p', { text: errorText(error) }),
  ]));
});
