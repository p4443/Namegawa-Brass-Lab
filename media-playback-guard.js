(() => {
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