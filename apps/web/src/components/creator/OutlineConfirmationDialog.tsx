import { ArrowRight, FileCheck2, ShieldCheck, TriangleAlert } from "lucide-react";
import { Button, Dialog } from "@/components/ui";
import type { CreationArtifact, EpisodeOutlineContent } from "@/types/api";
import "@/styles/outline-confirmation.css";

type Props = {
  review: CreationArtifact | null;
  working: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => void;
};

export function OutlineConfirmationDialog({ review, working, error, onClose, onConfirm }: Props) {
  const content = review?.content as EpisodeOutlineContent | undefined;
  const changes = content?.confirmation_impact?.changes ?? [];
  const reviewCount = changes.filter(row => row.script_review_required).length;
  return <Dialog open={!!review} size="large" className="outline-confirmation" title="确认大纲同步范围" description="核对分集变更，确认后进入剧本正文。" busy={working} onClose={onClose}
    footer={<><Button disabled={working} onClick={onClose}>返回编辑</Button><Button variant="primary" icon={<ArrowRight size={16} />} loading={working} onClick={onConfirm}>确认同步并进入正文</Button></>}>
    <div className="outline-confirmation__layout">
      <aside className="outline-confirmation__summary">
        <div className="outline-confirmation__eyebrow"><FileCheck2 size={18} />同步预览 <span>R{review?.revision}</span></div>
        <h3>大纲已准备就绪</h3>
        <dl className="outline-confirmation__counts">
          <div><dt>当前分集</dt><dd>{content?.episodes.length ?? 0}<small>集</small></dd></div>
          <div><dt>涉及变更</dt><dd>{changes.length}<small>集</small></dd></div>
        </dl>
        <section><h4>本次同步</h4><p>分集目录、标题、梗概、登场角色、戏剧目标、集尾悬念与规划时长。</p></section>
        <section className="outline-confirmation__preserved"><h4><ShieldCheck size={16} />制作内容保留</h4><p>原正文、场景、资产与制作成果不会重写。删除的分集会归档，恢复后沿用原 ID。</p></section>
        {reviewCount > 0 && <p className="outline-confirmation__warning"><TriangleAlert size={16} /><span>{reviewCount} 集已有正文需要重新核对确认。</span></p>}
      </aside>
      <section className="outline-confirmation__detail" aria-label="分集同步变更">
        <header><h3>分集变更清单</h3><span>{changes.length} 项</span></header>
        <div className="outline-confirmation__list">
          {changes.length ? <table><thead><tr><th scope="col">分集</th><th scope="col">同步内容</th><th scope="col">正文影响</th></tr></thead><tbody>{changes.map(row => <tr key={row.outline_key}>
            <th scope="row"><span className="outline-confirmation__episode">EP {String(row.number).padStart(2, "0")}</span><strong>{row.title}</strong></th>
            <td><div className="outline-confirmation__fields">{row.changes.map((field, index) => <span key={`${field}-${index}`}>{field}</span>)}</div></td>
            <td><span className={row.script_review_required ? "outline-confirmation__review" : "outline-confirmation__muted"}>{row.script_review_required ? "保留 · 待复核" : "无需复核"}</span></td>
          </tr>)}</tbody></table> : <div className="outline-confirmation__empty"><FileCheck2 size={28} /><h4>没有待同步的分集变更</h4><p>确认后可继续编辑剧本正文。</p></div>}
        </div>
        <p className="outline-confirmation__note">预览基于 R{review?.revision}，版本变化会阻止应用。项目有进行中的任务时，不能确认增删排序。</p>
        {error && <p className="outline-confirmation__error" role="alert">{error}</p>}
      </section>
    </div>
  </Dialog>;
}
