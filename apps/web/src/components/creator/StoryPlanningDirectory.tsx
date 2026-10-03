import { useContext } from "react";
import { createPortal } from "react-dom";
import { BookOpen, Milestone, Plus, Users } from "lucide-react";

import { Button, Tooltip } from "@/components/ui";
import type { StoryBibleCharacter } from "@/types/api";
import { OutlineWorkspaceContext } from "./outlineWorkspaceContext";
import { characterTierLabels, characterTierOrder } from "./storyPlanningFields";

export type StoryPlanningView = "overview" | "characters" | "events";

export function StoryPlanningDirectory({
  view,
  characters,
  selected,
  locked,
  onView,
  onSelectCharacter,
  onAddCharacter,
}: {
  view: StoryPlanningView;
  characters: StoryBibleCharacter[];
  selected: number;
  locked: boolean;
  onView: (view: StoryPlanningView) => void;
  onSelectCharacter: (index: number) => void;
  onAddCharacter: () => void;
}) {
  const context = useContext(OutlineWorkspaceContext);
  const directory = <nav className={`story-planning-directory ${context?.collapsed ? "is-collapsed" : ""}`} aria-label="故事资料目录">
    <header><strong>故事资料</strong><Button controlSize="compact" icon={<Plus size={15} />} disabled={locked || characters.length >= 20} aria-label="新增角色" title="新增角色" onClick={onAddCharacter} /></header>
    <div className="story-planning-directory__views">
      {([
        ["overview", "故事概览", BookOpen, "概览"],
        ["characters", `角色设定 ${characters.length}`, Users, "角色"],
        ["events", "事件脉络", Milestone, "事件"],
      ] as const).map(([key, label, Icon, short]) => <Tooltip key={key} placement="right" content={label}><button type="button" aria-current={view === key ? "page" : undefined} onClick={() => { onView(key); context?.closeMobile(); }}><Icon size={15} /><span>{label}</span><em>{short}</em></button></Tooltip>)}
    </div>
    {view === "characters" && <div className="story-planning-directory__people">{characterTierOrder.map(tier => {
      const members = characters.map((person, index) => ({ person, index })).filter(({ person }) => (person.importance ?? "unclassified") === tier);
      if (!members.length) return null;
      return <section key={tier}><h3>{characterTierLabels[tier]} <span>{members.length}</span></h3>{members.map(({ person, index }) => <Tooltip key={person.character_id || index} placement="right" content={`${person.name || "新角色"} · ${person.role || "待填写身份"}`}><button type="button" aria-current={selected === index ? "page" : undefined} onClick={() => { onSelectCharacter(index); context?.closeMobile(); }}><span>{String(index + 1).padStart(2, "0")}</span><strong>{person.name || "新角色"}</strong><small>{person.role || "待填写身份"}</small></button></Tooltip>)}</section>;
    })}</div>}
  </nav>;
  return context?.host ? createPortal(directory, context.host) : directory;
}
