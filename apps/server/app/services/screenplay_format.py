"""Versioned entry contract shared by screenplay creation and rewrite Skills."""

VERSION = "screenplay.format.v1"
SURFACES = {"episode_script", "episode_script_generation", "episode_script_optimization"}
INSTRUCTION = """正文排版合同 screenplay.format.v1（适用于 script 字符串，不改变本次 JSON Schema）：
每集统一采用以下行式模板；按剧情重复场景，不输出 Markdown 标题、表格、分镜或场景数组：
场景1：地点，日/夜，内/外景
人物：角色甲、角色乙
环境声：声音描述。
动作：可见行动与反应。
角色甲：（表演说明）台词。
角色乙：台词。
音效：声音描述。
配乐BGM：按剧情需要填写配乐建议，不要求每场都有；剧情内音乐另用“剧情内音乐：”标注。
示例的角色名和描述须替换为本集真实内容，缺少的声音不凭空补造。人物使用已提供设定的规范姓名或唯一简称；
表演说明放在冒号后括号内，不能混进要朗读的台词；动作单独使用“动作：”。
合说用明确姓名“角色甲/角色乙：台词”，同一句只写一次，不使用主体不明的“两人”。
旁白和设备语音分别用“旁白：”和明确来源（如“广播语音：”），不冒充人物。
不要为排版删改原文事实、台词或身份，不为篇幅截断内容。独立集收束本集核心冲突；
持续背景关系不等于未解决的本集矛盾，new_hooks 只记录实际新增且有待兑现的问题，不凑悬念。
"""
