import type { JSONContent } from "@tiptap/react";
type Row = Record<string, unknown>;
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter(item => item && typeof item === "object") : [];
export function scriptAssetBindings(refs: Row): Row[] {
  const pinned = rows(refs.asset_bindings);
  const pinnedIds = new Set(pinned.map(item => Number(item.asset_id)));
  return [...rows(refs.asset_references).filter(item => !pinnedIds.has(Number(item.asset_id))).map(item => {
    const material = rows(refs.asset_materials).find(candidate => candidate.asset_id === item.asset_id);
    return { ...item, asset_version_id: null, media_file_id: material?.media_file_id ?? null };
  }), ...pinned];
}
export function withoutAssetMentions(parameters: Row, assetId: number): Row {
  const script = parameters.structured_script as Row | undefined;
  if (!script) return parameters;
  const strip = (node: JSONContent): JSONContent => node.type === "assetMention" && Number(node.attrs?.asset_id) === assetId
    ? { type: "text", text: String(node.attrs?.name ?? "") }
    : { ...node, ...(node.content ? { content: node.content.map(strip) } : {}) };
  const documents = script.document_fields as Record<string, JSONContent> | undefined;
  return { ...parameters, structured_script: { ...script,
    ...(script.editor_document ? { editor_document: strip(script.editor_document as JSONContent) } : {}),
    ...(documents ? { document_fields: Object.fromEntries(Object.entries(documents).map(([key, value]) => [key, strip(value)])) } : {}),
    auto_mentions_migrated: true,
  } };
}

export function replaceAssetMentionVersion(parameters: Row, binding: Row): Row {
  const script = parameters.structured_script as Row | undefined;
  if (!script) return parameters;
  const replace = (node: JSONContent): JSONContent => node.type === "assetMention" && Number(node.attrs?.asset_id) === Number(binding.asset_id)
    ? { ...node, attrs: { ...node.attrs, asset_version_id: binding.asset_version_id ?? null, name: binding.asset_name, media_file_id: binding.media_file_id ?? null } }
    : { ...node, ...(node.content ? { content: node.content.map(replace) } : {}) };
  const documents = script.document_fields as Record<string, JSONContent> | undefined;
  return { ...parameters, structured_script: { ...script,
    ...(script.editor_document ? { editor_document: replace(script.editor_document as JSONContent) } : {}),
    ...(documents ? { document_fields: Object.fromEntries(Object.entries(documents).map(([key, value]) => [key, replace(value)])) } : {}),
  } };
}
