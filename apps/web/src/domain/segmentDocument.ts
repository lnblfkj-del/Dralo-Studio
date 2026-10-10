import type { JSONContent } from "@tiptap/react";
type Row = Record<string, unknown>;
const rows = (v: unknown): Row[] => Array.isArray(v) ? v : [];
const visualAssetTypes = new Set(["character", "costume", "scene", "prop"]);
export function withBoundAssetMentions(document: JSONContent, bindings: Row[]): JSONContent {
  const candidates = bindings.filter((item) => item.resolved !== false && visualAssetTypes.has(String(item.asset_type))
    && Number.isSafeInteger(Number(item.asset_id)) && Number.isSafeInteger(Number(item.asset_version_id)) && String(item.asset_name ?? "").trim());
  const unique = candidates.filter((item) => candidates.filter((other) => other.asset_name === item.asset_name).length === 1)
    .sort((a, b) => String(b.asset_name).length - String(a.asset_name).length);
  if (!unique.length) return document;
  const decorate = (node: JSONContent): JSONContent[] => {
    if (node.type === "assetMention" || node.type === "scriptTool") return [node];
    if (node.type === "text" && node.text && !node.text.includes("@")) {
      const result: JSONContent[] = [];
      let cursor = 0;
      while (cursor < node.text.length) {
        const matches = unique.map((binding) => ({ binding, index: node.text!.indexOf(String(binding.asset_name), cursor) }))
          .filter((match) => match.index >= 0).sort((a, b) => a.index - b.index || String(b.binding.asset_name).length - String(a.binding.asset_name).length);
        const match = matches[0];
        if (!match) { result.push({ ...node, text: node.text.slice(cursor) }); break; }
        if (match.index > cursor) result.push({ ...node, text: node.text.slice(cursor, match.index) });
        const binding = match.binding;
        result.push({ type: "assetMention", attrs: { asset_id: binding.asset_id, asset_version_id: binding.asset_version_id,
          name: binding.asset_name, media_file_id: binding.media_file_id, asset_type: binding.asset_type } });
        cursor = match.index + String(binding.asset_name).length;
      }
      return result;
    }
    return [{ ...node, ...(node.content ? { content: node.content.flatMap(decorate) } : {}) }];
  };
  return decorate(document)[0]!;
}
export const cameraMoves = ["固定", "缓慢推进", "缓慢拉远", "横移", "摇镜", "俯仰", "跟拍", "环绕", "升降", "手持"];
export function documentText(node: JSONContent): string {
  if (node.type === "assetMention") return String(node.attrs?.name ?? "");
  if (node.type === "scriptTool") {
    const a = node.attrs ?? {};
    return a.kind === "shot" ? `镜头 · ${a.duration}秒` : a.kind === "dialogue" ? `${a.speaker || "待确认说话人"}（${a.tone || "自然"}）：` : `运镜：${a.value}`;
  }
  return node.text ?? (node.content ?? []).map(documentText).join(node.type === "doc" ? "\n\n" : "");
}
export function scriptDocument(script: Row, shots: { shot_id: number; start_time: number; end_time: number }[], bindings: Row[] = []): JSONContent {
  if (script.editor_document) return script.auto_mentions_migrated ? script.editor_document as JSONContent
    : withBoundAssetMentions(script.editor_document as JSONContent, bindings);
  const content: JSONContent[] = [];
  const docs = (script.document_fields ?? {}) as Record<string, JSONContent>;
  const inline = (text: unknown, label: string): JSONContent[] => docs[label] && documentText(docs[label]!) === String(text ?? "")
    ? (docs[label]!.content ?? []).flatMap((p, i) => [...(i ? [{ type: "hardBreak" }] : []), ...(p.content ?? [])])
    : text ? [{ type: "text", text: String(text) }] : [];
  const line = (text: unknown, label = "") => { if (text) content.push({ type: "paragraph", content: inline(text, label) }); };
  const scene = (script.scene ?? {}) as Row;
  line([scene.name, scene.location, scene.time_of_day].filter(Boolean).join(" · "));
  line(scene.description, "场景描述"); line(script.entry_state, "进入状态");
  for (const [index, camera] of rows(script.camera).entries()) {
    const timing = shots.find((s) => s.shot_id === camera.shot_id);
    content.push({ type: "paragraph", content: [{ type: "scriptTool", attrs: { kind: "shot", id: `source-${camera.shot_id}`, sourceShotId: camera.shot_id, duration: Number(camera.duration) || (timing ? timing.end_time - timing.start_time : 4) } }, { type: "text", text: ` ${camera.shot_size ?? ""} · ${camera.camera_angle ?? ""} ` }, { type: "scriptTool", attrs: { kind: "movement", value: camera.camera_movement || "固定" } }] });
    for (const [i, p] of rows(script.performances).entries()) if (p.shot_id === camera.shot_id) content.push({ type: "paragraph", content: [ ...inline(p.subject, `主体 ${i + 1}`), { type: "text", text: "，" }, ...inline(p.expression, `表情 ${i + 1}`), { type: "text", text: "，" }, ...inline(p.action, `动作 ${i + 1}`) ] });
    for (const [i, d] of rows(script.dialogue).entries()) if (d.shot_id === camera.shot_id) {
      if (Array.isArray(d.stage_directions) && d.stage_directions.length) line(`表演说明（不朗读）：${d.stage_directions.join("；")}`);
      content.push({ type: "paragraph", content: [{ type: "scriptTool", attrs: { kind: "dialogue", speaker: d.speaker || "", tone: d.tone || "自然", confirmed: ["manual", "source"].includes(String(d.speaker_source)), sourceShotId: camera.shot_id, id: `dialogue-${index}-${i}` } }, ...inline(d.text, `对白 ${i + 1}`)] });
    }
    const audio = (script.audio ?? {}) as Row;
    for (const [key, label] of [["music", "配乐"], ["ambience", "环境声"], ["sound_effects", "音效"]]) for (const a of rows(audio[key!]).filter((a) => a.shot_id === camera.shot_id)) line(`${label}：${a.text}`);
  }
  line(script.exit_state, "结束状态");
  return withBoundAssetMentions({ type: "doc", content: content.length ? content : [{ type: "paragraph" }] }, bindings);
}
export function documentShots(doc: JSONContent): JSONContent[] {
  return (doc.content ?? []).flatMap((p) => p.content ?? []).filter((n) => n.type === "scriptTool" && n.attrs?.kind === "shot");
}
export function editedScript(script: Row, doc: JSONContent): Row {
  const total = documentShots(doc).reduce((sum, n) => sum + Number(n.attrs?.duration || 0), 0);
  const cameras = rows(script.camera);
  return { ...script, editor_document: doc, auto_mentions_migrated: true, document_fields: {}, camera: cameras.map((c) => ({ ...c, duration: total / cameras.length })) };
}
export function plainScriptDocument(prompt: string, duration: number, shotIds: number[]): Row {
  return { schema_version: 1, source_shot_ids: shotIds, camera: shotIds.map((shot_id) => ({ shot_id, duration: duration / shotIds.length })), editor_document: { type: "doc", content: [
    { type: "paragraph", content: [{ type: "scriptTool", attrs: { kind: "shot", id: crypto.randomUUID(), sourceShotId: shotIds[0] ?? null, duration } }] },
    ...prompt.split(/\n+/).map((text) => ({ type: "paragraph", content: text ? [{ type: "text", text }] : [] })),
  ] } };
}
