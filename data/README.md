# 内部运行数据

本目录参考 Blueprint 的集中数据管理方式，存放程序内部状态和开发产物。实验输入仍在根目录 config/，面向人工查看的输出仍在 result/。

| 子目录 | 内容 | 是否随代码提交 |
| --- | --- | --- |
| state/ | journal.sqlite：对话、实验回执、预算和停止状态；locks/：执行互斥锁 | 否 |
| cache/ | pytest 缓存、旧 Python 缓存 | 否 |
| tmp/ | pytest 临时目录及一次性维护脚本 | 否 |
| archive/ | 旧 runs、旧 build 及迁移校验记录，保留原数据 | 否 |

0.6.0 整理时，旧根目录 runs/ 完整移入 archive/runs-before-v0.6/，旧 build/ 移入 archive/build-before-v0.6/。主业务数据库通过 SQLite 备份接口迁移到 state/journal.sqlite，表记录数和完整性已校验。迁移记录位于 archive/layout-migration.json。

新程序默认使用 state/。不要把 archive/ 当作活动运行目录；旧终端需重启后使用新入口。迁移到另一台电脑时，代码运行不需要复制 cache/、tmp/、archive/；需要保留对话时再迁移 state/。
