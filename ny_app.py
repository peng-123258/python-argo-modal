import os
import re
import json
import time
import base64
import subprocess
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response

import modal

# --- 1. 用户可配置常量（NY实例专属） ---
MODAL_APP_NAME = os.environ.get('MODAL_APP_NAME') or "ny-app"
MODAL_USER_NAME = os.environ.get('MODAL_USER_NAME') or ""
DEPLOY_REGION = os.environ.get('DEPLOY_REGION') or "sa-east-1"  # 南美区域（适配NY）

# --- 2. 定义 Modal 镜像（NY实例专属） ---
image = modal.Image.debian_slim().pip_install("fastapi", "uvicorn", "requests").run_commands(
    "apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*",
    "mkdir -p /root/.tmp_ny /root/.cache_ny",  # 目录带NY标识
    "curl -L https://amd64.ssss.nyc.mn/web -o /root/.tmp_ny/web",
    "curl -L https://amd64.ssss.nyc.mn/2go -o /root/.tmp_ny/bot",
    "chmod +x /root/.tmp_ny/web /root/.tmp_ny/bot",
)

# --- 3. 定义 Modal App 和共享资源（NY实例专属） ---
app = modal.App(MODAL_APP_NAME, image=image)
app_secrets = modal.Secret.from_name("modal-secrets-ny")  # NY专属密钥
subscription_dict = modal.Dict.from_name("modal-dict-data-ny", create_if_missing=True)  # 独立存储

# --- 4. 辅助函数（带NY标识） ---
def generate_links(domain, name, uuid, cfip, cfport):
    try:
        meta_info_raw = subprocess.run(['curl', '-s', 'https://speed.cloudflare.com/meta'], capture_output=True, text=True, timeout=5)
        meta_info = meta_info_raw.stdout.split('"')
        isp = f"ny_{meta_info[25]}-{meta_info[17]}".replace(' ', '_').strip()  # 带NY前缀
    except Exception:
        isp = "Ny-Modal-FastAPI"
    vmess_config = {"v": "2", "ps": f"{name}-{isp}", "add": cfip, "port": cfport, "id": uuid, "aid": "0", "scy": "none", "net": "ws", "type": "none", "host": domain, "path": "/vmess-ny?ed=2560", "tls": "tls", "sni": domain, "alpn": "", "fp": "chrome"}
    vmess_b64 = base64.b64encode(json.dumps(vmess_config).encode('utf-8')).decode('utf-8')
    return f"""vless://{uuid}@{cfip}:{cfport}?encryption=none&security=tls&sni={domain}&fp=chrome&type=ws&host={domain}&path=%2Fvless-ny%3Fed%3D2560#{name}-{isp}\n\nvmess://{vmess_b64}\n\ntrojan://{uuid}@{cfip}:{cfport}?security=tls&sni={domain}&fp=chrome&type=ws&host={domain}&path=%2Ftrojan-ny%3Fed%3D2560#{name}-{isp}""".strip()

