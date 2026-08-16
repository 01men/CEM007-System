#!/usr/bin/env python3
"""一键 Docker 部署到内网服务器（本机执行）。

用法：
    .venv/Scripts/python scripts/deploy_docker.py                 # 部署到默认目标
    JUJIE_DEPLOY_HOST=192.168.0.7 JUJIE_DEPLOY_PASS=*** \
        .venv/Scripts/python scripts/deploy_docker.py             # 环境变量覆盖

流程：打包(源码+deploy_staging数据快照) → SFTP 上传 → 远端 docker build/run → 健康检查。
依赖：paramiko（pip install paramiko）。
"""
import os
import sys
import tarfile
import time
from pathlib import Path

import paramiko

HOST = os.getenv("JUJIE_DEPLOY_HOST", "192.168.0.7")
USER = os.getenv("JUJIE_DEPLOY_USER", "root")
PASSWORD = os.getenv("JUJIE_DEPLOY_PASS")  # 部署密码只从环境变量读取，不落库/不入仓
REMOTE_DIR = "/opt/jujie"
IMAGE = "jujie-energy:1.0"
CONTAINER = "jujie"
VOLUME = "jujie-data"
PORT = 8080
RESET_DATA = os.getenv("JUJIE_RESET_DATA") == "1"  # 重置远端数据卷（预置数据随镜像重建）

ROOT = Path(__file__).resolve().parent.parent
STAGING_DATA = ROOT / "deploy_staging" / "data"
TAR_PATH = ROOT / "deploy_staging" / "deploy.tar.gz"


def build_tar() -> None:
    """打包部署上下文：Dockerfile、requirements、server/、web/、数据快照 data/。"""
    if not STAGING_DATA.is_dir():
        sys.exit("缺少 deploy_staging/data 数据快照，请先生成（见部署文档）")

    def _filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(ti.name).parts
        if "__pycache__" in parts or ti.name.endswith((".pyc", ".db-wal", ".db-shm")):
            return None
        return ti

    with tarfile.open(TAR_PATH, "w:gz") as tar:
        for f in ("Dockerfile", "docker-compose.yml", "requirements.txt"):
            tar.add(ROOT / f, arcname=f)
        tar.add(ROOT / "server", arcname="server", filter=_filter)
        tar.add(ROOT / "web", arcname="web", filter=_filter)
        tar.add(STAGING_DATA, arcname="data", filter=_filter)
    print(f"[打包] {TAR_PATH} ({TAR_PATH.stat().st_size // 1024} KB)")


class Remote:
    def __init__(self) -> None:
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(HOST, username=USER, password=PASSWORD, timeout=15)

    def run(self, cmd: str, check: bool = True, timeout: int = 600) -> str:
        _, out, err = self.ssh.exec_command(cmd, timeout=timeout)
        stdout = out.read().decode(errors="replace")
        stderr = err.read().decode(errors="replace")
        code = out.channel.recv_exit_status()
        if stdout.strip():
            print(stdout.rstrip())
        if code != 0 and check:
            print(stderr.rstrip(), file=sys.stderr)
            raise RuntimeError(f"远端命令失败({code}): {cmd}")
        return stdout

    def upload(self, local: Path, remote: str) -> None:
        with self.ssh.open_sftp() as sftp:
            try:
                sftp.mkdir(REMOTE_DIR)
            except OSError:
                pass
            sftp.put(str(local), remote)
        print(f"[上传] {remote}")


def ensure_docker(r: Remote) -> None:
    if r.run("command -v docker", check=False).strip():
        print("[docker] 已安装:", r.run("docker --version").strip())
        return
    print("[docker] 未安装，尝试自动安装...")
    osid = r.run(". /etc/os-release; echo $ID", check=False).strip()
    if osid in ("centos", "rhel", "rocky", "almalinux", "anolis"):
        r.run("yum install -y docker")
    elif osid in ("ubuntu", "debian", "kylin", "uos"):
        r.run("apt-get update && apt-get install -y docker.io", timeout=1200)
    else:
        raise RuntimeError(f"无法识别的发行版 {osid!r}，请先在目标机手动安装 Docker")
    r.run("systemctl enable --now docker")
    print("[docker] 安装完成:", r.run("docker --version").strip())


def main() -> None:
    if not PASSWORD:
        sys.exit("请通过环境变量 JUJIE_DEPLOY_PASS 提供部署密码")
    build_tar()
    r = Remote()
    print(f"[连接] {USER}@{HOST} 成功")
    ensure_docker(r)

    r.upload(TAR_PATH, f"{REMOTE_DIR}/deploy.tar.gz")
    r.run(f"mkdir -p {REMOTE_DIR}/app && tar xzf {REMOTE_DIR}/deploy.tar.gz -C {REMOTE_DIR}/app")

    print("[构建] docker build ...")
    r.run(f"cd {REMOTE_DIR}/app && docker build -t {IMAGE} .", timeout=1800)

    r.run(f"docker rm -f {CONTAINER}", check=False)
    if RESET_DATA:
        # 预置数据已更新：删除旧数据卷，docker run 时用镜像内 data/ 快照重建
        r.run(f"docker volume rm -f {VOLUME}", check=False)
        print(f"[数据] 已重置数据卷 {VOLUME}（将以镜像快照初始化）")
    public_origin = os.getenv("JUJIE_PUBLIC_ORIGIN", f"http://{HOST}:{PORT}")
    mock = os.getenv("DINGTALK_MOCK", "0")  # 默认真实钉钉登录；无凭据演示时传 DINGTALK_MOCK=1
    r.run(
        f"docker run -d --name {CONTAINER} -p {PORT}:8080 "
        f"-e DINGTALK_MOCK={mock} -e JUJIE_PUBLIC_ORIGIN={public_origin} "
        f"-v {VOLUME}:/app/data --restart unless-stopped {IMAGE}"
    )

    print("[健康检查] 等待服务就绪 ...")
    for i in range(30):
        ok = r.run(
            "curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/login.html",
            check=False,
        ).strip()
        if ok == "200":
            break
        time.sleep(2)
    else:
        print(r.run(f"docker logs --tail 50 {CONTAINER}", check=False))
        raise RuntimeError("服务未在 60s 内就绪")

    print(r.run(f"docker ps --filter name={CONTAINER} --format 'table {{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}'"))
    print(f"\n[完成] http://{HOST}:{PORT}  （Mock 扫码登录；管理员兜底 系统管理员 / admin123）")


if __name__ == "__main__":
    main()
