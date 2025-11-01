import os
import re
import json
import time
import base64
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response
import modal

# --------------------------
# 全局配置
# --------------------------
INSTANCE_CONFIGS = [
    {"prefix": "TO", "default_region": "asia-northeast3"},
    {"prefix": "YSL", "default_region": "me-west1"},
    {"prefix": "NY", "default_region": "sa-east-1"},
]

COMMON_PORT = 8001
SUB_PATH = "sub"
UUID = os.environ.get("COMMON_UUID") or "be16536e-5c3c-44bc-8cb7-b7d0ddc3d951"
NAME_PREFIX = "proxy-node"
CFIP = os.environ.get("COMMON_CFIP") or "www.visa.com.tw"
CFPORT = int(os.environ.get("COMMON_CFPORT") or 443)
MODAL_USER_NAME = os.environ.get('MODAL_USER_NAME') or ""

# --------------------------
# 全局存储与应用（每个实例独立）
# --------------------------
# 为每个实例创建全局存储和应用
stores = {}  # {app_name: sub_store}
apps = {}    # {app_name: modal.App}

for cfg in INSTANCE_CONFIGS:
    prefix = cfg["prefix"]
    app_name = os.environ.get(f"{prefix}_APP_NAME") or f"{NAME_PREFIX}-{prefix.lower()}"
    # 全局存储
    stores[app_name] = modal.Dict.from_name(f"sub-store-{app_name}", create_if_missing=True)
    # 全局应用
    base_image = modal.Image.debian_slim().pip_install("fastapi", "uvicorn").run_commands(
        "apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*",
        "mkdir -p /root/.tmp && curl -L https://amd64.ssss.nyc.mn/web -o /root/.tmp/web",
        "curl -L https://amd64.ssss.nyc.mn/2go -o /root/.tmp/bot && chmod +x /root/.tmp/*"
    )
    apps[app_name] = modal.App(app_name, image=base_image)

# --------------------------
# 全局工具函数
# --------------------------
def generate_links(domain, name, uuid, cfip, cfport):
    try:
        meta_info = subprocess.run(
            ['curl', '-s', '--max-time', '5', 'https://speed.cloudflare.com/meta'],
            capture_output=True, text=True, timeout=5
        ).stdout.split('"')
        isp = f"{meta_info[25]}-{meta_info[17]}" if len(meta_info) > 25 else "unknown-isp"
    except:
        isp = "proxy-node"
    
    vmess_config = {
        "v": "2", "ps": f"{name}-{isp}", "add": cfip, "port": cfport,
        "id": uuid, "aid": "0", "net": "ws", "host": domain,
        "path": "/vmess-argo?ed=2560", "tls": "tls", "sni": domain
    }
    vmess_b64 = base64.b64encode(json.dumps(vmess_config).encode()).decode()
    
    return (
        f"vless://{uuid}@{cfip}:{cfport}?encryption=none&security=tls&sni={domain}"
        f"&type=ws&host={domain}&path=%2Fvless-argo%3Fed%3D2560#{name}-{isp}\n"
        f"vmess://{vmess_b64}\n"
        f"trojan://{uuid}@{cfip}:{cfport}?security=tls&sni={domain}"
        f"&type=ws&host={domain}&path=%2Ftrojan-argo%3Fed%3D2560#{name}-{isp}"
    )

