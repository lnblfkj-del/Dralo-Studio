import { SettingsButton } from "./SettingsPrimitives";

export function Pager({page,total,busy,change}:{page:number;total:number;busy:boolean;change:(n:number)=>void}) {
  return <footer className="users-pagination"><span>共 {total} 条</span><div><SettingsButton disabled={busy||page===1} onClick={()=>change(page-1)}>上一页</SettingsButton><span>{page} / {Math.max(1,Math.ceil(total/20))}</span><SettingsButton disabled={busy||page*20>=total} onClick={()=>change(page+1)}>下一页</SettingsButton></div></footer>;
}
