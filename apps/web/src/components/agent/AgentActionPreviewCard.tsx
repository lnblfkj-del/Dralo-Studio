import { useEffect, useState } from "react";
import { Check, GitCompareArrows, LoaderCircle, X } from "lucide-react";

import { Button } from "@/components/ui";
import type { AgentActionPreview, EpisodeOutlineContent, StoryBibleContent } from "@/types/api";
import { characterGroups, characterLabels, characterTierLabels } from "@/components/creator/storyPlanningFields";
import "@/styles/agent-action-preview.css";
import "@/styles/agent-character-batch.css";

type Props = {
  preview: AgentActionPreview;
  current?: Record<string, unknown> | null;
  busy?: boolean;
  onApply: (selectedCharacterKeys?: string[]) => void;
  onReject: () => void;
};

const text = (value: unknown) => typeof value === "string" ? value.trim() : "";
const excerpt = (value: unknown, size = 180) => {
  const normalized = text(value);
  return normalized.length > size ? `${normalized.slice(0, size)}…` : normalized || "—";
};

function ScriptDiff({ preview }: { preview: AgentActionPreview }) {
  const before = preview.source;
  const after = preview.proposed;
  const beforeScript = text(before.script);
  const afterScript = text(after.script);
  return (
    <div className="agent-action-diff">
      <div><span>标题</span><del>{before.title || "未命名"}</del><ins>{excerpt(after.title, 80)}</ins></div>
      <div><span>本集梗概</span><del>{excerpt(before.synopsis)}</del><ins>{excerpt(after.synopsis)}</ins></div>
      <div className="long"><span>正文变化</span><del>{excerpt(beforeScript)}</del><ins>{excerpt(afterScript)}</ins><small>{beforeScript.length.toLocaleString()} → {afterScript.length.toLocaleString()} 字</small></div>
    </div>
  );
}

