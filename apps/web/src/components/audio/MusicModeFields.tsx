export function MusicModeFields({song,lyrics,onSongChange,onLyricsChange}: {
  song:boolean;lyrics:string;onSongChange:(value:boolean)=>void;onLyricsChange:(value:string)=>void;
}) {
  return <>
    <label>生成类型<select aria-label="音乐生成类型" value={song?'song':'instrumental'} onChange={event=>onSongChange(event.target.value==='song')}>
      <option value="instrumental">纯音乐</option><option value="song">歌曲（含人声）</option>
    </select></label>
    {song && <label>歌词（选填）<textarea aria-label="歌词" rows={5} maxLength={4096} placeholder="留空自动作词" value={lyrics} onChange={event=>onLyricsChange(event.target.value)}/></label>}
  </>;
}
