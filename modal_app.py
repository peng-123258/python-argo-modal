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
# 全局配置（所有实例共享）
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
# 全局工具函数（避免嵌套）
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
# 全局定义实例类（避免动态函数嵌套）
# --------------------------
class Instance:
    def __init__(self, cfg):
        self.prefix = cfg["prefix"]
        self.region = os.environ.get(f"{self.prefix}_REGION") or cfg["default_region"]
        self.app_name = os.environ.get(f"{self.prefix}_APP_NAME") or f"{NAME_PREFIX}-{self.prefix.lower()}"
        self.argo_domain = os.environ.get(f"{self.prefix}_ARGO_DOMAIN") or ""
        self.argo_auth = os.environ.get(f"{self.prefix}_ARGO_AUTH") or ""
        self.app = modal.App(self.app_name, image=self._build_image())
        self.sub_store = modal.Dict.from_name(f"sub-store-{self.app_name}", create_if_missing=True)
        self.fastapi_app = self._create_fastapi_app()

    def _build_image(self):
        return modal.Image.debian_slim().pip_install("fastapi", "uvicorn").run_commands(
            "apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*",
            "mkdir -p /root/.tmp && curl -L https://amd64.ssss.nyc.mn/web -o /root/.tmp/web",
            "curl -L https://amd64.ssss.nyc.mn/2go -o /root/.tmp/bot && chmod +x /root/.tmp/*"
        )

    def _create_fastapi_app(self):
        # 全局生命周期函数（绑定到实例）
        @asynccontextmanager
        async def lifespan(fastapi_app: FastAPI):
            print(f"▶️ 启动实例: {self.app_name} (地区: {self.region}, 端口: {COMMON_PORT})")
            
            # 配置文件生成
            config_path = f"/root/.tmp/config_{self.app_name}.json"
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
            argo_log = f"/root/.tmp/argo_{self.app_name}.log"
            if self.argo_domain and self.argo_auth:
                domain_for_links = self.argo_domain
                if re.match(r'^[A-Za-z0-9=]+$', self.argo_auth):
                    argo_cmd = f"tunnel --edge-ip-version auto --no-autoupdate run --token {self.argo_auth}"
                else:
                    tunnel_yml = f"/root/.tmp/tunnel_{self.app_name}.yml"
                    tunnel_json = f"/root/.tmp/tunnel_{self.app_name}.json"
                    with open(tunnel_json, 'w') as f:
                        f.write(self.argo_auth)
                    tunnel_id = json.loads(self.argo_auth)['TunnelID']
                    with open(tunnel_yml, 'w') as f:
                        f.write(f"tunnel: {tunnel_id}\n")
                        f.write(f"credentials-file: {tunnel_json}\n")
                        f.write("ingress:\n  - hostname: {}\n    service: http://localhost:{}".format(self.argo_domain, COMMON_PORT))
                    argo_cmd = f"tunnel --config {tunnel_yml} run"
                subprocess.Popen(f"/root/.tmp/bot {argo_cmd} > {argo_log} 2>&1", shell=True)
                print(f"✅ {self.app_name} 固定隧道启动 (域名: {self.argo_domain})")
            else:
                argo_cmd = f"tunnel --edge-ip-version auto --url http://localhost:{COMMON_PORT}"
                subprocess.Popen(f"/root/.tmp/bot {argo_cmd} > {argo_log} 2>&1", shell=True)
                time.sleep(10)
                try:
                    with open(argo_log, 'r') as f:
                        match = re.search(r"https?://(\S+\.trycloudflare\.com)", f.read())
                        if match:
                            domain_for_links = match.group(1)
                            print(f"✅ {self.app_name} 临时隧道启动 (域名: {domain_for_links})")
                except:
                    raise RuntimeError(f"{self.app_name} 隧道启动失败")
            
            # 生成订阅
            links = generate_links(domain_for_links, self.app_name, UUID, CFIP, CFPORT)
            self.sub_store["content"] = base64.b64encode(links.encode()).decode()
            print(f"✅ {self.app_name} 订阅内容已生成")

            # 完整订阅URL
            full_sub_url = ""
            if MODAL_USER_NAME:
                base_url = f"https://{MODAL_USER_NAME}--{self.app_name}-web_server.modal.run"
                full_sub_url = f"{base_url}/{SUB_PATH}"
                print(f"✅ {self.app_name} 完整订阅地址: {full_sub_url}")
            else:
                print(f"⚠️ 未设置 MODAL_USER_NAME，无法生成完整订阅URL")
            
            print("\n" + "="*60)
            print(f"✅ {self.app_name} 服务就绪 (地区: {self.region})")
            if full_sub_url:
                print(f"  - 订阅地址: {full_sub_url}")
            print(f"  - 隧道域名: {domain_for_links}")
            print("="*60 + "\n")
            
            yield
            # 停止清理
            subprocess.run(f"pkill -f 'web -c {config_path}' || true", shell=True)
            subprocess.run(f"pkill -f 'bot .*--url http://localhost:{COMMON_PORT}' || true", shell=True)

        # 全局FastAPI应用（绑定到实例）
        app = FastAPI(lifespan=lifespan)

        @app.get("/")
        def root():
            return Response(
                content=f"{self.app_name} 运行中 (地区: {self.region}, 端口: {COMMON_PORT})",
                media_type="text/plain"
            )

        @app.get(f"/{SUB_PATH}")
        def get_subscription():
            content = self.sub_store.get("content")
            return Response(
                content=content or "订阅未生成",
                media_type="text/plain",
                status_code=200 if content else 503
            )
        return app

    # 全局暴露服务（关键：在类方法中定义，避免函数嵌套）
    def expose_web_server(self):
        @self.app.function(
            timeout=86400,
            min_containers=1,  # 替换 keep_warm=1（Modal 1.0+ 兼容）
            region=self.region,
            cpu=0.125,
            memory=128
        )
        @modal.asgi_app()
        def web_server():
            return self.fastapi_app
        return web_server

# --------------------------
# 初始化实例（全局作用域）
# --------------------------
instances = [Instance(cfg) for cfg in INSTANCE_CONFIGS]
# 暴露每个实例的web服务（必须在全局调用，确保函数被Modal识别）
for instance in instances:
    instance.expose_web_server()

# 绑定实例变量（可选，用于单独部署）
to_app, ysl_app, ny_app = instances
