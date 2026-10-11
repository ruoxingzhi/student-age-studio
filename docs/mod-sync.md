# Mod 协作与多平台同步（地基层说明）

> 给未来接 UI 的自己看的。用户永远看不到 git，下面这张对照表是铁律：
> 用户文案里出现左边任何一个词就是 bug。

| 绝不展示 | 用户看到的说法 |
|---|---|
| commit | 存档 |
| push / pull / fetch | 同步（发上去／收下来） |
| branch | 设备线（内部名，不展示） |
| merge | 合稿 |
| conflict | 待选（两边都留着） |
| clone | 加入 |
| remote | 中转站 |
| log | 时间线 |

## 东西放在哪

* Mod 文件夹里**没有** `.git`，保持干净（工坊上传、备份本就跳过 `.git`，双保险）。
* 账本在每台电脑自己的用户目录：`StudentAgeStudio/sync/<Mod指纹>/repo`（bare 格式），
  身份在同目录 `device.json`（名字可改，后缀永不重名）。
* 中转站 = 网盘里的一个空文件夹，里面是一个 bare 账本（`HEAD` 在顶层即正常）。
  每台电脑只写自己的历史线，从不同步 `.git` 目录本身（传的是 git 对象，不是文件夹同步）。

## 流程（对应 `standalone/sync_store.py:ModSync`）

1. `start_fresh` 建账本（幂等，已有则返回 `already`）→ `set_relay` 选中转站。
2. `save_snapshot(说明)` 存档；无变化返回 `saved: False`，不记空档。
3. `sync()`：收 → 合（干净则自动合；同改一句则留我的、对方进`合稿待选/`）→ 发；
   发不出去（对方抢先）自动收回合一次，还不行才报"对方也在同步，先收了他的再发一次"。
4. `join` 加入：要求 Mod 文件夹是空的；中转站空则报"让先发的人先同步一次"。
5. 一边删一边改：改的赢（对方删过你改过→留你的；反之捡回对方的），都记一笔。
6. `current_state()` 返回 `已修改／新增／已删除`；`timeline()` 倒序列存档点（编号／时间／作者／说明）。
7. `size_warning()` 超 900MB 提醒一句，不拦。

## 铁律（改代码别碰）

* 用户文案全中文，见 `test_sync_store.py:test_missing_git_message_is_chinese` 的断言方式；
  新增文案自觉加同类断言。
* 中转站只追加历史：本模块**永不删除**中转站里的任何东西；`set_relay` 碰到非空非账本文件夹直接拒绝。
* 合稿默认不丢字：拿不准就两份都留（我的在原位，对方的进`合稿待选/`）。
* 同步只发"已存档"的内容；没存档先报"还没有存档，先存一版再同步"。
* 测试用真 git（CI 三系统自带），本机没 git 整份跳过；单测跑
  `python -B -m unittest discover -s standalone/tests -p test_sync_store.py`。
* 后续接 UI／接服务端中转（B 方案）时，`ModSync` 的输入输出形状不变，只换 transport。
