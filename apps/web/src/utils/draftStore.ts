/** Serialize operations so a late backup cannot resurrect a saved draft. */
let connection: Promise<IDBDatabase> | undefined;
let tail: Promise<unknown> = Promise.resolve();

function database() {
  if (!connection) connection = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open("creation-drafts", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("drafts");
    request.onsuccess = () => { request.result.onversionchange = () => { request.result.close(); connection = undefined; }; resolve(request.result); };
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error("草稿数据库被其他页面占用"));
  }).catch(error => { connection = undefined; throw error; });
  return connection;
}

function operation<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const result = tail.catch(() => undefined).then(async () => {
    const db = await database();
    return new Promise<T>((resolve, reject) => {
      const transaction = db.transaction("drafts", mode);
      const request = run(transaction.objectStore("drafts"));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onabort = () => reject(transaction.error ?? new Error("草稿备份被中断"));
      transaction.onerror = () => reject(transaction.error);
    });
  });
  tail = result;
  return result;
}

export const draftStore = {
  async get<T>(key: string): Promise<T | null> { return (await operation("readonly", store => store.get(key))) ?? null; },
  put(key: string, value: unknown) { return operation("readwrite", store => store.put(value, key)); },
  remove(key: string) { return operation("readwrite", store => store.delete(key)); },
};
