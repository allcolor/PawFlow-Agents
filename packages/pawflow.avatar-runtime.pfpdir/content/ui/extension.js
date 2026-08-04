// PawFlow avatar runtime. All feature behavior is package-owned.
(function () {
  'use strict';

  var PACKAGE_ID = 'pawflow.avatar-runtime';
  var RESOURCE_TYPE = 'pawflow.avatar';

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function resultValue(response) {
    if (response && Object.prototype.hasOwnProperty.call(response, 'result')) {
      return response.result;
    }
    return response;
  }

  function errorText(error) {
    return (error && error.message) || String(error || 'Unknown error');
  }

  function assetById(row, id) {
    var assets = row && row.assets || [];
    for (var i = 0; i < assets.length; i++) {
      if (assets[i] && assets[i].id === id) return assets[i];
    }
    return null;
  }

  function storageKey(pfp) {
    var context = pfp.context();
    return [
      PACKAGE_ID,
      'selection',
      context.user,
      context.conversation,
      context.agent,
    ].join(':');
  }

  pawflow.register(PACKAGE_ID, function (pfp) {
    var state = {
      rows: null,
      selected: '',
      visible: false,
      speaking: false,
      status: 'idle',
      detail: '',
      root: null,
      surface: null,
      statusNode: null,
      titleNode: null,
      head: null,
      headAudio: null,
      motion: null,
      mediaNodes: Object.create(null),
      pcmSources: [],
      loadToken: 0,
      destroyed: false,
      syntheticTimer: null,
    };

    function call(action, body) {
      return pfp.call(action, body || {}).then(resultValue);
    }

    function setStatus(status, detail) {
      state.status = status;
      state.detail = String(detail || '');
      if (state.root) state.root.setAttribute('data-avatar-status', status);
      if (state.statusNode) {
        state.statusNode.textContent = state.detail || status;
      }
    }

    function readSelection() {
      try {
        return localStorage.getItem(storageKey(pfp)) || '';
      } catch (_error) {
        return '';
      }
    }

    function writeSelection(name) {
      state.selected = String(name || '');
      try {
        if (state.selected) localStorage.setItem(storageKey(pfp), state.selected);
        else localStorage.removeItem(storageKey(pfp));
      } catch (_error) {
        // Browser storage may be blocked; selection still works for this page.
      }
    }

    function ensureRows(force) {
      if (state.rows && !force) return Promise.resolve(state.rows);
      return call('avatar.list').then(function (rows) {
        state.rows = Array.isArray(rows) ? rows : [];
        var saved = readSelection();
        var exists = state.rows.some(function (row) {
          return row && row.name === saved;
        });
        state.selected = exists ? saved : (
          state.rows.length ? String(state.rows[0].name || '') : '');
        return state.rows;
      });
    }

    function selectedRow() {
      var rows = state.rows || [];
      for (var i = 0; i < rows.length; i++) {
        if (rows[i] && rows[i].name === state.selected) return rows[i];
      }
      return null;
    }

    function disconnectMedia() {
      Object.keys(state.mediaNodes).forEach(function (id) {
        var record = state.mediaNodes[id];
        try { if (record && record.node) record.node.disconnect(); } catch (_error) {}
        delete state.mediaNodes[id];
      });
      state.pcmSources.slice().forEach(function (source) {
        try { source.stop(); } catch (_error) {}
        try { source.disconnect(); } catch (_error) {}
      });
      state.pcmSources = [];
    }

    function teardownRenderer(reason) {
      state.loadToken += 1;
      disconnectMedia();
      if (state.syntheticTimer) {
        clearTimeout(state.syntheticTimer);
        state.syntheticTimer = null;
      }
      if (state.motion) {
        try { state.motion.stop(); } catch (_error) {}
        try { state.motion.stopMirror(); } catch (_error) {}
        state.motion = null;
      }
      if (state.headAudio) {
        try { state.headAudio.disconnect(); } catch (_error) {}
        state.headAudio.onvalue = null;
        state.headAudio.onstarted = null;
        state.headAudio.onended = null;
        state.headAudio = null;
      }
      if (state.head) {
        var audioContext = state.head.audioCtx;
        try { state.head.dispose(); } catch (_error) {}
        if (audioContext && typeof audioContext.close === 'function') {
          try { audioContext.close(); } catch (_error) {}
        }
        state.head = null;
      }
      if (state.surface) clear(state.surface);
      if (reason) setStatus('idle', reason);
    }

    function renderSynthetic(row) {
      clear(state.surface);
      var documentData = row.document || {};
      var options = documentData.renderer_options || {};
      var synthetic = options.synthetic || {};
      var face = el('div', 'pf-avatar-synthetic');
      face.style.setProperty('--pf-avatar-accent', synthetic.accent || '#6c8cff');
      var eyes = el('div', 'pf-avatar-synthetic-eyes');
      eyes.appendChild(el('span', 'pf-avatar-synthetic-eye'));
      eyes.appendChild(el('span', 'pf-avatar-synthetic-eye'));
      face.appendChild(eyes);
      face.appendChild(el('div', 'pf-avatar-synthetic-mouth'));
      state.surface.appendChild(face);
      setStatus('ready', 'Synthetic renderer ready');
    }

    function updateSyntheticLevel(level) {
      if (!state.surface) return;
      var face = state.surface.querySelector('.pf-avatar-synthetic');
      if (!face) return;
      var value = Math.max(0, Math.min(1, Number(level) || 0));
      face.style.setProperty('--pf-avatar-mouth', String(value));
      face.classList.toggle('is-speaking', value > 0.03 || state.speaking);
      if (state.syntheticTimer) clearTimeout(state.syntheticTimer);
      state.syntheticTimer = setTimeout(function () {
        face.style.setProperty('--pf-avatar-mouth', '0');
        face.classList.toggle('is-speaking', state.speaking);
      }, 120);
    }

    function configureHeadAudio(head, token) {
      var vendor = window.PawflowAvatarVendor;
      var workletUrl = pfp.asset('head-audio-worklet');
      var modelUrl = pfp.asset('head-audio-model');
      if (!vendor || !vendor.HeadAudio || !workletUrl || !modelUrl) {
        return Promise.resolve(false);
      }
      if (!window.AudioWorkletNode || !head.audioCtx || !head.audioCtx.audioWorklet) {
        return Promise.resolve(false);
      }
      return head.audioCtx.audioWorklet.addModule(workletUrl).then(function () {
        if (token !== state.loadToken || !state.head) return false;
        var audio = new vendor.HeadAudio(head.audioCtx, {
          processorOptions: {},
          parameterData: {
            vadGateActiveDb: -40,
            vadGateInactiveDb: -60,
          },
        });
        return audio.loadModel(modelUrl).then(function () {
          if (token !== state.loadToken || !state.head) {
            try { audio.disconnect(); } catch (_error) {}
            return false;
          }
          audio.onvalue = function (key, value) {
            var target = state.head && state.head.mtAvatar
              ? state.head.mtAvatar[key] : null;
            if (target) {
              Object.assign(target, {newvalue: value, needsUpdate: true});
            }
          };
          audio.onstarted = function () { updateSpeaking(true); };
          audio.onended = function () { updateSpeaking(false); };
          state.headAudio = audio;
          return true;
        });
      }).catch(function (error) {
        console.warn('[avatar-runtime] HeadAudio unavailable:', errorText(error));
        return false;
      });
    }

    function loadTalkingHead(row, token) {
      var vendor = window.PawflowAvatarVendor;
      if (!vendor || !vendor.TalkingHead || !vendor.MotionEngine) {
        throw new Error('The vendored avatar renderer did not load');
      }
      if (!window.WebGLRenderingContext && !window.WebGL2RenderingContext) {
        throw new Error('WebGL is not available in this browser');
      }
      var documentData = row.document || {};
      var model = documentData.model || {};
      var modelAsset = assetById(row, model.asset);
      if (!modelAsset || !modelAsset.url) {
        throw new Error('The selected avatar model asset is unavailable');
      }
      clear(state.surface);
      var options = documentData.renderer_options || {};
      var rendererOptions = options.talkinghead || {};
      var head = new vendor.TalkingHead(state.surface, {
        cameraView: rendererOptions.camera_view || 'upper',
        modelPixelRatio: Math.min(window.devicePixelRatio || 1, 2),
        modelFPS: 30,
        lipsyncModules: [],
        lightAmbientIntensity: 2,
        lightDirectIntensity: 10,
      });
      state.head = head;
      return head.showAvatar({
        url: modelAsset.url,
        body: model.body || 'F',
        avatarMood: 'neutral',
        lipsyncLang: 'en',
      }).then(function () {
        if (token !== state.loadToken || !state.head) return;
        var motion = new vendor.MotionEngine(head);
        motion.registerMotions(vendor.motions || {});
        state.motion = motion;
        return configureHeadAudio(head, token).then(function () {
          if (token !== state.loadToken || !state.head) return;
          head.opt.update = function (deltaMs) {
            if (state.headAudio) state.headAudio.update(deltaMs);
            if (state.motion) state.motion.update(deltaMs);
          };
          setStatus(
            state.headAudio ? 'ready' : 'degraded',
            state.headAudio
              ? 'Avatar ready with audio lip sync'
              : 'Avatar ready; AudioWorklet lip sync is unavailable');
        });
      });
    }

    function loadSelected() {
      var row = selectedRow();
      teardownRenderer('');
      if (!state.visible) return Promise.resolve();
      if (!row) {
        setStatus('empty', 'No avatar is installed');
        return Promise.resolve();
      }
      var documentData = row.document || {};
      if (state.titleNode) state.titleNode.textContent = documentData.title || row.name;
      setStatus('loading', 'Loading ' + (documentData.title || row.name) + '…');
      var token = state.loadToken;
      if (documentData.renderer === 'synthetic') {
        renderSynthetic(row);
        return Promise.resolve();
      }
      return Promise.resolve().then(function () {
        return loadTalkingHead(row, token);
      }).catch(function (error) {
        if (token !== state.loadToken) return;
        teardownRenderer('');
        setStatus('error', errorText(error));
        if (state.surface) {
          state.surface.appendChild(el(
            'div', 'pf-avatar-error',
            'Avatar renderer error: ' + errorText(error)));
        }
      });
    }

    function selectAvatar(name) {
      var exists = (state.rows || []).some(function (row) {
        return row && row.name === name;
      });
      if (!exists) return Promise.reject(new Error('Unknown avatar: ' + name));
      writeSelection(name);
      return loadSelected().then(function () {
        return {selected: state.selected, status: state.status};
      });
    }

    function updateSpeaking(speaking) {
      state.speaking = Boolean(speaking);
      if (state.root) state.root.classList.toggle('is-speaking', state.speaking);
      updateSyntheticLevel(state.speaking ? 0.45 : 0);
      if (state.speaking && state.head && state.head.lookAtCamera) {
        try { state.head.lookAtCamera(300); } catch (_error) {}
      }
    }

    function attachTrack(descriptor) {
      if (!state.headAudio || !state.head || !descriptor) return;
      var track = descriptor.track && descriptor.track.mediaStreamTrack;
      if (!track || !window.MediaStream) return;
      try {
        var stream = new MediaStream([track]);
        var source = state.head.audioCtx.createMediaStreamSource(stream);
        source.connect(state.headAudio);
        state.mediaNodes[descriptor.id] = {node: source, stream: stream};
      } catch (error) {
        console.warn('[avatar-runtime] media track attach failed:', errorText(error));
      }
    }

    function detachTrack(payload) {
      var id = payload && (payload.id || (payload.source && payload.source.id));
      var record = id ? state.mediaNodes[id] : null;
      if (!record) return;
      try { record.node.disconnect(); } catch (_error) {}
      delete state.mediaNodes[id];
    }

    function frameLevel(samples) {
      if (!samples || !samples.length) return 0;
      var sum = 0;
      for (var i = 0; i < samples.length; i++) {
        var value = Number(samples[i]) || 0;
        sum += value * value;
      }
      return Math.sqrt(sum / samples.length);
    }

    function feedPcm(frame) {
      var samples = frame && frame.samples;
      updateSyntheticLevel(frameLevel(samples) * 4);
      if (!state.headAudio || !state.head || !samples || !samples.length) return;
      try {
        var values = samples instanceof Float32Array
          ? samples : new Float32Array(samples);
        var rate = Number(frame.sample_rate) || 24000;
        var buffer = state.head.audioCtx.createBuffer(1, values.length, rate);
        buffer.getChannelData(0).set(values);
        var source = state.head.audioCtx.createBufferSource();
        source.buffer = buffer;
        source.connect(state.headAudio);
        state.pcmSources.push(source);
        source.onended = function () {
          var index = state.pcmSources.indexOf(source);
          if (index >= 0) state.pcmSources.splice(index, 1);
          try { source.disconnect(); } catch (_error) {}
        };
        source.start();
      } catch (error) {
        console.warn('[avatar-runtime] PCM lip-sync feed failed:', errorText(error));
      }
    }

    function makeStage() {
      if (state.root) return state.root;
      var root = el('section', 'pf-avatar-stage');
      root.hidden = !state.visible;
      var toolbar = el('div', 'pf-avatar-toolbar');
      state.titleNode = el('strong', 'pf-avatar-title', 'Avatar');
      var repositoryButton = el('button', 'pf-avatar-button', 'Repository');
      repositoryButton.type = 'button';
      repositoryButton.addEventListener('click', openRepository);
      var closeButton = el('button', 'pf-avatar-button', 'Close');
      closeButton.type = 'button';
      closeButton.addEventListener('click', function () { toggleStage(false); });
      toolbar.appendChild(state.titleNode);
      toolbar.appendChild(repositoryButton);
      toolbar.appendChild(closeButton);
      state.surface = el('div', 'pf-avatar-surface');
      state.statusNode = el('div', 'pf-avatar-status', 'idle');
      root.appendChild(toolbar);
      root.appendChild(state.surface);
      root.appendChild(state.statusNode);
      state.root = root;
      return root;
    }

    function toggleStage(force) {
      state.visible = typeof force === 'boolean' ? force : !state.visible;
      var root = makeStage();
      root.hidden = !state.visible;
      if (!state.visible) {
        teardownRenderer('Avatar paused');
        return Promise.resolve({visible: false});
      }
      return ensureRows(false).then(loadSelected).then(function () {
        return {visible: true, selected: state.selected, status: state.status};
      }).catch(function (error) {
        setStatus('error', errorText(error));
        return {visible: true, status: 'error', error: errorText(error)};
      });
    }

    function repositoryCard(row, dialogBody) {
      var documentData = row.document || {};
      var card = el('article', 'pf-avatar-card');
      var previewAsset = documentData.preview
        ? assetById(row, documentData.preview.asset) : null;
      if (previewAsset && previewAsset.url) {
        var image = el('img', 'pf-avatar-card-preview');
        image.src = previewAsset.url;
        image.alt = '';
        card.appendChild(image);
      } else {
        card.appendChild(el(
          'div', 'pf-avatar-card-preview pf-avatar-card-placeholder',
          documentData.renderer === 'synthetic' ? 'Synthetic' : '3D'));
      }
      var copy = el('div', 'pf-avatar-card-copy');
      copy.appendChild(el('strong', '', documentData.title || row.name));
      copy.appendChild(el(
        'p', '', documentData.description || 'No description'));
      copy.appendChild(el(
        'small', '',
        (row.source || 'package') + ' · ' + (documentData.renderer || 'unknown')));
      card.appendChild(copy);
      var actions = el('div', 'pf-avatar-card-actions');
      var select = el(
        'button', 'pf-avatar-button',
        row.name === state.selected ? 'Selected' : 'Select');
      select.type = 'button';
      select.disabled = row.name === state.selected;
      select.addEventListener('click', function () {
        selectAvatar(row.name).then(function () {
          pfp.ui.closeDialog();
          toggleStage(true);
        }).catch(function (error) {
          dialogBody.prepend(el('div', 'pf-avatar-error', errorText(error)));
        });
      });
      actions.appendChild(select);
      if (row.source === 'user') {
        var remove = el('button', 'pf-avatar-button pf-avatar-danger', 'Delete');
        remove.type = 'button';
        remove.addEventListener('click', function () {
          call('avatar.delete', {name: row.name}).then(function () {
            state.rows = null;
            if (state.selected === row.name) writeSelection('');
            openRepository();
          }).catch(function (error) {
            dialogBody.prepend(el('div', 'pf-avatar-error', errorText(error)));
          });
        });
        actions.appendChild(remove);
      }
      card.appendChild(actions);
      return card;
    }

    function openRepository() {
      var body = el('div', 'pf-avatar-repository');
      body.appendChild(el('div', 'pf-avatar-status', 'Loading avatars…'));
      pfp.ui.openDialog('Avatar repository', body);
      ensureRows(true).then(function (rows) {
        clear(body);
        var controls = el('div', 'pf-avatar-repository-controls');
        var refresh = el('button', 'pf-avatar-button', 'Refresh');
        refresh.type = 'button';
        refresh.addEventListener('click', openRepository);
        controls.appendChild(el(
          'span', '', rows.length + (rows.length === 1 ? ' avatar' : ' avatars')));
        controls.appendChild(refresh);
        body.appendChild(controls);
        if (!rows.length) {
          body.appendChild(el(
            'p', 'pf-avatar-empty',
            'Install an avatar pack that depends on pawflow.avatar-runtime.'));
          return;
        }
        var grid = el('div', 'pf-avatar-grid');
        rows.forEach(function (row) {
          grid.appendChild(repositoryCard(row, body));
        });
        body.appendChild(grid);
      }).catch(function (error) {
        clear(body);
        body.appendChild(el('div', 'pf-avatar-error', errorText(error)));
      });
    }

    function toggleButton(compact) {
      var button = el(
        'button',
        compact ? 'pf-avatar-toggle pf-avatar-toggle-compact' : 'pf-avatar-toggle',
        compact ? 'Avatar' : 'Open avatar');
      button.type = 'button';
      button.title = 'Toggle avatar stage';
      button.addEventListener('click', function () { toggleStage(); });
      return button;
    }

    pfp.ui.slot('header_actions', 'avatar.toggle', function () {
      return toggleButton(true);
    });
    pfp.ui.slot('composer_accessory', 'avatar.toggle', function () {
      return toggleButton(true);
    });
    pfp.ui.slot('action_menu', 'avatar.repository', function () {
      var item = el('button', 'pf-avatar-menu-item', 'Avatar repository');
      item.type = 'button';
      item.addEventListener('click', openRepository);
      return item;
    });
    pfp.ui.slot('conversation_stage', 'avatar.stage', makeStage);
    pfp.ui.slot('resources_collection', 'avatar.repository', function () {
      var box = el('div', 'pf-avatar-resource-entry');
      var button = el('button', 'pf-avatar-button', 'Browse avatars');
      button.type = 'button';
      button.addEventListener('click', openRepository);
      box.appendChild(button);
      return box;
    });

    if (pfp.semantic) {
      pfp.semantic.register({
        id: 'stage.avatar',
        role: 'figure',
        label: 'Current PawFlow avatar',
        parent: 'conversation.stage',
        state: function () {
          return {
            selected: state.selected,
            speaking: state.speaking,
            visible: state.visible,
            status: state.status,
            detail: state.detail,
          };
        },
        actions: {
          toggle: {
            parameters: {
              visible: {type: 'boolean'},
            },
            run: function (params) {
              return toggleStage(
                typeof params.visible === 'boolean'
                  ? params.visible : undefined);
            },
          },
          select: {
            parameters: {
              name: {type: 'string', required: true},
            },
            run: function (params) {
              return ensureRows(false).then(function () {
                return selectAvatar(params.name);
              });
            },
          },
          showRepository: {
            parameters: {},
            run: function () {
              openRepository();
              return {opened: true};
            },
          },
          setMood: {
            parameters: {
              mood: {type: 'string', required: true},
            },
            run: function (params) {
              if (!state.head || !state.head.setMood) {
                throw new Error('No TalkingHead avatar is active');
              }
              state.head.setMood(params.mood);
              return {mood: params.mood};
            },
          },
          playMotion: {
            parameters: {
              motion: {type: 'string', required: true},
            },
            run: function (params) {
              if (!state.motion) throw new Error('No motion engine is active');
              if (state.motion.getMotionNames().indexOf(params.motion) < 0) {
                throw new Error('Unknown motion: ' + params.motion);
              }
              return state.motion.play(params.motion).then(function () {
                return {motion: params.motion};
              });
            },
          },
        },
      });
    }

    pfp.on('conversation_changed', function () {
      state.rows = null;
      writeSelection(readSelection());
      teardownRenderer('Conversation changed');
      if (state.visible) toggleStage(true);
    });
    pfp.on('agent_changed', function () {
      state.rows = null;
      writeSelection(readSelection());
      teardownRenderer('Agent changed');
      if (state.visible) toggleStage(true);
    });
    pfp.on('resource_changed', function (event) {
      if (!event || event.resource_type !== RESOURCE_TYPE) return;
      state.rows = null;
      ensureRows(true).then(function () {
        if (state.visible) return loadSelected();
      }).catch(function (error) {
        setStatus('error', errorText(error));
      });
    });
    pfp.on('realtime_state_changed', function (event) {
      var value = event && event.state;
      updateSpeaking(value === 'speaking' || value === 'responding');
      if (value === 'listening' && state.motion) {
        state.motion.play('thinking_face').catch(function () {});
      }
    });
    pfp.on('media_track_subscribed', attachTrack);
    pfp.on('media_track_unsubscribed', detachTrack);
    pfp.on('media_audio_frame', feedPcm);
    pfp.on('message_streaming', function () {
      if (!state.speaking) updateSyntheticLevel(0.2);
    });
    pfp.on('shutdown', function () {
      state.destroyed = true;
      state.visible = false;
      teardownRenderer('Avatar stopped');
    });
  });
}());
