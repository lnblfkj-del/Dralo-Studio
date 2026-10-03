import type { StoryBibleCharacter } from "@/types/api";
import { characterGroups, characterTierLabels, extraFields } from "./storyPlanningFields";

type Field = typeof characterGroups[number]["fields"][number];

export function StoryCharacterDetails({ person, editing, onChange }: {
  person: StoryBibleCharacter; editing: boolean; onChange: (value: StoryBibleCharacter) => void;
}) {
  const extras = extraFields(person, ["character_id", "aliases", "importance", "narrative_function", "appearance_scope", ...characterGroups.flatMap(group => group.fields.map(([key]) => key))]);
  const field = ([key, label, max]: Field, wide = false) => {
    const value = person[key] ?? "";
    if (!editing && !value) return null;
    return <div className={`story-planning-v2__field${wide ? " is-wide" : ""}`} key={key}>
      {editing ? <label>{label}{key === "name" || key === "role" || key === "age"
        ? <input aria-label={label} required={key === "name" || key === "role"} maxLength={max} value={value} onChange={event => onChange({ ...person, [key]: event.target.value })} />
        : <textarea aria-label={label} rows={3} maxLength={max} value={value} placeholder={key === "voice" ? "音高、质感、语速、口音与表达特点；不确定可留空" : "未说明可留空"} onChange={event => onChange({ ...person, [key]: event.target.value })} />}</label>
        : <><span>{label}</span><p>{value}</p></>}
    </div>;
  };
  const identity = characterGroups[0].fields;
  const biography = identity.slice(3);
  const motivation = characterGroups[1].fields;
  const visual = characterGroups[2].fields;
  const sound = characterGroups[3].fields;
  const hasValue = (fields: readonly Field[]) => fields.some(([key]) => Boolean(person[key]));

  if (!editing) return <div className="story-planning-v2__profile-read">
    <div className="story-planning-v2__overview-read">
      <div className="story-planning-v2__profile-read-main">
        {(person.narrative_function || hasValue(biography)) && <section>
          <h3>人物与叙事</h3>
          <div className="story-planning-v2__profile-grid">
            {person.narrative_function && <div className="story-planning-v2__field"><span>叙事职责</span><p>{person.narrative_function}</p></div>}
            {field(identity[3])}
          </div>
        </section>}
        {hasValue(motivation) && <section>
          <h3>目标与成长</h3>
          <div className="story-planning-v2__profile-grid">{motivation.map(item => field(item))}</div>
        </section>}
      </div>
      <aside><dl>
        <dt>角色层级</dt><dd>{characterTierLabels[person.importance ?? "unclassified"]}</dd>
        {person.age && <><dt>年龄 / 年龄段</dt><dd>{person.age}</dd></>}
        <dt>出场范围</dt><dd>{person.appearance_scope || "未设定"}</dd>
      </dl></aside>
    </div>
    {(person.personality || hasValue(visual) || hasValue(sound) || person.aliases?.some(Boolean) || extras.length > 0) && <details className="story-planning-v2__profile-extra">
      <summary>更多角色资料</summary>
      <div className="story-planning-v2__profile-grid">
        {field(identity[4], true)}
        {visual.map(item => field(item))}
        {sound.map(item => field(item, true))}
        {person.aliases?.some(Boolean) && <div className="story-planning-v2__field is-wide"><span>别名</span><p>{person.aliases.filter(Boolean).join("、")}</p></div>}
      </div>
      {extras.map(([key, value]) => <div className="story-planning-v2__field" key={key}><span>{key}</span><p>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</p></div>)}
      <p className="story-planning-v2__hint">故事角色是叙事规划资料，不代表已进入制作资产库。只有在最终剧本中实际出现且需要视觉一致性的角色，才会在资产拆解后进入候选资产。</p>
    </details>}
  </div>;

  return <div className="story-planning-v2__profile is-editing">
    <section className="story-planning-v2__profile-section">
      <h3>基本资料</h3>
      <div className="story-planning-v2__profile-grid is-identity">
        {identity.slice(0, 2).map(item => field(item))}
        <div className="story-planning-v2__field"><label>角色层级<select aria-label="角色层级" value={person.importance ?? ""} onChange={event => onChange({ ...person, importance: (event.target.value || null) as StoryBibleCharacter["importance"] })}><option value="">未分层</option>{Object.entries(characterTierLabels).filter(([key]) => key !== "unclassified").map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
        {identity[2] && field(identity[2])}
        <div className="story-planning-v2__field is-wide"><label>出场范围<textarea aria-label="出场范围" rows={2} maxLength={500} value={person.appearance_scope ?? ""} onChange={event => onChange({ ...person, appearance_scope: event.target.value })} placeholder="例如：全季常驻 / 第 21-35 集阶段登场 / 单集临时角色" /></label></div>
      </div>
    </section>
    <section className="story-planning-v2__profile-section">
      <h3>剧情设定</h3>
      <div className="story-planning-v2__profile-grid">
        <div className="story-planning-v2__field"><label>叙事职责<textarea aria-label="叙事职责" rows={3} maxLength={500} value={person.narrative_function ?? ""} onChange={event => onChange({ ...person, narrative_function: event.target.value })} placeholder="例如：持续制造职业冲突，并在中段迫使主角改变策略" /></label></div>
        {biography[0] && field(biography[0])}
        {motivation.map(item => field(item))}
      </div>
    </section>
    <details className="story-planning-v2__profile-extra"><summary>性格、造型与声音</summary>
      <div className="story-planning-v2__profile-grid">
        {biography[1] && field(biography[1])}
        {visual.map(item => field(item))}
        {sound.map(item => field(item))}
        <div className="story-planning-v2__field is-wide"><label>别名（每行一个）<textarea aria-label="别名" rows={2} value={(person.aliases ?? []).join("\n")} onChange={event => onChange({ ...person, aliases: event.target.value.split("\n") })} /></label></div>
      </div>
    </details>
    {extras.length > 0 && <details className="story-planning-v2__profile-extra"><summary>其他原有角色资料</summary>{extras.map(([key, value]) => <div key={key}><strong>{key}</strong><p>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</p></div>)}</details>}
  </div>;
}
