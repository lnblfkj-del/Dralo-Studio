import type { JSONContent } from "@tiptap/react";
type Row = Record<string, unknown>;
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter(item => item && typeof item === "object") : [];
export function documentPinnedBindings(parameters: Row, bindings: Row[]): Row[] {
  const keys = new Set<string>();
  const walk = (node: JSONContent) => {
    if (node.type === "assetMention" && node.attrs?.asset_version_id != null) keys.add(`${node.attrs.asset_id}:${node.attrs.asset_version_id}`);
    node.content?.forEach(walk);
  };
  const script = parameters.structured_script as Row | undefined;
  if (script?.editor_document) walk(script.editor_document as JSONContent);
  else Object.values((script?.document_fields ?? {}) as Record<string, JSONContent>).forEach(walk);
  return [...new Map(bindings.filter(binding => keys.has(`${binding.asset_id}:${binding.asset_version_id}`))
    .map(binding => [`${binding.asset_id}:${binding.asset_version_id}`, binding])).values()];
}
export function documentAssetBindings(bindings: Row[], choices: Row[]): Row[] {
  return [...choices.filter(choice => !bindings.some(binding => Number(binding.asset_id) === Number(choice.asset_id))),
    ...bindings.map(binding => ({ ...binding, media_file_id: binding.media_file_id ??
      (binding.asset_version_id == null ? choices.find(choice => Number(choice.asset_id) === Number(binding.asset_id))?.media_file_id : null) }))];
}
export function documentAssetReferences(parameters: Row, choices: Row[]): Row[] {
  const ids = new Set<number>();
  const walk = (node: JSONContent) => {
    if (node.type === "assetMention" && node.attrs?.asset_version_id == null) ids.add(Number(node.attrs?.asset_id));
    node.content?.forEach(walk);
  };
  const script = parameters.structured_script as Row | undefined;
  if (script?.editor_document) walk(script.editor_document as JSONContent);
  return choices.filter(choice => ids.has(Number(choice.asset_id))).map(choice => ({
    asset_id: choice.asset_id, asset_name: choice.asset_name, asset_type: choice.asset_type,
    resolved: true, delivery: "text", source_keys: [],
  }));
}
export function scriptAssetBindings(refs: Row): Row[] {
  const pinned = rows(refs.asset_bindings);
  const pinnedIds = new Set(pinned.map(item => Number(item.asset_id)));
  return [...rows(refs.asset_references).filter(item => !pinnedIds.has(Number(item.asset_id))).map(item => {
    const material = rows(refs.asset_materials).find(candidate => candidate.asset_id === item.asset_id);
    return { ...item, asset_version_id: null, media_file_id: material?.media_file_id ?? null };
  }), ...pinned, ...rows(refs.document_asset_bindings).filter(item => !pinned.some(binding => binding.asset_id === item.asset_id && binding.asset_version_id === item.asset_version_id))];
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
