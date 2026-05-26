"""
config.py - 全局配置管理
使用 pydantic-settings 从 .env 文件加载环境变量
"""

import os
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    """
    全局配置类
    """

    # ========== 阿里云百炼配置 ==========
    # API 密钥
    openai_api_key: str = Field(
        ...,
        alias="OPENAI_API_KEY",
        description="阿里云百炼 API 密钥"
    )

    # 基础 URL
    openai_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="OPENAI_BASE_URL",
        description="阿里云百炼基础 URL"
    )

    # 模型
    llm_model: str = Field(
        default="qwen-max",
        alias="LLM_MODEL",
        description="阿里云百炼模型"
    )

    # LLM 温度参数
    llm_temperature: float = Field(
        default=0.1,
        alias="LLM_TEMPERATURE",
        description="LLM温度参数"
    )

    # ========== Redis 配置 ==========
    # Redis 连接 URL
    redis_url: Optional[str] = Field(
        default=None,
        alias="REDIS_URL",
        description="Redis连接URL"
    )
    
    # 缓存过期时间（天）
    cache_ttl_days: int = Field(
        default=7,
        alias="CACHE_TTL_DAYS",
        description="缓存过期天数"
    )

    # ========== GitHub 配置 ==========
    # GitHub Token
    github_token: Optional[str] = Field(
        default=None,
        alias="GITHUB_TOKEN",
        description="GitHub访问令牌"
    )
    
    # GitHub Webhook 签名密钥
    github_webhook_secret: Optional[str] = Field(
        default=None,
        alias="GITHUB_WEBHOOK_SECRET",
        description="GitHub Webhook签名验证密钥"
    )

    # ========== Agent 超时配置（秒）==========
    # 规范员超时
    style_checker_timeout: int = Field(
        default=10,
        alias="STYLE_CHECKER_TIMEOUT",
        description="规范员超时时间（秒）"
    )
    
    # 安全员超时
    security_checker_timeout: int = Field(
        default=10,
        alias="SECURITY_CHECKER_TIMEOUT",
        description="安全员超时时间（秒）"
    )
    
    # 逻辑员超时
    logic_checker_timeout: int = Field(
        default=15,
        alias="LOGIC_CHECKER_TIMEOUT",
        description="逻辑员超时时间（秒）"
    )
    
    # 可读员超时
    readability_checker_timeout: int = Field(
        default=10,
        alias="READABILITY_CHECKER_TIMEOUT",
        description="可读员超时时间（秒）"
    )

    # ========== 降级配置 ==========
    # 降级触发阈值（失败Agent数 <= 此值时尝试继续）
    degradation_threshold: int = Field(
        default=2,
        alias="DEGRADATION_THRESHOLD",
        description="部分降级阈值：成功Agent数低于此值触发全量降级"
    )

    # ========== 其他配置 ==========
    # 项目名称
    project_name: str = Field(
        default="Code Review Multi-Agent System",
        alias="PROJECT_NAME"
    )

    # 日志级别
    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="日志级别：DEBUG/INFO/WARNING/ERROR"
    )

    class Config:
        """Pydantic 配置"""

        env_file = ".env"
        env_file_encoding = "utf-8"
        # 忽略额外的环境变量
        extra = "ignore"

# 创建全局单例实例
settings = Settings()

# 验证必要配置的函数（用于启动时检查）
def validate_required_settings() -> None:
    """
    验证必要的配置项是否已设置
    
    在 FastAPI lifespan 启动时调用
    如果缺少必要配置，抛出异常阻止启动
    """
    missing = []
    
    # API Key 是必须的
    if not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    
    if missing:
        raise ValueError(
            f"缺少必要的环境变量: {', '.join(missing)}\n"
            f"请复制 .env.example 为 .env 并填写正确的值"
        )
    
    print(f"✓ 配置验证通过")
    print(f"  - API 地址: {settings.openai_base_url}")
    print(f"  - LLM 模型: {settings.llm_model}")
    print(f"  - Redis: {'已配置' if settings.redis_url else '未配置（缓存禁用）'}")
    print(f"  - GitHub: {'已配置' if settings.github_token else '未配置（PR评论禁用）'}")