export function createMultitrackHost() {
  let owner: symbol | null = null;
  return {
    acquire(candidate: symbol) {
      if (owner !== null && owner !== candidate) return false;
      owner = candidate;
      return true;
    },
    release(candidate: symbol) {
      if (owner === candidate) owner = null;
    },
  };
}

export const multitrackHost = createMultitrackHost();
