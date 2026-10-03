/** Recover a failed Vite CSS preload without reloading or discarding drafts. */
export async function retryCanvasImport<T>(load: () => Promise<T>): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await load();
    } catch (error) {
      const match = error instanceof Error
        ? /^Unable to preload CSS for (.+)$/.exec(error.message)
        : null;
      if (!match?.[1] || attempt >= 2) throw error;
      const url = new URL(match[1], window.location.href);
      if (url.origin !== window.location.origin || !/^\/assets\/[^/]+\.css$/.test(url.pathname)) throw error;
      await restoreStylesheet(url.href);
    }
  }
}

function restoreStylesheet(href: string): Promise<void> {
  return new Promise((resolve, reject) => {
    // Vite remembers the failed dependency and skips it on the next import.
    for (const existing of document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')) {
      if (existing.href === href) existing.remove();
    }
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.crossOrigin = '';
    link.href = href;
    const timer = window.setTimeout(() => finish(new Error('画布样式加载超时，请检查网络后重新打开页面。')), 10_000);
    function finish(error?: Error) {
      window.clearTimeout(timer);
      link.onload = null;
      link.onerror = null;
      if (error) { link.remove(); reject(error); }
      else resolve();
    }
    link.onload = () => finish();
    link.onerror = () => finish(new Error('画布样式加载失败，请检查网络后重新打开页面。'));
    document.head.append(link);
  });
}
