# thiliapr/hentaiverse_script
一个适用于 [HentaiVerse](https://hentaiverse.org/) 的 API 和一个示例的自动打怪脚本，支持针对怪兽的各属性抗性特点，选择合适的攻击目标和攻击魔法攻击。

## 许可证
![GNU AGPL Version 3 Logo](https://www.gnu.org/graphics/agplv3-with-text-162x68.png)

thiliapr/hentaiverse_script 是自由软件，遵循 [Affero GNU 通用公共许可证第 3 版或任何后续版本](https://www.gnu.org/licenses/agpl-3.0.html)。你可以自由地使用、修改和分发。

## 写这个 API 的理由
前作[hentaiverse_battle_bot](https://github.com/thiliapr/hentaiverse_battle_bot/)太混乱了，耦合度极高，加个功能都不知道怎么加。  
所以把 API 和外挂策略解耦出来，这样逻辑就清晰很多了，而且方便调试。

## 这个工具有什么用
请见[前作的介绍](https://github.com/thiliapr/hentaiverse_battle_bot/)

## 怎么使用这个项目里的脚本
### 项目里脚本的介绍
- `battle_bot.py`: 自动打怪脚本，封装了一些战斗逻辑，适用于战斗时，但请确保开启前你没有碰过战斗，否则请打到下一场战斗
- `task_bot.py`: 根据`battle_bot.py`写的自动做任务脚本，包括检测体力值并进行[Arena](https://ehwiki.org/wiki/Arena)战斗、浏览[E-Hentai Gallery](https://ehwiki.org/wiki/Galleries)并进行[随机遇敌事件](https://ehwiki.org/wiki/Random_Encounter)、修复装备（请确保材料足够，你可以通过事先在 [Market](https://hentaiverse.org/?s=Bazaar&ss=mk&screen=browseitems&filter=ma) 买够）、属性加点、贩卖无用物品（在配置指定不想贩卖的过滤器和物品），并简单地忽略[小马谜题](https://ehwiki.org/wiki/RiddleMaster)（注意，小马谜题错误率过高会导致体力消耗过快，请慎重使用）

### 快速使用
1. 填写`config.json`并保存，示例如下（`ipb_member_id`和`ipb_pass_hash`获取方法请见[前作的介绍](https://github.com/thiliapr/hentaiverse_battle_bot/?tab=readme-ov-file#%E6%B5%81%E7%A8%8B)）
   ```json
   {
       "authentication": {
           "ipb_member_id": "19890604",
           "ipb_pass_hash": "deadbeefdeadbeefdeadbeefdeadbeef",
           "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
       },
       "battle_bot": {
           "elite_health_threshold": 1000,
           "critical_health_line": 50,
           "normal_healing_line": 100,
           "mana_supply_line": 20,
           "pre_battle_health_reserve": 200,
           "pre_battle_mana_reserve": 20,
           "draught_buff_round_threshold": 5,
           "spark_buff": false
       },
       "task_bot": {
           "market_bot": {
               "wanted_items": ["Health Draught", "Health Potion", "Mana Draught", "Mana Potion"],
               "skipped_filters": ["Materials", "Trophies"]
           },
           "encounter_difficult_level": "3",
           "arena_difficult_level": "1",
           "arena_epsilon": 0.3,
           "training_henjutsu": "Adept Learner"
       }
   }
   ```
2. 创建文件`skill_data.json`和`monster_data.json`，内容如下
   ```json
   {}
   ```
3. 运行`python task_bot.py`，大功告成

## 怎么写自己的脚本
请参考`battle_bot.py`的内容和`utils/battle.py`的`BattleAPI`类的方法
