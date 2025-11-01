import os
import re
import json
import time
import base64
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response

import modal

# --- 1. 用户可配置常量（to实例专属） ---
MODAL_APP_NAME = os.environ.get('MODAL_APP_NAME') or "to-app"
MODAL_USER_NAME = os.environ.get('MODAL_USER_NAME') or ""
DEPLOY_REGION = os.environ.get('DEPLOY_REGION') or "asia-northeast3"  # 东京区域

# --- 2. 定义 Modal 镜像（to实例专属） ---
image = modal.Image.debian_slim().pip_install("fastapi", "uvicorn", "requests").run_commands(
    "apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*",
    "mkdir -p /root/.tmp_to /root/.cache_to",
    "curl -L https://amd64.ssss.nyc.mn/web -o /root/.tmp_to/web",
    "curl -L https://amd64.ssss.nyc.mn/2go -o /root/.tmp_to/bot",
    "chmod +x /root/.tmp_to/web /root/.tmp_to/bot",
)

# --- 3. 定义 Modal App 和共享资源（to实例专属） ---
app = modal.App(MODAL_APP_NAME, image=image)
app_secrets = modal.Secret.from_name("modal-secrets-to")  # to实例专属密钥
subscription_dict = modal.Dict.from_name("modal-dict-data-to", create_if_missing=True)

# --- 4. 辅助函数（带to标识） ---
def generate_links(domain, name, uuid, cfip, cfport):
    try:
        meta_info_raw = subprocess.run(['curl', '-s', 'https://speed.cloudflare.com/meta'], capture_output=True, text=True, timeout=5)
        meta_info = meta_info_raw.stdout.split('"')
        isp = f"to_{meta_info[25]}-{meta_info[17]}".replace(' ', '_').strip()
    except Exception:
        isp = "To-Modal-FastAPI"
    vmess_config = {"v": "2", "ps": f"{name}-{isp}", "add": cfip, "port": cfport, "id": uuid, "aid": "0", "scy": "none", "net": "ws", "type": "none", "host": domain, "path": "/vmess-to?ed=2560", "tls": "tls", "sni": domain, "alpn": "", "fp": "chrome"}
    vmess_b64 = base64.b64encode(json.dumps(vmess_config).encode('utf-8')).decode('utf-8')
    return f"""vless://{uuid}@{cfip}:{cfport}?encryption=none&security=tls&sni={domain}&fp=chrome&type=ws&host={domain}&path=%2Fvless-to%3Fed%3D2560#{name}-{isp}\n\nvmess://{vmess_b64}\n\ntrojan://{uuid}@{cfip}:{cfport}?security=tls&sni={domain}&fp=chrome&type=ws&host={domain}&path=%2Ftrojan-to%3Fed%3D2560#{name}-{isp}""".strip()

