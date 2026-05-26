"""
FastAPI 中间件配置

请求处理流程中的"拦截器"，在每个请求进入路由之前和返回响应之后执行
"""

import time
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from src.utils.logger import logger

class LoggingMiddleware(BaseHTTPMiddleware):
    """
    请求日志中间件
    
    功能：
    1. 记录每个请求的开始时间和结束时间
    2. 计算耗时并记录到日志
    3. 记录请求方法、路径、状态码
    """

    async def dispatch(self, request: Request, call_next):
        """
        请求日志中间件
        
        执行流程：
        1. 请求进入 -> 记录开始时间
        2. 执行下一个中间件或路由处理函数 -> 拿到响应
        3. 记录结束时间 -> 计算耗时 -> 写日志
        4. 返回响应
        """

        # 记录请求开始时间
        start_time = time.time()
        
        # 记录请求基本信息（方法、路径、客户端IP）
        method = request.method
        url = request.url.path
        client_ip = request.client.host if request.client else "unknown"

        # 请求进入日志
        logger.info(f"请求开始 | {method} {url} | IP: {client_ip}")

        try:
            # 执行下一个中间件或路由处理函数
            response = await call_next(request)
            
            # 计算耗时
            process_time = time.time() - start_time
            elapsed_ms = round(process_time * 1000)  # 毫秒
            
            # 响应日志（包含状态码和耗时）
            status_code = response.status_code
            logger.info(
                f"请求完成 | {method} {url} | 状态码: {status_code} | 耗时: {elapsed_ms}ms"
            )
            
            # 在响应头中添加耗时信息（便于前端调试）
            response.headers["X-Process-Time"] = str(elapsed_ms)
            
            return response

        except Exception as e:
            # 捕获异常
            process_time = time.time() - start_time
            elapsed_ms = round(process_time * 1000)
            
            # 记录错误日志
            logger.error(
                f"请求异常 | {method} {url} | 错误: {str(e)} | 耗时: {elapsed_ms}ms"
            )
            
            # 返回统一的错误响应
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal Server Error",
                    "detail": str(e) if str(e) else "服务器内部错误"
                }
            )

class ExceptionHandlingMiddleware(BaseHTTPMiddleware):
    """
    全局异常处理中间件
    
    捕获所有未处理的异常，返回统一的 JSON 格式错误响应
    避免暴露内部错误信息给客户端
    """
    
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
            
        except Exception as e:
            # 记录完整的错误堆栈到日志（便于调试）
            logger.opt(exception=True).error(f"未处理的异常: {str(e)}")
            
            # 返回给客户端的安全错误信息（不暴露内部细节）
            return JSONResponse(
                status_code=500,
                content={
                    "code": 500,
                    "message": "服务器内部错误，请稍后重试",
                    "detail": str(e) if str(e) else None
                }
            )

