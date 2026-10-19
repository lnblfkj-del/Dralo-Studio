import { FlaskConical, MoreHorizontal, Pencil, Settings2, Star, Trash2 } from "lucide-react";
import type { ProviderModel } from "@/types/provider";
import { Button, Dropdown } from "@/components/ui";
import { ModelBrandIcon } from "./BrandIcon";
import { inputNames } from "./videoSupport";

const capabilityLabels: Record<string, string> = {reasoning: '深度思考', text_to_image: '文生图', reference_images: '参考图', speech: '音色配音', music: '音乐生成'};
const types: Record<string, string> = {text: '文本', image: '图片', video: '视频', audio: '音乐', tts: '配音', embedding: '向量'};

export function ModelDefinitionRow({model, providerName, canManage, canDelete, toggling, onToggle, onEdit, onTools, onTest, onDefault, onDelete}: {
  model: ProviderModel; providerName: string; canManage: boolean; canDelete: boolean; toggling: boolean;
  onToggle: () => void; onEdit: () => void; onTest: () => void; onDefault: () => void; onDelete: () => void;
  onTools: () => void;
}) {
  const modes = model.video_config?.specification?.modes;
  const tags = model.api_protocol === 'stepfun_gen' ? ['描述生成参考声线'] : model.model_type === 'video' ? modes?.length ? [...new Set(modes.map(mode => inputNames[mode.input_mode] ?? mode.input_mode))] : ['待完善'] : model.capabilities.map(capability => capabilityLabels[capability]).filter(Boolean);
  return <article className="model-definition-card" data-model-id={model.id}>
    <div className="model-definition-main"><span className="model-brand-mark"><ModelBrandIcon modelId={model.model_id} providerName={providerName} size={25} /></span><div><h3>{model.name}{model.is_default && <em className="default-model"><Star size={11} />默认</em>}</h3><small>{model.model_id}</small><div className="model-capability-tags"><b>{types[model.model_type]}模型</b>{tags.map(tag => <span key={tag}>{tag}</span>)}{model.rate_limit_until && <span>限流冷却中</span>}</div></div></div>
    <div className="model-definition-actions vm-model-actions">
      <label className="vm-model-enabled"><input type="checkbox" role="switch" aria-label={`启用 ${model.name}`} checked={model.enabled} disabled={!canManage || toggling} onChange={onToggle} />{model.enabled ? '已启用' : '已停用'}</label>
      <Button data-model-edit={model.id} disabled={!canManage} icon={<Pencil size={14} />} onClick={onEdit}>编辑</Button>
      <Dropdown trigger={['click']} menu={{items: [
        ...(model.model_type === 'video' ? [{key: 'tools', label: '配置工具', icon: <Settings2 size={14} />, disabled: !canManage, onClick: onTools}] : []),
        {key: 'test', label: model.model_type === 'text' ? '测试' : '测试生成', icon: <FlaskConical size={14} />, disabled: !canManage, onClick: onTest},
        ...(model.model_type === 'text' && !model.is_default ? [{key: 'default', label: '设为默认', icon: <Star size={14} />, disabled: !canManage, onClick: onDefault}] : []),
        {key: 'delete', label: '删除模型', icon: <Trash2 size={14} />, danger: true, disabled: !canDelete, onClick: onDelete},
      ]}}><Button data-model-more={model.id} aria-label={`${model.name}的更多操作`} title="更多操作" icon={<MoreHorizontal size={16} />} /></Dropdown>
    </div>
  </article>;
}
