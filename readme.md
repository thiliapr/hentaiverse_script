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

## 怎么使用
请参考`minion.py`的内容和`utils/battle.py`的`Battle`类的方法
