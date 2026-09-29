(() => {
  const protectedMediaSelector = 'img, video, audio, canvas';

  function protectedMediaFromTarget(target) {
    return target instanceof Element ? target.closest(protectedMediaSelector) : null;
  }

  function protectMedia(media) {
    media.setAttribute('draggable', 'false');
    if (media instanceof HTMLMediaElement) {
      if (media.controlsList) media.controlsList.add('nodownload');
      media.setAttribute('controlsList', 'nodownload noremoteplayback');
    }
    if (media instanceof HTMLVideoElement) {
      media.disablePictureInPicture = true;
    }
  }

  function protectMediaWithin(root) {
    if (root instanceof Element && root.matches(protectedMediaSelector)) {
      protectMedia(root);
    }
    root.querySelectorAll?.(protectedMediaSelector).forEach(protectMedia);
  }

  protectMediaWithin(document);

  document.addEventListener('contextmenu', (event) => {
    if (protectedMediaFromTarget(event.target)) event.preventDefault();
  });

  document.addEventListener('dragstart', (event) => {
    if (protectedMediaFromTarget(event.target)) event.preventDefault();
  });

  const mediaObserver = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (node instanceof Element) protectMediaWithin(node);
      });
    });
  });
  mediaObserver.observe(document.documentElement, { childList: true, subtree: true });

  const videos = [...document.querySelectorAll('video')];
  const primaryVideos = [...document.querySelectorAll('video:not([data-background-video])')];
  if (!primaryVideos.length) return;

  const channelName = 'namegawa-media-playback';
  const storageKey = `${channelName}-event`;
  const tabId = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  let playbackChannel = null;

  try {
    playbackChannel = new BroadcastChannel(channelName);
  } catch {
    playbackChannel = null;
  }

  function pauseOtherVideos(activeVideo = null) {
    videos.forEach((video) => {
      if (video !== activeVideo && !video.paused) video.pause();
    });
  }

  function receivePlaybackNotice(message) {
    if (message?.type === 'playing' && message.tabId !== tabId) {
      pauseOtherVideos();
    }
  }

  function announcePlayback() {
    const message = { type: 'playing', tabId, sentAt: Date.now() };
    playbackChannel?.postMessage(message);
    try {
      localStorage.setItem(storageKey, JSON.stringify(message));
    } catch {
      // Storage may be disabled; BroadcastChannel and same-page control still work.
    }
  }

  primaryVideos.forEach((video) => {
    video.addEventListener('playing', () => {
      pauseOtherVideos(video);
      announcePlayback();
    });
  });

  if (playbackChannel) {
    playbackChannel.addEventListener('message', (event) => {
      receivePlaybackNotice(event.data);
    });
  }

  window.addEventListener('storage', (event) => {
    if (event.key !== storageKey || !event.newValue) return;
    try {
      receivePlaybackNotice(JSON.parse(event.newValue));
    } catch {
      // Ignore malformed values from unrelated scripts or browser extensions.
    }
  });

  window.addEventListener('pagehide', () => playbackChannel?.close(), { once: true });
})();