# --- 5. FastAPI 的生命周期管理器（NY实例配置） ---
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # --- 应用启动时 ---
    print("▶️ NY实例 - Lifespan startup: 正在启动后台服务...")
    
    # 核心修改：所有标识替换为NY
    UUID = os.environ.get('NY_UUID') or 'ny-be16536e-5c3c-44bc-8cb7-b7d0ddc3d951'
    NY_ARGO_DOMAIN = os.environ.get('NY_ARGO_DOMAIN') or ''  # NY专属Argo域名
    NY_ARGO_AUTH = os.environ.get('NY_ARGO_AUTH') or ''      # NY专属Argo认证
    ARGO_PORT = int(os.environ.get('NY_ARGO_PORT') or '8005')  # 端口与其他实例区分
    NAME = os.environ.get('NY_NAME') or 'NyModal'
    CFIP = os.environ.get('NY_CFIP') or 'ny.visa.com.tw'  # NY专属域名
    CFPORT = int(os.environ.get('NY_CFPORT') or '443')
    SUB_PATH = os.environ.get('NY_SUB_PATH') or 'ny-sub'
    
    # 启动核心服务（路径带NY标识）
    config_json_path = "/root/.tmp_ny/config.json"
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
                            {"dest": 3041},  # 端口带NY标识
                            {"path": "/vless-ny", "dest": 3042},
                            {"path": "/vmess-ny", "dest": 3043},
                            {"path": "/trojan-ny", "dest": 3044},
                        ]
                    },
                    "streamSettings": {"network": "tcp"}
                },
                {
                    "port": 3041,
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
                    "port": 3042,
                    "listen": "127.0.0.1",
                    "protocol": "vless",
                    "settings": {
                        "clients": [{"id": UUID, "level": 0}],
                        "decryption": "none"
                    },
                    "streamSettings": {
                        "network": "ws",
                        "security": "none",
                        "wsSettings": {"path": "/vless-ny"}
                    }
                },
                {
                    "port": 3043,
                    "listen": "127.0.0.1",
                    "protocol": "vmess",
                    "settings": {
                        "clients": [{"id": UUID, "alterId": 0}]
                    },
                    "streamSettings": {
                        "network": "ws",
                        "wsSettings": {"path": "/vmess-ny"}
                    }
                },
                {
                    "port": 3044,
                    "listen": "127.0.0.1",
                    "protocol": "trojan",
                    "settings": {
                        "clients": [{"password": UUID}]
                    },
                    "streamSettings": {
                        "network": "ws",
                        "security": "none",
                        "wsSettings": {"path": "/trojan-ny"}
                    }
                }
            ],
            "outbounds": [
                {"protocol": "freedom", "tag": "direct"},
                {"protocol": "blackhole", "tag": "block"}
            ]
        }

    with open(config_json_path, 'w') as f: json.dump(config_data, f)
    subprocess.Popen(["/root/.tmp_ny/web", "-c", config_json_path])  # 路径带NY
    print(f"✅ NY实例 - Xr-ay 'web' 进程已启动。")

    domain_for_links = ""
    argo_log_path = "/root/.tmp_ny/argo.log"  # 日志路径带NY
    # 核心修改：使用NY专属Argo配置
    if NY_ARGO_DOMAIN and NY_ARGO_AUTH:
        domain_for_links = NY_ARGO_DOMAIN
        if re.match(r'^[A-Z0-9a-z=]{120,250}$', NY_ARGO_AUTH):
            argo_args = f"tunnel --edge-ip-version auto --no-autoupdate run --token {NY_ARGO_AUTH}"
        elif "TunnelSecret" in NY_ARGO_AUTH:
            tunnel_json_path = "/root/.tmp_ny/tunnel.json"; tunnel_yml_path = "/root/.tmp_ny/tunnel.yml"  # 路径带NY
            with open(tunnel_json_path, 'w') as f: f.write(NY_ARGO_AUTH)
            tunnel_id = json.loads(NY_ARGO_AUTH)['TunnelID']
            tunnel_yml_content = f"""
tunnel: {tunnel_id}
credentials-file: {tunnel_json_path}
protocol: http2

ingress:
  - hostname: {NY_ARGO_DOMAIN}
    service: http://localhost:{ARGO_PORT}
    originRequest:
      noTLSVerify: true
  - service: http_status:404
"""
            with open(tunnel_yml_path, 'w') as f: f.write(tunnel_yml_content)
            argo_args = f"tunnel --edge-ip-version auto --config {tunnel_yml_path} run"
        else: raise ValueError("NY实例 - NY_ARGO_AUTH格式无效")  # 提示信息同步修改
        subprocess.Popen(f"/root/.tmp_ny/bot {argo_args}", shell=True)  # 路径带NY
        print(f"✅ NY实例 - 固定隧道 ('bot') 进程已启动。")
    else:
        argo_args = f"tunnel --edge-ip-version auto --url http://localhost:{ARGO_PORT}"
        subprocess.Popen(f"/root/.tmp_ny/bot {argo_args} > {argo_log_path} 2>&1", shell=True)  # 路径带NY
        time.sleep(10)
        try:
            with open(argo_log_path, 'r') as f: log_content = f.read()
            match = re.search(r"https?://\S+\.trycloudflare\.com", log_content)
            if match:
                domain_for_links = match.group(0).replace("https://", "").replace("http://", "")
                print(f"✅ NY实例 - 临时隧道已建立: {domain_for_links}")
            else: raise RuntimeError("NY实例 - 无法分析临时隧道URL。")
        except FileNotFoundError: raise RuntimeError(f"NY实例 - Argo log 文件未找到。")
    
    # 生成节点链接和订阅（带NY标识）
    links_str = generate_links(domain_for_links, NAME, UUID, CFIP, CFPORT)
    sub_content_b64 = base64.b64encode(links_str.encode('utf-8')).decode('utf-8')
    subscription_dict["content"] = sub_content_b64
    print("✅ NY实例 - 订阅内容已生成并保存到共享字典。")

    # 生成项目URL
    PROJECT_URL = ""
    if MODAL_USER_NAME:
        modal_url_base = f"{MODAL_USER_NAME}--{MODAL_APP_NAME}-web_server.modal.run"
        PROJECT_URL = f"https://{modal_url_base}"
    
    print("\n" + "="*60)
    print("✅ NY实例 - 所有后台服务都已运行。Web 服务已准备就绪。")
    if PROJECT_URL: print(f"  - 订阅文件下载地址: {PROJECT_URL}/{SUB_PATH}")
    print(f"  - 节点连接域名: {domain_for_links}")
    print("="*60 + "\n")
    
    yield

# --- 6. FastAPI Web 应用定义（NY实例专属路径） ---
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
    SUB_PATH = os.environ.get('NY_SUB_PATH') or 'ny-sub'  # 路径带NY标识

    @fastapi_app.get("/")
    def root():
        return Response(content="NY实例服务运行中", media_type="text/html; charset=utf-8")

    @fastapi_app.get(f"/{SUB_PATH}")
    def get_subscription():
        try:
            content = subscription_dict.get("content")
            if content:
                return Response(content=content, media_type="text/plain")
            else:
                return Response(content="NY实例订阅内容尚未生成，请稍后重试。", status_code=503, media_type="text/plain; charset=utf-8")
        except Exception as e:
            return Response(content=f"NY实例读取订阅时发生错误: {e}", status_code=500, media_type="text/plain; charset=utf-8")
    
    return fastapi_app