function StoryDiff({ preview, current }: { preview: AgentActionPreview; current?: Record<string, unknown> | null }) {
  const after = preview.proposed as unknown as StoryBibleContent;
  const before = (current ?? {}) as unknown as Partial<StoryBibleContent>;
  const rows = [
    ["故事名称", before.title, after.title],
    ["一句话梗概", before.logline, after.logline],
    ["类型与基调", [before.genre, before.tone].filter(Boolean).join(" · "), [after.genre, after.tone].filter(Boolean).join(" · ")],
    ["目标受众", before.audience, after.audience],
    ["世界设定", before.world, after.world],
    ["核心主题", before.themes?.join("、"), after.themes?.join("、")],
  ] as const;
  if (preview.story_section === "events") {
    const previousEvents = before.event_timeline ?? [];
    const nextEvents = after.event_timeline ?? [];
    return (
      <div className="agent-action-diff">
        <small>事件脉络 {previousEvents.length} 条 → {nextEvents.length} 条</small>
        {nextEvents.map((event, index) => {
          const previous = previousEvents.find(item => (
            typeof event.episode_hint === "number" && item.episode_hint === event.episode_hint
          )) ?? previousEvents[index];
          const label = typeof event.episode_hint === "number" ? `第 ${event.episode_hint} 集` : `事件 ${index + 1}`;
          return (
            <div className="long" key={`${event.episode_hint ?? "event"}-${index}`}>
              <span>{label}</span>
              <del>{previous ? `${previous.title || "未命名"}：${previous.summary || "未说明"}` : "无对应旧事件"}</del>
              <ins>{`${event.title || "未命名"}：${event.summary || "未说明"}`}</ins>
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <div className="agent-action-diff">
      {!preview.character_batch && rows.map(([label, oldValue, newValue]) => (
        <div key={label}><span>{label}</span><del>{excerpt(oldValue)}</del><ins>{excerpt(newValue)}</ins></div>
      ))}
      {preview.story_section !== "overview" && <small>角色 {after.characters?.length ?? 0} 位 · 关键事件 {after.event_timeline?.length ?? 0} 条</small>}
      {preview.story_section !== "overview" && (after.characters ?? []).map((person, index) => {
        const old = before.characters?.find(item => person.character_id ? item.character_id === person.character_id : item.name === person.name);
        const ecosystemFields = [
          ["importance", "角色层级"], ["narrative_function", "叙事职责"], ["appearance_scope", "出场范围"],
        ] as const;
        return <section key={person.character_id || index} aria-label={`${person.name}角色修改对照`}><strong>{person.name}{!old ? " · 新增角色" : ""}</strong>
          {ecosystemFields.filter(([key]) => !old || (old[key] ?? "") !== (person[key] ?? "")).map(([key, label]) => <div className="long" key={key}><span>{label}</span><del>{key === "importance" ? characterTierLabels[old?.importance ?? "unclassified"] : old?.[key] || "未说明"}</del><ins>{key === "importance" ? characterTierLabels[person.importance ?? "unclassified"] : person[key] || "未说明"}</ins></div>)}
          {characterGroups.flatMap(group => group.fields.map(([key, label]) => ({ key, label }))).filter(({ key }) => (old?.[key] ?? "") !== (person[key] ?? "")).map(({ key, label }) => <div className="long" key={key}><span>{label}</span><del style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{old?.[key] || "未说明"}</del><ins style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{person[key] || "未说明"}</ins></div>)}
        </section>;
      })}
    </div>
  );
}

function OutlineDiff({ preview, current }: { preview: AgentActionPreview; current?: Record<string, unknown> | null }) {
  const after = (preview.proposed as unknown as EpisodeOutlineContent).episodes ?? [];
  const before = ((current ?? {}) as unknown as Partial<EpisodeOutlineContent>).episodes ?? [];
  const changes = after.filter((item, index) => {
    const old = before[index];
    return !old || old.title !== item.title || old.synopsis !== item.synopsis || old.dramatic_goal !== item.dramatic_goal || old.cliffhanger !== item.cliffhanger || JSON.stringify(old.characters ?? []) !== JSON.stringify(item.characters ?? []);
  });
  return (
    <div className="agent-outline-diff">
      <p>{before.length || 0} 集 → {after.length} 集 · {changes.length} 集有调整</p>
      {changes.slice(0, 6).map((item) => {
        const old = before[item.number - 1];
        const castChanged = JSON.stringify(old?.characters ?? []) !== JSON.stringify(item.characters ?? []);
        return <span key={item.number}><b>EP {String(item.number).padStart(2, "0")}</b>{item.title}{castChanged && <small>角色：{old?.characters?.join("、") || "未规划"} → {item.characters?.join("、") || "未规划"}</small>}</span>;
      })}
      {changes.length > 6 && <small>另有 {changes.length - 6} 集调整</small>}
    </div>
  );
}

function CanvasTextDiff({ preview }: { preview: AgentActionPreview }) {
  const operation = text(preview.proposed.operation);
  return (
    <div className="agent-action-diff">
      <div>
        <span>{operation === "update" ? "更新节点" : "新增节点"}</span>
        <del>{operation === "update" ? excerpt(preview.source.content) : "当前画布不变"}</del>
        <ins>{excerpt(preview.proposed.content, 260)}</ins>
      </div>
    </div>
  );
}

type AssetCandidatePreview = {
  candidate_id?: number;
  name?: string;
  asset_type?: string;
  episode_numbers?: number[];
  matched_asset_id?: number | null;
};

function AssetBreakdownDiff({ preview }: { preview: AgentActionPreview }) {
  const candidates = Array.isArray(preview.proposed.candidates)
    ? preview.proposed.candidates as AssetCandidatePreview[]
    : [];
  const labels: Record<string, string> = { character: "角色", scene: "场景", prop: "道具" };
  return (
    <div className="agent-asset-diff">
      {candidates.slice(0, 12).map((candidate, index) => (
        <span key={candidate.candidate_id ?? `${candidate.asset_type}-${candidate.name}-${index}`}>
          <b>{labels[candidate.asset_type ?? ""] ?? candidate.asset_type ?? "资产"}</b>
          {candidate.name || "未命名"}
          <small>
            出现于 {candidate.episode_numbers?.join("、") || "—"} 集
            {candidate.matched_asset_id ? " · 已匹配资产库" : ""}
          </small>
        </span>
      ))}
      {candidates.length > 12 && <small>另有 {candidates.length - 12} 项候选资产</small>}
    </div>
  );
}

function CharacterBatchReview({ preview, current, busy, onApply, onReject }: Props) {
  const batch = preview.character_batch!;
  const available = batch.items.filter(item => item.changed_fields.length > 0);
  const settled = preview.status !== "pending";
  const [selectedKeys, setSelectedKeys] = useState<string[]>(() => batch.selected_character_keys ?? available.map(item => item.character_key));
  const [focusedKey, setFocusedKey] = useState(() => available[0]?.character_key ?? batch.items[0]?.character_key ?? "");
  useEffect(() => {
    setSelectedKeys(batch.selected_character_keys ?? available.map(item => item.character_key));
    setFocusedKey(available[0]?.character_key ?? batch.items[0]?.character_key ?? "");
  }, [preview.trace.job_id, preview.status]);
  const focused = batch.items.find(item => item.character_key === focusedKey) ?? batch.items[0];
  const proposed = ((preview.proposed as unknown as Partial<StoryBibleContent>).characters ?? []);
  const before = (((current ?? {}) as unknown as Partial<StoryBibleContent>).characters ?? []);
  const findCharacter = (people: StoryBibleContent["characters"]) => people.find(person =>
    focused?.character_key.startsWith("id:")
      ? person.character_id === focused.character_key.slice(3)
      : person.name === focused?.name,
  );
  const nextPerson = findCharacter(proposed);
  const oldPerson = findCharacter(before);
  const selectedCount = available.filter(item => selectedKeys.includes(item.character_key)).length;
  const changedCount = available.reduce((total, item) => total + item.changed_fields.length, 0);
  const status = (item: typeof batch.items[number]) => item.status === "complete"
    ? `补全 ${item.changed_fields.length} 项`
    : item.status === "partial"
      ? `补全 ${item.changed_fields.length} 项 · 仍缺 ${item.missing_fields.length} 项`
      : item.status === "excluded" ? "未采用" : "无有效补全";

  return <section className="agent-character-review" aria-label="逐角色审核">
    <div className="agent-character-review__summary">
      <span>{available.length} 位角色可采用 · {changedCount} 项补全</span>
      <span>{preview.status === "pending" ? "待审核" : preview.status === "applied" ? "已应用" : preview.status === "rejected" ? "已放弃" : "已过期"}</span>
    </div>
    <div className="agent-character-review__main">
      <nav className="agent-character-review__nav" aria-label="角色列表">
        {batch.items.map((item, index) => {
          const applicable = item.changed_fields.length > 0;
          return <div className="agent-character-review__nav-row" data-active={focused?.character_key === item.character_key} key={item.character_key}>
            <input type="checkbox" aria-label={`采用${item.name}的补全`} disabled={!applicable || settled || busy} checked={applicable && selectedKeys.includes(item.character_key)} onChange={event => setSelectedKeys(event.target.checked ? [...selectedKeys, item.character_key] : selectedKeys.filter(key => key !== item.character_key))} />
            <button type="button" aria-current={focused?.character_key === item.character_key ? "true" : undefined} onClick={() => setFocusedKey(item.character_key)}>
              <span className="agent-character-review__index">{String(index + 1).padStart(2, "0")}</span>
              <span><strong>{item.name}</strong><small>{status(item)}</small></span>
            </button>
          </div>;
        })}
      </nav>
      <div className="agent-character-review__detail">
        {focused && <>
          <header><div><span>角色补全建议</span><h3>{focused.name}</h3></div><small>{status(focused)}</small></header>
          {focused.changed_fields.length ? <dl>
            {focused.changed_fields.map(field => {
              const oldValue = oldPerson?.[field as keyof typeof oldPerson];
              const newValue = nextPerson?.[field as keyof typeof nextPerson];
              return <div key={field}><dt>{characterLabels[field] ?? field}</dt><dd>{oldValue != null && String(oldValue).trim() && <small>原内容：{String(oldValue)}</small>}<p>{newValue == null || String(newValue).trim() === "" ? "未返回内容" : String(newValue)}</p></dd></div>;
            })}
          </dl> : <p className="agent-character-review__empty">模型没有返回可采用的补全内容。</p>}
          {focused.missing_fields.length > 0 && <p className="agent-character-review__missing">仍未补全：{focused.missing_fields.map(field => characterLabels[field] ?? field).join("、")}</p>}
        </>}
      </div>
    </div>
    <footer><small>{preview.trace.model || "未记录模型"}{preview.source.revision !== undefined ? ` · 源 R${preview.source.revision}` : ""}</small>
      {!settled && <div><span>已选 {selectedCount} / {available.length} 位</span><Button disabled={busy} onClick={onReject}>放弃方案</Button><Button variant="primary" disabled={busy || !selectedCount} loading={busy} onClick={() => onApply(selectedKeys)}>采用所选角色</Button></div>}
    </footer>
  </section>;
}

export function AgentActionPreviewCard({ preview, current, busy, onApply, onReject }: Props) {
  if (preview.character_batch) return <CharacterBatchReview preview={preview} current={current} busy={busy} onApply={onApply} onReject={onReject} />;
  const settled = preview.status !== "pending";
  return (
    <section className={`agent-action-card ${preview.status}`} aria-label="Agent 变更提案">
      <header>
        <div><GitCompareArrows size={17} /><span>待审阅变更</span></div>
        <em>{preview.status === "pending" ? "未写入" : preview.status === "applied" ? "已应用" : preview.status === "rejected" ? "已放弃" : "已过期"}</em>
      </header>
      <h3>{preview.title}</h3>
      <p>{preview.summary}</p>
      {preview.media_estimates?.map((item, index) => <p key={`${item.node_id}-${index}`}>{item.node_id} · {item.model} · {item.pricing_estimate?.amount != null ? `预估 ${item.pricing_estimate.currency} ${item.pricing_estimate.amount}` : item.estimated_cents === null ? "费用未知，请先核对渠道定价" : `预估 ${item.estimated_cents} 分（人民币）`}</p>)}
      {preview.target_type === "episode_script" && <ScriptDiff preview={preview} />}
      {preview.target_type === "story_bible" && <StoryDiff preview={preview} current={current} />}
      {preview.target_type === "episode_outline" && <OutlineDiff preview={preview} current={current} />}
      {preview.target_type === "canvas_text_node" && <CanvasTextDiff preview={preview} />}
      {preview.target_type === "canvas_production" && <pre style={{ whiteSpace: "pre-wrap", maxHeight: 300, overflow: "auto" }}>{JSON.stringify(preview.proposed.operations, null, 2)}</pre>}
      {preview.target_type === "asset_breakdown" && <AssetBreakdownDiff preview={preview} />}
      <footer>
        <small>
          {preview.trace.model || "未记录模型"}
          {preview.trace.skill_key ? ` · Skill ${preview.trace.skill_key}` : ""}
          {preview.source.revision !== undefined
            ? ` · 源 R${preview.source.revision}`
            : preview.source.version !== undefined
              ? ` · 源 V${preview.source.version}`
              : ""}
        </small>
        {!settled && <div>
          <Button controlSize="compact" disabled={busy} icon={<X size={14} />} onClick={onReject}>放弃</Button>
          <Button
            className="primary"
            controlSize="compact"
            variant="primary"
            disabled={busy}
            icon={busy ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}
            onClick={() => onApply()}
          >确认应用</Button>
        </div>}
      </footer>
    </section>
  );
}
