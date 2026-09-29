"""长江雨课堂自动解题器。

通过 CDP 接管本机 Chrome，从雨课堂课件接口 / WebSocket 中捕获随堂习题，
交给 DeepSeek 推理出答案，再按配置自动或手动作答。
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
