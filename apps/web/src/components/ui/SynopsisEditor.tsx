import { useEffect } from "react";
import { EditorContent, useEditor, type JSONContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Slice } from "@tiptap/pm/model";
import { Bold, Italic, List, ListOrdered, Undo2, Redo2 } from "lucide-react";
import { Button } from "./Button";
import "./synopsis-editor.css";

export function synopsisText(node: JSONContent): string {
  if (node.type === "text") return node.text ?? "";
  if (node.type === "hardBreak") return "\n";
  return (node.content ?? []).map(synopsisText).join(node.type === "paragraph" ? "" : "\n\n");
}
export function synopsisDocument(text: string): JSONContent {
  return { type: "doc", content: text.split("\n\n").map(paragraph => ({ type: "paragraph", content: paragraph.split("\n").flatMap((line, index) => [...(index ? [{ type: "hardBreak" }] : []), ...(line ? [{ type: "text", text: line }] : [])]) })) };
}

export function SynopsisEditor({ value, document, disabled, onChange }: { value: string; document?: JSONContent | null; disabled: boolean; onChange: (text: string, document: JSONContent) => void }) {
  const editor = useEditor({
    extensions: [StarterKit.configure({ blockquote: false, code: false, codeBlock: false, heading: false, horizontalRule: false, link: false, strike: false, underline: false })],
    content: document ?? synopsisDocument(value),
    editable: !disabled,
    editorProps: { attributes: { role: "textbox", "aria-label": "本集梗概", "aria-multiline": "true" },
      handlePaste: (view, event) => {
        // 外部粘贴仅保留文本和换行，避免带入网页字号、颜色和隐藏内容。
        const text = event.clipboardData?.getData("text/plain");
        if (text === undefined) return false;
        event.preventDefault();
        const slice = view.state.schema.nodeFromJSON(synopsisDocument(text));
        view.dispatch(view.state.tr.replaceSelection(new Slice(slice.content, 0, 0)));
        return true;
      },
      handleDrop: (_view, event) => { event.preventDefault(); return true; },
    },
    onUpdate: ({ editor: instance }) => { const json = instance.getJSON(); onChange(synopsisText(json), json); },
  });
  useEffect(() => { editor?.setEditable(!disabled, false); }, [editor, disabled]);
  if (!editor) return <p>正在加载编辑器…</p>;
  const controls = [
    { label: "加粗", icon: <Bold size={16} />, run: () => editor.chain().focus().toggleBold().run() },
    { label: "斜体", icon: <Italic size={16} />, run: () => editor.chain().focus().toggleItalic().run() },
    { label: "项目列表", icon: <List size={16} />, run: () => editor.chain().focus().toggleBulletList().run() },
    { label: "编号列表", icon: <ListOrdered size={16} />, run: () => editor.chain().focus().toggleOrderedList().run() },
    { label: "撤销文字修改", icon: <Undo2 size={16} />, run: () => editor.chain().focus().undo().run() },
    { label: "重做文字修改", icon: <Redo2 size={16} />, run: () => editor.chain().focus().redo().run() },
  ];
  return <div className="ui-synopsis"><div className="ui-synopsis__toolbar" role="toolbar" aria-label="梗概文字格式">{controls.map(control => <Button key={control.label} variant="text" disabled={disabled} title={control.label} aria-label={control.label} icon={control.icon} onMouseDown={event => event.preventDefault()} onClick={control.run} />)}</div><EditorContent editor={editor} /><footer><span>{value.length} / 20000 字符</span><span>支持段落、加粗与列表</span></footer></div>;
}
