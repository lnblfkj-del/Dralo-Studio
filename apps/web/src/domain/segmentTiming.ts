import type { JSONContent } from "@tiptap/react";
import { documentShots, editedScript } from "./segmentDocument";

export function resizeSegmentTiming(generation: number, parameters: Record<string, unknown> = {}, shots: { shot_id: number; start_time: number; end_time: number }[] = []) {
  const next = { ...parameters };
  const script = next.structured_script as Record<string, unknown> | undefined;
  if (script?.editor_document) {
    const doc = structuredClone(script.editor_document) as JSONContent;
    const nodes = documentShots(doc);
    const total = nodes.reduce((sum, n) => sum + Number(n.attrs?.duration || 0), 0);
    for (const n of nodes) n.attrs = { ...n.attrs, duration: generation * Number(n.attrs?.duration || 0) / total };
    next.structured_script = editedScript(script, doc);
  } else if (script && Array.isArray(script.camera)) {
    const cameras = script.camera as Record<string, unknown>[];
    const weight = (item: Record<string, unknown>) => {
      const shot = shots.find((entry) => entry.shot_id === item.shot_id);
      return Number(item.duration) || (shot ? shot.end_time - shot.start_time : 1);
    };
    const total = cameras.reduce((sum, item) => sum + weight(item), 0);
    next.structured_script = { ...script, camera: cameras.map((item) => ({
      ...item, duration: generation * weight(item) / total,
    })) };
  }
  if ("duration" in next) next.duration = generation;
  return { generation_duration: generation, timeline_duration: generation, trim_in: 0, trim_out: 0, parameters: next };
}
