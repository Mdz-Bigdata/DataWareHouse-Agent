"""深度审计测试包。

这里有 ``__init__.py`` 而 ``tests/smoke/`` 没有，是**故意**的：两个目录里存在同名
测试文件（``test_ingest.py`` / ``test_quality.py`` …），pytest 默认的 prepend 导入
模式按「文件名 → 模块名」映射，同名文件会撞车并中断收集。给本目录加上包标记后，
本目录的用例以 ``deep.test_xxx`` 的模块名导入，与 ``tests/smoke/`` 的同名文件不再冲突。
"""
