"""采集器共享异常，避免 web_spider 与 opencli_spider 之间循环导入。"""


class FetchError(RuntimeError):
    pass
