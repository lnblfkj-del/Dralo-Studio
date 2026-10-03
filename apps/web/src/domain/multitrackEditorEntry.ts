import { z } from "zod";

const entityId = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const frameCount = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);

export const multitrackEditorTargetSchema = z.object({
  projectId: entityId,
  editProjectId: entityId,
}).strict();

export const multitrackEditorEntrySchema = z.object({
  target: multitrackEditorTargetSchema,
  origin: z.discriminatedUnion("kind", [
    z.object({ kind: z.literal("episode"), episodeId: entityId }).strict(),
    z.object({
      kind: z.literal("canvas"),
      canvasId: entityId,
      nodeId: z.string().trim().min(1).max(128),
    }).strict(),
  ]),
}).strict();

// Entry summaries contain metadata only; media and the timeline load on opening.
export const multitrackEditorSummarySchema = z.object({
  target: multitrackEditorTargetSchema,
  title: z.string().trim().min(1).max(200),
  revision: frameCount,
  durationFrames: frameCount,
  frameRate: z.union([z.literal(24), z.literal(30)]),
  clipCount: frameCount,
}).strict();

export type MultitrackEditorTarget = z.infer<typeof multitrackEditorTargetSchema>;
export type MultitrackEditorEntry = z.infer<typeof multitrackEditorEntrySchema>;
export type MultitrackEditorSummary = z.infer<typeof multitrackEditorSummarySchema>;

export function multitrackEditorCacheKey(target: MultitrackEditorTarget) {
  const parsed = multitrackEditorTargetSchema.parse(target);
  return ["multitrack-edit-project", parsed.projectId, parsed.editProjectId] as const;
}
