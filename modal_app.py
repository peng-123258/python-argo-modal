import os
import re
import json
import time
import base64
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, Request
import modal

# 实例配置（修正地区格式，去除空格）
INSTANCE_CONFIGS = [
    {"prefix": "TO", "default_region": "asia-northeast3"},  # 原"asia northeast3"修正
    {"prefix": "YSL", "default_region": "me-west1"},  # 原"me me-west1"修正
    {"prefix": "NY", "default_region": "sa-east-1"},
]

# 共享配置（保持原始参数）
COMMON_PORT = 8001
SUB_PATH = "sub"
UUID = os.environ.get("COMMON_UUID") or "be16536e-5c3c-44bc-8cb7-b7d0ddc3d951"
NAME_PREFIX = "proxy-node"
CFIP = os.environ.get("COMMON_CFIP") or "www.visa.com.tw"
CFPORT = int(os.environ.get("COMMON_CFPORT") or 443)
MODAL_USER_NAME = os.environ.get('MODAL_USER_NAME') or ""

# 全局函数：生成订阅链接（移到全局作用域，避免嵌套）
def generate_links(domain, name, uuid, cfip, cfport):
    # 保留原始vless、vmess、trojan链接生成逻辑
    vless_link = (
        f"vless://{uuid}@{cfip}:{cfport}?encryption=none&security=tls&sni={domain}"
        f"&type=ws&host={domain}&path=%2Fvless-argo%3Fed%3D2560#{name}"
    )
    
    vmess_config = {
        "v": "2", "ps": name, "add": cfip, "port": cfport,
        "id": uuid, "aid": "0", "net": "ws", "host": domain,
        "path": "/vmess-argo?ed=2560", "tls": "tls", "sni": domain
    }
    vmess_b64 = base64.b64encode(json.dumps(vmess_config).encode()).decode()
    vmess_link = f"vmess://{vmess_b64}"
    
    trojan_link = (
        f"trojan://{uuid}@{cfip}:{cfport}?security=tls&sni={domain}"
        f"&type=ws&host={domain}&path=%2Ftrojan-argo%3Fed%3D2560#{name}"
    )
    
    return f"{vless_link}\n{vmess_link}\n{trojan_link}"

# 全局函数：启动外部服务（增加错误处理，避免进程残留）
def start_external_service(service_path, log_path):
    try:
        # 检查文件是否存在，避免subprocess调用失败
        if not os.path.exists(service_path):
            raise FileNotFoundError(f"服务文件不存在: {service_path}")
        
        # 启动进程并绑定日志输出
        with open(log_path, "w") as f:
            process = subprocess.Popen(
                [service_path],
                stdout=f,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid  # 独立进程组，避免被父进程终止影响
            )
        return process
    except Exception as e:
        print(f"启动服务失败: {e}")
        return None

# 全局创建实例函数（每个实例独立App，避免动态创建导致识别失败）
def create_instance(cfg):
    prefix = cfg["prefix"]
    region = os.environ.get(f"{prefix}_REGION") or cfg["default_region"]
    app_name = os.environ.get(f"{prefix}_APP_NAME") or f"{NAME_PREFIX}-{prefix.lower()}"
    
    # 实例专属存储（全局定义，避免嵌套）
    sub_store = modal.Dict.from_name(f"sub-store-{app_name}", create_if_missing=True)
    log_store = modal.Volume.from_name(f"log-volume-{app_name}", create_if_missing=True)
    
    # 全局定义FastAPI应用（避免在lifespan内创建）
    fastapi_app = FastAPI()
    
    # 全局定义生命周期函数（移到全局，避免嵌套）
    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        print(f"▶️ 启动实例: {app_name} (地区: {region})")
        
        # 启动核心服务（保留subprocess但增加检查）
        web_service = start_external_service(
            "/root/.tmp/web",  # 假设文件存在，实际应通过modal.Image预安装
            f"/vol/{app_name}_web.log"
        )
        bot_service = start_external_service(
            "/root/.tmp/bot",
            f"/vol/{app_name}_bot.log"
        )
        
        # 生成订阅链接（调用全局函数）
        domain_for_links = os.environ.get(f"{prefix}_DOMAIN") or f"{app_name}.example.com"
        links = generate_links(domain_for_links, app_name, UUID, CFIP, CFPORT)
        sub_store["content"] = base64.b64encode(links.encode()).decode()
        
        yield  # 运行阶段
        
        # 停止服务（优雅清理）
        if web_service:
            os.killpg(os.getpgid(web_service.pid), subprocess.SIGTERM)
        if bot_service:
            os.killpg(os.getpgid(bot_service.pid), subprocess.SIGTERM)
        print(f"▶️ 停止实例: {app_name}")
    
    # 绑定生命周期（全局操作）
    fastapi_app = FastAPI(lifespan=lifespan)
    
    # 路由定义（移到全局，避免嵌套在create_instance内）
    @fastapi_app.get("/")
    def root():
        return Response(
            f"{app_name} 运行中 (地区: {region}, 端口: {COMMON_PORT})",
            media_type="text/plain"
        )
    
    @fastapi_app.get(f"/{SUB_PATH}")
    def get_subscription():
        content = sub_store.get("content")
        return Response(
            content or "订阅未生成",
            media_type="text/plain"
        )
    
    @fastapi_app.get("/logs")
    def get_logs():
        log_content = ""
        log_path = f"/vol/{app_name}_web.log"
        if log_store.exists(log_path):
            with log_store.open(log_path, "r") as f:
                log_content = f.read()
        return Response(log_content, media_type="text/plain")
    
    # 定义Modal应用（全局作用域，避免动态创建）
    app = modal.App(name=app_name)
    
    # 暴露服务（使用@modal.asgi_app装饰全局FastAPI实例）
    @app.function(
        timeout=86400,
        keep_warm=1,
        region=region,
        cpu=0.125,
        memory=128
    )
    @modal.asgi_app()
    def web_server():
        return fastapi_app
    
    return app

# 初始化实例（全局执行，避免嵌套）
instances = [create_instance(cfg) for cfg in INSTANCE_CONFIGS]
to_app, ysl_app, ny_app = instances
