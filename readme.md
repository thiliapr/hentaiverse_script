# thiliapr/hentaiverse_script
一个适用于 [HentaiVerse](https://hentaiverse.org/) 的 API 和一个示例的自动打怪脚本，支持针对怪兽的各属性抗性特点，选择合适的攻击目标和攻击魔法攻击。

## 许可证
![GNU AGPL Version 3 Logo](https://www.gnu.org/graphics/agplv3-with-text-162x68.png)

thiliapr/hentaiverse_script 是自由软件(Free as in Freedom)，遵循 [Affero GNU 通用公共许可证第 3 版或任何后续版本](https://www.gnu.org/licenses/agpl-3.0.html)。你享有运行、研究和修改、分发修改前后的拷贝的自由，详情请参见 [什么是自由软件？](https://www.gnu.org/philosophy/free-sw.html)

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
1. 填写`world/persistent/config.json`并保存，示例如下（`ipb_member_id`和`ipb_pass_hash`获取方法请见[前作的介绍](https://github.com/thiliapr/hentaiverse_battle_bot/?tab=readme-ov-file#%E6%B5%81%E7%A8%8B)）
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
           "spirit_supply_line": -1,
           "pre_battle_health_reserve": 200,
           "pre_battle_mana_reserve": 20,
           "pre_battle_magics": ["Full-Cure", "Cure"],
           "pre_battle_items": ["Health Potion"],
           "spark_trigger_spirit": 19890604,
           "prof_mana_threshold": 19890604,
           "supportive_buff": false
       },
       "riddle_ai": {
           "threshold": 1,
           "model_path": "riddle/runs/detect/ckpt/train/weights/best.pt"
       },
       "task_bot": {
           "enabled": true,
           "market_bot": {
               "wanted_items": ["Health Draught", "Health Potion", "Mana Draught", "Mana Potion", "Spirit Draught", "Spirit Potion", "Scrap Cloth", "Scrap Wood"],
               "skipped_filters": ["Materials", "Trophies"]
           },
           "equipment_store_bot": {
               "skipped_filters": ["Staffs", "Cloth"],
               "skipped_qualities": ["Magnificent", "Legendary", "Peerless"]
           },
           "battle": {
               "Random Encounter": {
                   "difficult_level": "2",
                   "epsilon": 0,
                   "battle_bot_override": {
                       "mana_supply_line": 10,
                       "elite_health_threshold": 19890604
                   }
               },
               "Arena": {
                   "difficult_level": "1",
                   "epsilon": 0.1,
                   "battle_bot_override": {}
               },
               "Ring of Blood": {
                   "difficult_level": "1",
                   "epsilon": 0.1,
                   "battle_bot_override": {}
               }
           },
           "training_henjutsu": ["Adept Learner", "Assimilator", "Scavenger", "Quartermaster", "Luck of the Draw", "Archaeologist"]
       }
   }
   ```
2. 填写`world/isekai/config.json`填写类似配置，但是不用写`task_bot.battle.{regex,(.+)}.difficult_level`和`training_henjutsu`项
3. 运行`python task_bot.py`，大功告成

### 异世界
1. 异世界的介绍请参见 [Wiki](https://ehwiki.org/wiki/Isekai)
2. - 使用方法快速使用相同，但是把 `world/persistent` 改成 `world/isekai`，然后填写异世界专用配置
   - `task_bot` 需要 `market_bot` 和 `epsilon` 项，不需要其他项
3. 运行时手动打开异世界的战斗，然后运行 `python battle_bot.py --isekai` 即可

## 怎么写自己的脚本
请参考`battle_bot.py`的内容和`utils/battle.py`的`BattleAPI`类的方法

## 私货
- plz，看看这些文章
  - [Tivoization（硬件自锁技术）](https://www.gnu.org/philosophy/tivoization.html)
    - 臭名昭著的 [Android](https://zh.wikipedia.org/wiki/Android) 的 BootLoader 锁就是一个典型案例 
  - [Android 和用户的自由](https://www.gnu.org/philosophy/android-and-users-freedom.html)
  - [Linux、GNU和自由](https://www.gnu.org/philosophy/linux-gnu-freedom.html)
  - [让软件免受专利困扰](https://www.gnu.org/philosophy/limit-patent-effect.html)
  - [对版权的误解—一系列的错误](https://www.gnu.org/philosophy/misinterpreting-copyright.html)
  - [重新审视版权制度：公众利益应居首位](https://www.gnu.org/philosophy/reevaluating-copyright.html)
  - [还在用“知识产权”这词吗？它只是看上去很美](https://www.gnu.org/philosophy/not-ipr.html)
  - [别让 “知识产权” 扭曲你的价值观](https://www.gnu.org/philosophy/no-ip-ethos.html)