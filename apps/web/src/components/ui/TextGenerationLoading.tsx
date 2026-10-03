import { useEffect, useState } from "react";
import "./text-generation-loading.css";

const QUIPS = [
  "灵感刚刚路过，我用翅膀把它拦住了。",
  "脑洞可以放飞，逻辑还得系好安全带。",
  "正在给文字梳羽毛，马上就精神了。",
  "键盘已经冒烟，翅膀申请轮班。",
  "灵感正在排队，凑字数的被我劝退了。",
  "我负责敲字，你负责惊艳全场。",
];

/** Only for text generation; branding and general request spinners stay independent. */
export function TextGenerationIcon({ size = 24 }: { size?: number }) {
  return <span className="text-generation-icon" style={{ width: size, height: size }} aria-hidden="true">
    <img className="text-generation-bird" src="/assets/parrot-generating.svg" alt="" />
  </span>;
}

export function TextGenerationQuip() {
  const [text, setText] = useState("");
  useEffect(() => {
    if (typeof window.matchMedia !== "function") { setText(QUIPS[0] ?? ""); return; }
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let line = 0, character = 0, hold = 0;
    let timer: ReturnType<typeof setInterval> | undefined;
    const update = () => {
      if (timer) clearInterval(timer);
      timer = undefined;
      if (motion.matches || document.hidden) { setText(QUIPS[line] ?? ""); return; }
      timer = setInterval(() => {
        const phrase = QUIPS[line] ?? "";
        if (character < phrase.length) setText(phrase.slice(0, ++character));
        else if (++hold >= 30) { line = (line + 1) % QUIPS.length; character = 0; hold = 0; setText(""); }
      }, 110);
    };
    update();
    motion.addEventListener("change", update);
    document.addEventListener("visibilitychange", update);
    return () => { if (timer) clearInterval(timer); motion.removeEventListener("change", update); document.removeEventListener("visibilitychange", update); };
  }, []);
  return <div className="text-generation-quip" aria-hidden="true"><span>鹦鹉碎碎念</span><div>{text}<i /></div></div>;
}

export function TextGenerationLoading({ label, quip = false }: { label: string; quip?: boolean }) {
  return <div className="text-generation-loading"><div className="text-generation-loading__status" role="status"><TextGenerationIcon /><span>{label}</span></div>{quip && <TextGenerationQuip />}</div>;
}
