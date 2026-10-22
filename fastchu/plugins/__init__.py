"""NoneBot 插件包。

这里只放「机器人框架胶水」，业务逻辑都在 :mod:`fastchu.services` /
:mod:`fastchu.render` 里。因此本包**不要**在 ``__init__`` 里导入插件模块——
``fastchu.plugins`` 被导入时不一定有 NoneBot 环境。
"""
