import { Extension } from "@tiptap/react";
import { Plugin } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import { assetIdPattern, isContinuityRecord } from "@/domain/segmentScriptPresentation";

// Decorations change presentation only: the underlying document and undo history stay intact.
export const SegmentScriptPresentation = Extension.create({
  name: "segmentScriptPresentation",
  addProseMirrorPlugins() {
    return [new Plugin({ props: { decorations(state) {
      const decorations: Decoration[] = [];
      state.doc.descendants((node, pos) => {
        if (node.type.name === "paragraph") {
          let text = "";
          node.forEach(child => { text += child.isText ? child.text : child.type.name === "assetMention" ? child.attrs.name : ""; });
          if (isContinuityRecord(text)) decorations.push(Decoration.node(pos, pos + node.nodeSize, { class: "script-continuity-record" }));
        }
        if (node.isText && node.text) {
          for (const match of node.text.matchAll(new RegExp(assetIdPattern))) {
            decorations.push(Decoration.inline(pos + match.index!, pos + match.index! + match[0].length, { class: "script-internal-asset-id" }));
          }
        }
      });
      return DecorationSet.create(state.doc, decorations);
    } } })];
  },
});
