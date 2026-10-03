// Adapted from ai-video-editor d5e9b3f; copyright/license in NOTICE.md.
export function getVisualSegmentTimeline<T extends { id: string; duration: number }>(segments: T[]) {
  let cursor = 0;
  return segments.map((segment) => {
    const duration = Math.max(0, segment.duration || 0);
    const item = { id: segment.id, start: cursor, end: cursor + duration, duration };
    cursor += duration;
    return item;
  });
}

export function reorderTimelineItems<T>(items: T[], fromIndex: number, toIndex: number) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= items.length || toIndex >= items.length) return items;
  const nextItems = [...items];
  const [movedItem] = nextItems.splice(fromIndex, 1);
  if (movedItem !== undefined) nextItems.splice(toIndex, 0, movedItem);
  return nextItems;
}
