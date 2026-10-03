import { useContext, useState, type ComponentProps } from "react";
import { AgentActionPreviewCard } from "@/components/agent/AgentActionPreviewCard";
import { OutlineWorkspaceContext } from "./outlineWorkspaceContext";
import { Button, Dialog } from "@/components/ui";
import type { EpisodeOutlineContent } from "@/types/api";
import "@/styles/episode-outline-editor.css";

/** Flush local edits before applying a proposal; server then checks its baseline revision. */
export function OutlineAgentPreview(props: ComponentProps<typeof AgentActionPreviewCard>) {
  const context = useContext(OutlineWorkspaceContext);
  const [saving, setSaving] = useState(false);
  const [open, setOpen] = useState(false);
  const before = ((props.current ?? {}) as unknown as EpisodeOutlineContent).episodes ?? [];
  const after = (props.preview.proposed as unknown as EpisodeOutlineContent | undefined)?.episodes ?? [];
  const apply = async (selectedCharacterKeys?: string[]) => {
    setSaving(true);
    try { if (context?.beforeLeave.current && !await context.beforeLeave.current()) return; setOpen(false); props.onApply(selectedCharacterKeys); }
    finally { setSaving(false); }
  };
  return <><AgentActionPreviewCard {...props} busy={props.busy || saving} onApply={selectedCharacterKeys => { if (props.preview.target_type === "episode_outline") setOpen(true); else void apply(selectedCharacterKeys); }} />
    {props.preview.target_type === "episode_outline" && <Button onClick={() => setOpen(true)}>查看完整修改对照</Button>}
    <Dialog open={open} title="全剧大纲优化对照" size="large" busy={saving || props.busy} onClose={() => setOpen(false)} footer={<><Button onClick={() => setOpen(false)}>返回</Button><Button variant="primary" disabled={props.preview.status !== "pending" || props.busy} loading={saving} onClick={() => { void apply(); }}>确认应用 AI 提案</Button></>}>
      <p>应用后生成新草稿版本，正式正文不会直接修改。请核对梗概与登场角色是否一致；梗概有修改时采用 AI 纯文本，原富文本保留在历史版本。</p>
      <p>共 {after.length} 集 · 梗概调整 {after.filter(row => row.synopsis !== before.find(item => item.outline_key ? item.outline_key === row.outline_key : item.number === row.number)?.synopsis).length} 集 · 角色调整 {after.filter(row => JSON.stringify(row.characters ?? []) !== JSON.stringify(before.find(item => item.outline_key ? item.outline_key === row.outline_key : item.number === row.number)?.characters ?? [])).length} 集</p>
      <div className="outline-proposal-comparison">{after.map(row => { const old = before.find(item => row.outline_key ? item.outline_key === row.outline_key : item.number === row.number) ?? before.find(item => !item.outline_key && item.number === row.number); return <section key={row.outline_key ?? row.number}><h3>第 {row.number} 集 · {row.title}</h3><div><article><h4>当前内容</h4><strong>{old?.title ?? "暂无"}</strong><p>{old?.synopsis ?? "暂无梗概"}</p><p>登场角色：{old?.characters?.join("、") || "未规划"}</p><p>戏剧目标：{old?.dramatic_goal}</p><p>集尾钩子：{old?.cliffhanger}</p></article><article><h4>AI 建议</h4><strong>{row.title}</strong><p>{row.synopsis}</p><p>登场角色：{row.characters?.join("、") || "未规划"}</p><p>戏剧目标：{row.dramatic_goal}</p><p>集尾钩子：{row.cliffhanger}</p></article></div></section>; })}</div>
    </Dialog></>;
}