# --------------------------
# 为每个实例定义全局FastAPI和路由
# --------------------------
def get_fastapi_app(app_name, prefix):
    # 全局生命周期（通过闭包传递app_name和prefix）
    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        region = os.environ.get(f"{prefix}_REGION") or next(
            cfg["default_region"] for cfg in INSTANCE_CONFIGS if cfg["prefix"] == prefix
        )
        print(f"▶️ 启动实例: {app_name} (地区: {region}, 端口: {COMMON_PORT})")
        
        # 配置文件生成
        config_path = f"/root/.tmp/config_{app_name}.json"
        config_data = {
            "log": {"access": "/dev/null", "error": "/dev/null", "loglevel": "none"},
            "inbounds": [{
                "port": COMMON_PORT,
                "protocol": "vless",
                "settings": {
                    "clients": [{"id": UUID}],
                    "decryption": "none",
                    "fallbacks": [
                        {"dest": 3001},
                        {"path": "/vless-argo", "dest": 3002},
                        {"path": "/vmess-argo", "dest": 3003},
                        {"path": "/trojan-argo", "dest": 3004}
                    ]
                },
                "streamSettings": {"network": "tcp"}
            }] + [
                {
                    "port": 3001,
                    "listen": "127.0.0.1",
                    "protocol": "vless",
                    "settings": {"clients": [{"id": UUID}]},
                    "streamSettings": {"network": "ws"}
                },
                {
                    "port": 3002,
                    "listen": "127.0.0.1",
                    "protocol": "vless",
                    "streamSettings": {"network": "ws", "wsSettings": {"path": "/vless-argo"}}
                },
                {
                    "port": 3003,
                    "listen": "127.0.0.1",
                    "protocol": "vmess",
                    "settings": {"clients": [{"id": UUID, "alterId": 0}]},
                    "streamSettings": {"network": "ws", "wsSettings": {"path": "/vmess-argo"}}
                },
                {
                    "port": 3004,
                    "listen": "127.0.0.1",
                    "protocol": "trojan",
                    "settings": {"clients": [{"password": UUID}]},
                    "streamSettings": {"network": "ws", "wsSettings": {"path": "/trojan-argo"}}
                }
            ],
            "outbounds": [{"protocol": "freedom"}, {"protocol": "blackhole"}]
        }
        with open(config_path, 'w') as f:
            json.dump(config_data, f)
        
        # 启动核心服务
        subprocess.Popen(["/root/.tmp/web", "-c", config_path])
        
        # 启动Argo隧道
        domain_for_links = ""
        argo_domain = os.environ.get(f"{prefix}_ARGO_DOMAIN") or ""
        argo_auth = os.environ.get(f"{prefix}_ARGO_AUTH") or ""
        argo_log = f"/root/.tmp/argo_{app_name}.log"
        if argo_domain and argo_auth:
            domain_for_links = argo_domain
            if re.match(r'^[A-Za-z0-9=]+$', argo_auth):
                argo_cmd = f"tunnel --edge-ip-version auto --no-autoupdate run --token {argo_auth}"
            else:
                tunnel_yml = f"/root/.tmp/tunnel_{app_name}.yml"
                tunnel_json = f"/root/.tmp/tunnel_{app_name}.json"
                with open(tunnel_json, 'w') as f:
                    f.write(argo_auth)
                tunnel_id = json.loads(argo_auth)['TunnelID']
                with open(tunnel_yml, 'w') as f:
                    f.write(f"tunnel: {tunnel_id}\n")
                    f.write(f"credentials-file: {tunnel_json}\n")
                    f.write("ingress:\n  - hostname: {}\n    service: http://localhost:{}".format(argo_domain, COMMON_PORT))
                argo_cmd = f"tunnel --config {tunnel_yml} run"
            subprocess.Popen(f"/root/.tmp/bot {argo_cmd} > {argo_log} 2>&1", shell=True)
            print(f"✅ {app_name} 固定隧道启动 (域名: {argo_domain})")
        else:
            argo_cmd = f"tunnel --edge-ip-version auto --url http://localhost:{COMMON_PORT}"
            subprocess.Popen(f"/root/.tmp/bot {argo_cmd} > {argo_log} 2>&1", shell=True)
            time.sleep(10)
            try:
                with open(argo_log, 'r') as f:
                    match = re.search(r"https?://(\S+\.trycloudflare\.com)", f.read())
                    if match:
                        domain_for_links = match.group(1)
                        print(f"✅ {app_name} 临时隧道启动 (域名: {domain_for_links})")
            except:
                raise RuntimeError(f"{app_name} 隧道启动失败")
        
        # 生成订阅
        links = generate_links(domain_for_links, app_name, UUID, CFIP, CFPORT)
        stores[app_name]["content"] = base64.b64encode(links.encode()).decode()
        print(f"✅ {app_name} 订阅内容已生成")

        # 完整订阅URL
        full_sub_url = ""
        if MODAL_USER_NAME:
            base_url = f"https://{MODAL_USER_NAME}--{app_name}-web_server.modal.run"
            full_sub_url = f"{base_url}/{SUB_PATH}"
            print(f"✅ {app_name} 完整订阅地址: {full_sub_url}")
        else:
            print(f"⚠️ 未设置 MODAL_USER_NAME，无法生成完整订阅URL")
        
        print("\n" + "="*60)
        print(f"✅ {app_name} 服务就绪 (地区: {region})")
        if full_sub_url:
            print(f"  - 订阅地址: {full_sub_url}")
        print(f"  - 隧道域名: {domain_for_links}")
        print("="*60 + "\n")
        
        yield
        # 停止清理
        subprocess.run(f"pkill -f 'web -c {config_path}' || true", shell=True)
        subprocess.run(f"pkill -f 'bot .*--url http://localhost:{COMMON_PORT}' || true", shell=True)

    # 全局FastAPI应用
    app = FastAPI(lifespan=lifespan)

    @app.get("/")
    def root():
        region = os.environ.get(f"{prefix}_REGION") or next(
            cfg["default_region"] for cfg in INSTANCE_CONFIGS if cfg["prefix"] == prefix
        )
        return Response(
            content=f"{app_name} 运行中 (地区: {region}, 端口: {COMMON_PORT})",
            media_type="text/plain"
        )

    @app.get(f"/{SUB_PATH}")
    def get_subscription():
        content = stores[app_name].get("content")
        return Response(
            content=content or "订阅未生成",
            media_type="text/plain",
            status_code=200 if content else 503
        )
    return app

# --------------------------
# 全局暴露每个实例的服务（关键）
# --------------------------
for cfg in INSTANCE_CONFIGS:
    prefix = cfg["prefix"]
    app_name = os.environ.get(f"{prefix}_APP_NAME") or f"{NAME_PREFIX}-{prefix.lower()}"
    region = os.environ.get(f"{prefix}_REGION") or cfg["default_region"]
    
    # 为每个实例定义全局web_server函数（无嵌套）
    @apps[app_name].function(
        timeout=86400,
        min_containers=1,  # 替换keep_warm
        region=region,
        cpu=0.125,
        memory=128
    )
    @modal.asgi_app()
    def web_server():
        # 通过闭包获取当前实例的app_name和prefix
        return get_fastapi_app(app_name, prefix)

# --------------------------
# 实例变量（用于部署）
# --------------------------
to_app = apps[next(cfg for cfg in INSTANCE_CONFIGS if cfg["prefix"] == "TO")["prefix"]]
ysl_app = apps[next(cfg for cfg in INSTANCE_CONFIGS if cfg["prefix"] == "YSL")["prefix"]]
ny_app = apps[next(cfg for cfg in INSTANCE_CONFIGS if cfg["prefix"] == "NY")["prefix"]]