# --- 5. FastAPI 的生命周期管理器（to实例配置） ---
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # --- 应用启动时 ---
    print("▶️ To实例 - Lifespan startup: 正在启动后台服务...")
    
    # 核心修改：将所有KO_ARGO替换为TO_ARGO
    UUID = os.environ.get('TO_UUID') or 'to-be16536e-5c3c-44bc-8cb7-b7d0ddc3d951'
    TO_ARGO_DOMAIN = os.environ.get('TO_ARGO_DOMAIN') or ''  # 原KO_ARGO_DOMAIN
    TO_ARGO_AUTH = os.environ.get('TO_ARGO_AUTH') or ''      # 原KO_ARGO_AUTH
    ARGO_PORT = int(os.environ.get('TO_ARGO_PORT') or '8002')
    NAME = os.environ.get('TO_NAME') or 'ToModal'
    CFIP = os.environ.get('TO_CFIP') or 'to.visa.com.tw'
    CFPORT = int(os.environ.get('TO_CFPORT') or '443')
    SUB_PATH = os.environ.get('TO_SUB_PATH') or 'to-sub'
    
    # 启动核心服务
    config_json_path = "/root/.tmp_to/config.json"
    config_data = {
            "log": {
                "access": "/dev/null",
                "error": "/dev/null",
                "loglevel": "none"
            },
            "inbounds": [
                {
                    "port": ARGO_PORT,
                    "protocol": "vless",
                    "settings": {
                        "clients": [{"id": UUID}],
                        "decryption": "none",
                        "fallbacks": [
                            {"dest": 3011},
                            {"path": "/vless-to", "dest": 3012},
                            {"path": "/vmess-to", "dest": 3013},
                            {"path": "/trojan-to", "dest": 3014},
                        ]
                    },
                    "streamSettings": {"network": "tcp"}
                },
                {
                    "port": 3011,
                    "listen": "127.0.0.1",
                    "protocol": "vless",
                    "settings": {
                        "clients": [{"id": UUID}],
                        "decryption": "none"
                    },
                    "streamSettings": {
                        "network": "ws",
                        "security": "none"
                    }
                },
                {
                    "port": 3012,
                    "listen": "127.0.0.1",
                    "protocol": "vless",
                    "settings": {
                        "clients": [{"id": UUID, "level": 0}],
                        "decryption": "none"
                    },
                    "streamSettings": {
                        "network": "ws",
                        "security": "none",
                        "wsSettings": {"path": "/vless-to"}
                    }
                },
                {
                    "port": 3013,
                    "listen": "127.0.0.1",
                    "protocol": "vmess",
                    "settings": {
                        "clients": [{"id": UUID, "alterId": 0}]
                    },
                    "streamSettings": {
                        "network": "ws",
                        "wsSettings": {"path": "/vmess-to"}
                    }
                },
                {
                    "port": 3014,
                    "listen": "127.0.0.1",
                    "protocol": "trojan",
                    "settings": {
                        "clients": [{"password": UUID}]
                    },
                    "streamSettings": {
                        "network": "ws",
                        "security": "none",
                        "wsSettings": {"path": "/trojan-to"}
                    }
                }
            ],
            "outbounds": [
                {"protocol": "freedom", "tag": "direct"},
                {"protocol": "blackhole", "tag": "block"}
            ]
        }

    with open(config_json_path, 'w') as f: json.dump(config_data, f)
    subprocess.Popen(["/root/.tmp_to/web", "-c", config_json_path])
    print(f"✅ To实例 - Xr-ay 'web' 进程已启动。")

    domain_for_links = ""
    argo_log_path = "/root/.tmp_to/argo.log"
    # 核心修改：使用TO_ARGO_DOMAIN和TO_ARGO_AUTH
    if TO_ARGO_DOMAIN and TO_ARGO_AUTH:
        domain_for_links = TO_ARGO_DOMAIN
        if re.match(r'^[A-Z0-9a-z=]{120,250}$', TO_ARGO_AUTH):
            argo_args = f"tunnel --edge-ip-version auto --no-autoupdate run --token {TO_ARGO_AUTH}"
        elif "TunnelSecret" in TO_ARGO_AUTH:
            tunnel_json_path = "/root/.tmp_to/tunnel.json"; tunnel_yml_path = "/root/.tmp_to/tunnel.yml"
            with open(tunnel_json_path, 'w') as f: f.write(TO_ARGO_AUTH)
            tunnel_id = json.loads(TO_ARGO_AUTH)['TunnelID']
            tunnel_yml_content = f"""
tunnel: {tunnel_id}
credentials-file: {tunnel_json_path}
protocol: http2

ingress:
  - hostname: {TO_ARGO_DOMAIN}
    service: http://localhost:{ARGO_PORT}
    originRequest:
      noTLSVerify: true
  - service: http_status:404
"""
            with open(tunnel_yml_path, 'w') as f: f.write(tunnel_yml_content)
            argo_args = f"tunnel --edge-ip-version auto --config {tunnel_yml_path} run"
        else: raise ValueError("To实例 - TO_ARGO_AUTH格式无效")  # 提示信息同步修改
        subprocess.Popen(f"/root/.tmp_to/bot {argo_args}", shell=True)
        print(f"✅ To实例 - 固定隧道 ('bot') 进程已启动。")
    else:
        argo_args = f"tunnel --edge-ip-version auto --url http://localhost:{ARGO_PORT}"
        subprocess.Popen(f"/root/.tmp_to/bot {argo_args} > {argo_log_path} 2>&1", shell=True)
        time.sleep(10)
        try:
            with open(argo_log_path, 'r') as f: log_content = f.read()
            match = re.search(r"https?://\S+\.trycloudflare\.com", log_content)
            if match:
                domain_for_links = match.group(0).replace("https://", "").replace("http://", "")
                print(f"✅ To实例 - 临时隧道已建立: {domain_for_links}")
            else: raise RuntimeError("To实例 - 无法分析临时隧道URL。")
        except FileNotFoundError: raise RuntimeError(f"To实例 - Argo log 文件未找到。")
    
    # 生成节点链接和订阅
    links_str = generate_links(domain_for_links, NAME, UUID, CFIP, CFPORT)
    sub_content_b64 = base64.b64encode(links_str.encode('utf-8')).decode('utf-8')
    subscription_dict["content"] = sub_content_b64
    print("✅ To实例 - 订阅内容已生成并保存到共享字典。")

    # 生成项目URL
    PROJECT_URL = ""
    if MODAL_USER_NAME:
        modal_url_base = f"{MODAL_USER_NAME}--{MODAL_APP_NAME}-web_server.modal.run"
        PROJECT_URL = f"https://{modal_url_base}"
    
    print("\n" + "="*60)
    print("✅ To实例 - 所有后台服务都已运行。Web 服务已准备就绪。")
    if PROJECT_URL: print(f"  - 订阅文件下载地址: {PROJECT_URL}/{SUB_PATH}")
    print(f"  - 节点连接域名: {domain_for_links}")
    print("="*60 + "\n")
    
    yield

# --- 6. FastAPI Web 应用定义 ---
fastapi_app = FastAPI(lifespan=lifespan)

@app.function(
    secrets=[app_secrets],
    timeout=86400,
    keep_warm=1,
    region=DEPLOY_REGION,
    cpu=0.125,
    memory=128
)
@modal.asgi_app()
def web_server():
    SUB_PATH = os.environ.get('TO_SUB_PATH') or 'to-sub'

    @fastapi_app.get("/")
    def root():
        return Response(content="To实例服务运行中", media_type="text/html; charset=utf-8")

    @fastapi_app.get(f"/{SUB_PATH}")
    def get_subscription():
        try:
            content = subscription_dict.get("content")
            if content:
                return Response(content=content, media_type="text/plain")
            else:
                return Response(content="To实例订阅内容尚未生成，请稍后重试。", status_code=503, media_type="text/plain; charset=utf-8")
        except Exception as e:
            return Response(content=f"To实例读取订阅时发生错误: {e}", status_code=500, media_type="text/plain; charset=utf-8")
    
    return fastapi_app
