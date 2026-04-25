"""平台内置 agent CLI 模块包。

每个子模块对应一个 agent，需实现两个函数：
  register_subparser(sub: ArgumentParser) -> None
  run(args: Namespace, settings: Settings, parser: ArgumentParser) -> int
"""
