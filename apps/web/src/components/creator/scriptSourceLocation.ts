export function sourceLineRange(text: string, line: number) {
  if (!Number.isInteger(line) || line < 1) return null;
  const separator = /\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/g;
  let start = 0;
  for (let current = 1; ; current++) {
    const match = separator.exec(text);
    const end = match?.index ?? text.length;
    if (current === line) return { start, end, text: text.slice(start, end) };
    if (!match) return null;
    start = match.index + match[0].length;
  }
}

export function revealEditorLine(editor: HTMLTextAreaElement, start: number, end: number) {
  editor.focus(); editor.setSelectionRange(start, end);
  const style = getComputedStyle(editor);
  const mirror = document.createElement("div");
  Object.assign(mirror.style, {
    position: "fixed", visibility: "hidden", width: `${editor.clientWidth}px`,
    boxSizing: "border-box", whiteSpace: "pre-wrap", overflowWrap: "break-word",
    font: style.font, lineHeight: style.lineHeight, padding: style.padding,
    letterSpacing: style.letterSpacing, tabSize: style.tabSize,
  });
  mirror.append(document.createTextNode(editor.value.slice(0, start)));
  const marker = document.createElement("span"); marker.textContent = editor.value.slice(start, end) || " ";
  mirror.append(marker, document.createTextNode(editor.value.slice(end)));
  document.body.append(mirror);
  editor.scrollTop = Math.max(0, marker.getBoundingClientRect().top - mirror.getBoundingClientRect().top - editor.clientHeight / 3);
  mirror.remove();
}
