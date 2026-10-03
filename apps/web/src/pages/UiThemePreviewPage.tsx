import { useState } from "react";
import { Save, Settings2, Trash2 } from "lucide-react";
import { TextGenerationLoading } from "@/components/ui/TextGenerationLoading";
import { AssetExtractionProgress } from "@/components/creator/AssetExtractionProgress";

import {
  Button,
  ConfirmDialog,
  Dialog,
  FeedbackBoundary,
  IconButton,
  SelectField,
  TextField,
  Tooltip,
  useFeedback,
} from "@/components/ui";
import "@/styles/ui-theme-preview.css";

/** UI-M2 集中验收入口；仅开发环境注册，不代表业务页面已经迁移。 */
export function UiThemePreviewPage() {
  return <FeedbackBoundary><ComponentShowcase /></FeedbackBoundary>;
}

function ComponentShowcase() {
  const [dialog, setDialog] = useState<"clean" | "dirty" | "confirm" | null>(null);
  const feedback = useFeedback();

  return (
    <main className="ui-theme-preview">
      <header className="ui-theme-preview__heading">
        <span className="ui-theme-preview__eyebrow">UI SYSTEM · M2</span>
        <h1>公共组件标准展示</h1>
        <p>集中检查正常、悬停、焦点、加载、禁用、错误、长文本、空数据与小屏状态。</p>
      </header>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section__title"><span>01</span><div><h2>按钮与操作</h2><p>操作语义由组件决定，不允许页面临时改成黑色悬停。</p></div></div>
        <div className="ui-showcase-row">
          <Button variant="primary" icon={<Save size={16} />}>保存内容</Button>
          <Button>次要操作</Button>
          <Button variant="text">文字操作</Button>
          <Button variant="danger" icon={<Trash2 size={16} />}>删除</Button>
          <Button loading>处理中</Button>
          <Button variant="primary" loading loadingKind="text">正在生成新方案</Button>
          <Button disabled>不可操作</Button>
          <IconButton label="页面设置" icon={<Settings2 size={17} />} />
          <Tooltip content="Tooltip 仅补充说明，按钮本身仍有文字"><Button controlSize="compact">悬停说明</Button></Tooltip>
        </div>
        <div className="ui-showcase-row">
          <Button controlSize="compact">紧凑 32</Button>
          <Button controlSize="default">默认 36</Button>
          <Button controlSize="emphasized" variant="primary">强调 40</Button>
        </div>
      </section>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section__title"><span>AI</span><div><h2>文字生成状态</h2></div></div>
        <TextGenerationLoading label="正在生成剧本正文" quip />
        <AssetExtractionProgress completed={6} total={30} />
      </section>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section__title"><span>02</span><div><h2>输入与选择</h2><p>标签、帮助文字、错误状态和控件高度遵守同一契约。</p></div></div>
        <div className="ui-showcase-fields">
          <TextField label="项目名称" required placeholder="输入项目名称" help="最多 50 个字符" maxLength={50} />
          <SelectField label="默认模型" defaultValue="main" options={[{ value: "main", label: "主创作模型" }, { value: "backup", label: "备用模型" }]} />
          <TextField label="渠道地址" defaultValue="格式不正确" error="请输入有效的 HTTPS 地址" />
          <TextField label="禁用状态" defaultValue="当前不可修改" disabled />
          <TextField label="长文本内容" multiline rows={4} placeholder="用于检查多行文字、中文标点、较长说明和自动换行是否仍然清楚可读。" />
          <SelectField label="空数据状态" placeholder="暂无可选模型" options={[]} help="配置模型渠道后即可选择" />
        </div>
      </section>

      <section className="ui-showcase-section">
        <div className="ui-showcase-section__title"><span>03</span><div><h2>反馈与浮层</h2><p>Message、Notification 与 Dialog 全部继承全局主题和 Portal 行为。</p></div></div>
        <div className="ui-showcase-row">
          <Button variant="primary" onClick={() => feedback.success("内容已保存")}>成功反馈</Button>
          <Button onClick={() => feedback.warning("仍有字段需要确认")}>警告反馈</Button>
          <Button onClick={() => feedback.error("保存失败，请检查渠道连接")}>错误反馈</Button>
          <Button onClick={() => feedback.notify("后台任务已开始", "可在任务中心查看进度")}>通知反馈</Button>
          <Button onClick={() => setDialog("clean")}>普通弹窗</Button>
          <Button onClick={() => setDialog("dirty")}>脏数据弹窗</Button>
          <Button variant="danger" onClick={() => setDialog("confirm")}>危险确认</Button>
        </div>
      </section>

      <Dialog
        open={dialog === "clean" || dialog === "dirty"}
        title={dialog === "dirty" ? "编辑未保存内容" : "公共弹窗验收"}
        description="标题、关闭按钮、正文和操作区保持紧凑并可独立滚动。"
        dirty={dialog === "dirty"}
        onClose={() => setDialog(null)}
        footer={requestClose => <><Button onClick={requestClose}>取消</Button><Button variant="primary" onClick={() => setDialog(null)}>保存</Button></>}
      >
        <div className="ui-showcase-dialog-content">
          <TextField label="名称" defaultValue={dialog === "dirty" ? "尚未保存的修改" : "示例内容"} />
          <TextField label="说明" multiline rows={5} defaultValue="弹窗宽度按内容选择，并受视口限制。关闭后焦点应回到触发按钮；存在未保存内容时，需要二次确认。" />
        </div>
      </Dialog>

      <ConfirmDialog
        open={dialog === "confirm"}
        title="删除这项配置？"
        message="删除后无法从页面恢复，请确认当前配置不再使用。"
        confirmLabel="确认删除"
        danger
        onClose={() => setDialog(null)}
        onConfirm={() => { setDialog(null); feedback.success("示例配置已删除"); }}
      />
    </main>
  );
}

export default UiThemePreviewPage;
