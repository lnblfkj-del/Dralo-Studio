import { useEffect, useState } from "react";
import { parseBoundedInt } from "@/components/creator/BriefCustomNumber";
import type { StoryUnderstanding } from "./creativeOutlineModel";

export function PlannedDurationInput({
  number,
  value,
  disabled,
  onChange,
}: {
  number: number;
  value: number | null | undefined;
  disabled: boolean;
  onChange: (next: number | null) => void;
}) {
  const [draft, setDraft] = useState(value ? String(value) : "");
  useEffect(() => {
    setDraft(value ? String(value) : "");
  }, [value]);
  return (
    <label>
      规划时长
      <span>
        <input
          disabled={disabled}
          inputMode="numeric"
          aria-label={`第 ${number} 集规划时长`}
          value={draft}
          onChange={(event) => {
            const next = event.target.value;
            setDraft(next);
            // 空串代表“未规划”，半截输入不能当成有效值。
            if (next.trim() === "") {
              onChange(null);
              return;
            }
            const parsed = parseBoundedInt(next, 1, 3600);
            if (parsed !== null) onChange(parsed);
          }}
          onBlur={() => setDraft(value ? String(value) : "")}
        />
        <small>秒</small>
      </span>
    </label>
  );
}

export function StoryUnderstandingFields({ value, onChange }: { value: StoryUnderstanding; onChange: (value: StoryUnderstanding) => void }) {
  return <div className="story-understanding-grid">
    <label>题材<input value={value.genre} onChange={(event) => onChange({ ...value, genre: event.target.value })} placeholder="例如：法医重生、都市悬疑" /></label>
    <label>核心冲突<input value={value.conflict} onChange={(event) => onChange({ ...value, conflict: event.target.value })} placeholder="例如：证据链与身份掩盖" /></label>
    <label>主要人物<input value={value.characters} onChange={(event) => onChange({ ...value, characters: event.target.value })} placeholder="例如：重生法医与昔日搭档" /></label>
    <label>基调<input value={value.tone} onChange={(event) => onChange({ ...value, tone: event.target.value })} placeholder="例如：冷峻、克制、复仇" /></label>
  </div>;
}
