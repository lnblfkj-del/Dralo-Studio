import { type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import "@/styles/settings-primitives.css";

export type SettingsDialogSize = "small" | "medium" | "large";
export function SettingsDialog({ title, description, size = "medium", dirty = false, busy = false, formless = false, onClose, onSubmit, footer, children }: {
  title: string; description?: string; size?: SettingsDialogSize; dirty?: boolean; busy?: boolean;
  formless?: boolean; onClose: () => void; onSubmit?: (event: FormEvent<HTMLFormElement>) => void; footer?: ReactNode | ((requestClose: () => void) => ReactNode); children: ReactNode;
}) {
  const content=(requestClose:()=>void)=><><div className="settings-dialog__body">{children}</div>{footer&&<footer className="settings-dialog__footer">{typeof footer==="function"?footer(requestClose):footer}</footer>}</>;
  return <Dialog open className={`settings-dialog settings-dialog--${size}`} title={title} description={description} size={size} dirty={dirty} busy={busy} onClose={onClose}>
    {requestClose=>formless?<div className="settings-dialog__form">{content(requestClose)}</div>:<form className="settings-dialog__form" onSubmit={event=>{if(onSubmit)onSubmit(event);else event.preventDefault();}}>{content(requestClose)}</form>}
  </Dialog>;
}
export function SettingsTabs<T extends string>({value,items,onChange,label}:{value:T;items:Array<{value:T;label:string}>;onChange:(value:T)=>void;label:string}) {
  return <div className="settings-tabs" role="tablist" aria-label={label}>{items.map((item,index)=><button key={item.value} type="button" role="tab" aria-selected={value===item.value} tabIndex={value===item.value?0:-1} onClick={()=>onChange(item.value)} onKeyDown={event=>{
    const direction=event.key==="ArrowRight"?1:event.key==="ArrowLeft"?-1:0;
    const next=event.key==="Home"?0:event.key==="End"?items.length-1:direction?(index+direction+items.length)%items.length:-1;
    const nextItem=items[next];
    if(next<0||!nextItem)return;event.preventDefault();onChange(nextItem.value);(event.currentTarget.parentElement?.children[next] as HTMLElement|undefined)?.focus();
  }}>{item.label}</button>)}</div>;
}
export function SettingsStatus({tone="neutral",children}:{tone?:"success"|"warning"|"danger"|"neutral";children:ReactNode}) {
  return <span className="settings-status" data-tone={tone}><i aria-hidden="true"/>{children}</span>;
}
export function SettingsConfirmDialog({title,message,confirmLabel="确认",danger=false,busy=false,onConfirm,onClose}:{title:string;message:ReactNode;confirmLabel?:string;danger?:boolean;busy?:boolean;onConfirm:()=>void;onClose:()=>void}) {
  return <ConfirmDialog open title={title} message={message} confirmLabel={confirmLabel} danger={danger} busy={busy} onConfirm={onConfirm} onClose={onClose}/>;
}

export function SettingsButton({primary=false,danger=false,children,...props}:React.ComponentProps<typeof Button>&{primary?:boolean;danger?:boolean}) {
  return <Button variant={danger?"danger":primary?"primary":"secondary"} {...props}>{children}</Button>;
}
