export type ClientStorage = { downloadDirectory: string; cacheDirectory: string; cacheUsed: number };
export type ClientVersion = { version: string; status: string; nextVersion: string; progress: number };
export type ClientStorageAction = { type: 'read' | 'download-directory' | 'cache-directory' | 'open-download' | 'open-cache' | 'clear-cache' };
declare global {
  interface Window {
    draloDesktop?: {
      storage: (action?: ClientStorageAction) => Promise<ClientStorage>;
      version: () => Promise<ClientVersion>;
      showVersion: () => Promise<void>;
      website: () => Promise<void>;
    };
  }
}
export const desktopClient = () => window.draloDesktop;
