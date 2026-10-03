import { Sparkles } from "lucide-react";

import type { CreativeDirectionProposal } from "@/api/creation";
import { StoryUnderstandingFields } from "@/components/creator/CreativeOutlineFields";
import type { CreativeDirectionBatch, DirectionMode, StoryUnderstanding } from "@/components/creator/creativeOutlineModel";
import { Button } from "@/components/ui";

type StoryPlanningBoardProps = {
  brief: string;
  understanding: StoryUnderstanding;
  onUnderstandingChange: (value: StoryUnderstanding) => void;
  batch?: CreativeDirectionBatch;
  proposalId: string | null;
  onProposal: (proposal: CreativeDirectionProposal) => void;
  selected: DirectionMode;
  onSelected: (value: DirectionMode) => void;
  customDirection: string;
  onCustomDirectionChange: (value: string) => void;
  stale: boolean;
  generating: boolean;
  generateError?: string;
  onGenerate: () => void;
  canSubmit: boolean;
  submitting: boolean;
  onSubmit: () => void;
};

export function StoryPlanningBoard({
  brief,
  understanding,
  onUnderstandingChange,
  batch,
  proposalId,
  onProposal,
  selected,
  onSelected,
  customDirection,
  onCustomDirectionChange,
  stale,
  generating,
  generateError,
  onGenerate,
  canSubmit,
  submitting,
  onSubmit,
}: StoryPlanningBoardProps) {
  return (
    <section className="story-direction-board story-planning-board" aria-label="故事策划">
      <article className="story-field story-planning-brief">
        <span>创作起点</span>
        <h3>原始创意</h3>
        <p>{brief || "尚未填写创意。"}</p>
      </article>

      <article className="story-field story-planning-understanding">
        <div className="story-planning-heading">
          <div>
            <span>可随时修正</span>
            <h3>系统对故事的理解</h3>
          </div>
          {batch && <em>提案 V{batch.version}</em>}
        </div>
        <StoryUnderstandingFields value={understanding} onChange={onUnderstandingChange} />
        {batch?.understanding.audience && (
          <p className="story-planning-audience">目标观众：{batch.understanding.audience}</p>
        )}
      </article>

      <article className="story-field story-planning-proposals">
        <div className="story-planning-heading">
          <div>
            <span>专业策划 Skill · 三案比较</span>
            <h3>候选方案</h3>
          </div>
          <Button className="story-generate-button" variant="primary" controlSize="compact" loadingKind="text" loading={generating} icon={generating ? undefined : <Sparkles size={14} />} onClick={onGenerate}>
            {generating ? "生成中…" : batch ? "重新生成三案" : "生成三个方向"}
          </Button>
        </div>

        {generating && <p className="story-planning-status">正在根据当前理解生成三套差异化方向；已有版本会保留到新结果通过校验。</p>}
        {generateError && <p className="story-planning-error" role="alert">{generateError}</p>}
        {stale && <p className="story-planning-stale">故事理解已改。下方仍是 V{batch?.version} 的候选；可继续查看，但需重新生成后才能采用旧候选。</p>}

        {batch ? (
          <div className="story-proposal-grid">
            {batch.proposals.map((item, index) => (
              <label key={item.id} className={proposalId === item.id ? "selected direction-card" : "direction-card"}>
                <input
                  type="radio"
                  name="direction"
                  value={item.id}
                  checked={proposalId === item.id}
                  onChange={() => onProposal(item)}
                />
                <span>
                  <strong><b>方案 {index + 1}</b>{item.title}</strong>
                  <small><i>故事发动机</i>{item.spine}</small>
                  <small><i>关系推进</i>{item.relationships}</small>
                  <small><i>方案差异</i>{item.difference}</small>
                </span>
              </label>
            ))}
          </div>
        ) : (
          <div className="story-planning-empty">
            <Sparkles size={20} />
            <p>先检查上面的故事理解，再生成三套真正不同的叙事策略。系统不会自动消耗模型额度。</p>
          </div>
        )}

        <div className="story-custom-direction">
          <label className={selected === "recommended" && !proposalId ? "selected direction-card" : "direction-card"}>
            <input type="radio" name="direction" checked={selected === "recommended" && !proposalId} onChange={() => onSelected("recommended")} />
            <span><strong>按原创意直接继续</strong><small>跳过提案比较，由后续 Agent 根据原始创意生成设定。</small></span>
          </label>
          <label className={selected === "custom" && !proposalId ? "selected direction-card" : "direction-card"}>
            <input type="radio" name="direction" checked={selected === "custom" && !proposalId} onChange={() => onSelected("custom")} />
            <span><strong>自定义创作方向</strong><small>明确题材组合、叙事重心或导演意图。</small></span>
          </label>
          {selected === "custom" && !proposalId && (
            <textarea value={customDirection} onChange={(event) => onCustomDirectionChange(event.target.value)} placeholder="请输入创作方向（必填）" maxLength={120} />
          )}
        </div>

        <textarea
          value={understanding.notes}
          onChange={(event) => onUnderstandingChange({ ...understanding, notes: event.target.value })}
          placeholder="补充要求（可选）"
          maxLength={2000}
        />
        <div className="creative-stage-action">
          <div><strong>采用当前创作方向</strong><span>系统会保存提案版本与输入快照，再生成可审阅的故事设定。</span></div>
          <Button variant="primary" controlSize="emphasized" loadingKind="text" loading={submitting} disabled={!canSubmit} icon={submitting ? undefined : <Sparkles size={15} />} onClick={onSubmit}>按此方向生成设定</Button>
        </div>
      </article>
    </section>
  );
}
