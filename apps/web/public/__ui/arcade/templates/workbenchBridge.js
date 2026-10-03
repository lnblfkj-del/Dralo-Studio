// Workbench integration only. The upstream game and engine files remain separate.
(() => {
  let active = true;
  let forcedPause = false;
  let pausedBeforeHide = false;
  let suspendedAudio = false;
  const update = () => {
    const playing = active && !document.hidden;
    if (!playing && !forcedPause && typeof setPaused === 'function') {
      pausedBeforeHide = typeof getPaused === 'function' && getPaused();
      setPaused(true);
      forcedPause = true;
    } else if (playing && forcedPause) {
      setPaused(pausedBeforeHide);
      forcedPause = false;
    }
    if (typeof audioContext !== 'undefined' && audioContext) {
      if (!playing && audioContext.state === 'running') {
        suspendedAudio = true;
        void audioContext.suspend();
      } else if (playing && suspendedAudio) {
        suspendedAudio = false;
        void audioContext.resume();
      }
    }
  };
  window.addEventListener('message', (event) => {
    if (event.source !== window.parent || event.data?.type !== 'arcade-visibility') return;
    active = Boolean(event.data.playing);
    update();
  });
  document.addEventListener('visibilitychange', update);
  window.parent.postMessage({ type: 'arcade-ready' }, '*');
})();
