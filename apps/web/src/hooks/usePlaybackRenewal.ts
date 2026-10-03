import { useEffect, useRef, useState } from 'react';

// Refresh before the server's 30-minute URL lifetime; bound error recovery to one attempt.
export function usePlaybackRenewal(mediaId: number | null | undefined) {
  const [revision, setRevision] = useState(0);
  const [original, setOriginal] = useState(false);
  const attempted = useRef(false);
  useEffect(() => { attempted.current = false; setOriginal(false); }, [mediaId]);
  useEffect(() => {
    if (!mediaId) return;
    const timer = setInterval(() => { attempted.current = false; setOriginal(false); setRevision(value => value + 1); }, 25 * 60_000);
    return () => clearInterval(timer);
  }, [mediaId]);
  return {revision, original, recover: () => {
    if (attempted.current) return false;
    attempted.current = true;
    setOriginal(true);
    setRevision(value => value + 1);
    return true;
  }};
}
