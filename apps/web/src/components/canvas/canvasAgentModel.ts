export function inferCanvasTextAction(prompt: string): "chat" | "text_node" {
  const normalized = prompt.replace(/\s+/g, "");
  if (/(然后|接着|再创建|多步骤|角色节点|场景节点|图片节点|视频节点|组合|排列|导演台)/.test(normalized)) return "chat";
  const asksForTextNode =
    /(创建|新建|生成|添加).{0,8}(文本|文案|台词|提示词).{0,4}节点/.test(normalized) ||
    /(把|将).{0,40}(添加|放到|放入|写入).{0,8}(画布|文本节点)/.test(normalized);
  return asksForTextNode ? "text_node" : "chat";
}
